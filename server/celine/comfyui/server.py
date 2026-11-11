"""A ComfyUI server: the few endpoints the app uses, one prompt run to its end, the
presets it can run (its "models" in Settings), the startup free, and the lease on the GPU
of a ComfyUI that starts on demand.

A provider with Starts on demand (`on_demand`) has the comfy helper as its address, a
host process that starts ComfyUI for a batch and stops it when no batch needs it. A
batch takes a lease from it before its first item (`lease`) and holds it to its end; the
helper alone decides when the GPU is free. Its contract: POST /leases {holder, kind}
gives a lease {id, state, why, ahead}; GET /leases/<id> renews it (404: unknown or
expired); DELETE /leases/<id> releases it. `state` is waiting (the GPU is in use; `why`
says so, `ahead` counts the leases asked for earlier that also wait), starting (ComfyUI
is coming up), ready (ComfyUI answers) or failed (ComfyUI cannot be started; `why` says
what happened). A lease lives 90 s past its last GET. Everything else is ComfyUI's own
API, passed through while ComfyUI is up; while it is down the helper answers
/object_info and the model listings from its last start, anything else with 503."""
from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import anyio
import anyio.abc
import httpx

from ..errors import answered, said
from ..settings.registry import provider_headers
from . import presets as presets_mod
from .presets import ImageError

log = logging.getLogger("celine.comfyui")

POLL = 1.0                  # seconds between /history looks
REQUEST = 30.0              # seconds one request to ComfyUI may take
PROBE = 2.5                 # seconds the Settings check waits for ComfyUI
CLEANUP_SECONDS = 5.0       # the most the interrupt and /free may take after a Stop; a
                            # restart frees what was left (free_shared)
LEASE_POLL = 2.0            # seconds between looks at a lease that waits or starts
LEASE_RENEW = 20.0          # seconds between renewals of a lease that is held


class Comfy:
    """The few ComfyUI endpoints the app uses."""

    def __init__(self, base_url: str, headers: dict[str, str] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=REQUEST, transport=transport,
                                        headers=headers or {})

    async def close(self) -> None:
        await self.client.aclose()

    async def object_info(self, classes: set[str]) -> dict[str, Any]:
        try:
            return await object_info(self.client, self.url, classes)
        except httpx.HTTPStatusError as e:
            raise ImageError(str(e)) from None
        except ValueError:
            raise ImageError(f"ComfyUI at {_host(self.url)} answered /object_info with "
                             f"something that is not JSON: is {self.url} a ComfyUI "
                             f"server?") from None

    async def queue(self, graph: dict[str, Any], client_id: str) -> str:
        r = await self.client.post(f"{self.url}/prompt",
                                   json={"prompt": graph, "client_id": client_id})
        if r.status_code == 400:
            raise ImageError(_refused(r))
        return str(_ok(r).json()["prompt_id"])

    async def upload(self, data: bytes, name: str, media_type: str) -> str:
        """The source of an edit, into ComfyUI's input folder. Returns the name its
        LoadImage node takes."""
        r = await self.client.post(f"{self.url}/upload/image",
                                   files={"image": (name, data, media_type)},
                                   data={"type": "input", "overwrite": "true"})
        out = _ok(r).json() or {}
        return f"{out['subfolder']}/{out['name']}" if out.get("subfolder") else out["name"]

    async def history(self, prompt_id: str) -> dict[str, Any] | None:
        r = _ok(await self.client.get(f"{self.url}/history/{prompt_id}"))
        return (r.json() or {}).get(prompt_id)

    async def position(self, prompt_id: str) -> tuple[bool, int]:
        """(running now, prompts ahead of it in ComfyUI's own queue)."""
        q = _ok(await self.client.get(f"{self.url}/queue")).json() or {}
        if any(item[1] == prompt_id for item in q.get("queue_running") or []):
            return True, 0
        pending = sorted(q.get("queue_pending") or [], key=lambda item: item[0])
        ids = [item[1] for item in pending]
        return False, ids.index(prompt_id) if prompt_id in ids else 0

    async def output(self, ref: dict[str, Any]) -> tuple[bytes, str]:
        r = await self.client.get(f"{self.url}/view", params={
            "filename": ref["filename"], "subfolder": ref.get("subfolder", ""),
            "type": ref.get("type", "output")})
        _ok(r)
        return r.content, r.headers.get("content-type", "image/png").split(";")[0]

    async def stop(self, prompt_id: str) -> None:
        """Interrupts the prompt if it runs (only this one) and takes it out of the
        queue if it still waits there."""
        await self.client.post(f"{self.url}/interrupt", json={"prompt_id": prompt_id})
        await self.client.post(f"{self.url}/queue", json={"delete": [prompt_id]})

    async def free(self) -> None:
        await self.client.post(f"{self.url}/free",
                               json={"unload_models": True, "free_memory": True})

    # ---- the comfy helper's leases (the module docstring) ----------------------------

    async def take_lease(self, holder: str, kind: str) -> dict[str, Any]:
        r = await self.client.post(f"{self.url}/leases", json={"holder": holder, "kind": kind})
        if r.status_code in (404, 405):
            raise ImageError(f"ComfyUI at {_host(self.url)} takes no leases ({r.status_code}): "
                             f"with Starts on demand on, its address is the comfy helper's.")
        return self._lease(r)

    async def renew_lease(self, lid: str) -> dict[str, Any] | None:
        """The lease as the helper holds it now, renewed; None when the helper does not
        know it (it restarted, or the lease expired)."""
        r = await self.client.get(f"{self.url}/leases/{lid}")
        return None if r.status_code == 404 else self._lease(r)

    async def release_lease(self, lid: str) -> None:
        _ok(await self.client.delete(f"{self.url}/leases/{lid}"))

    def _lease(self, r: httpx.Response) -> dict[str, Any]:
        _ok(r)
        try:
            data = r.json()
        except ValueError:
            data = None
        if not isinstance(data, dict) or not isinstance(data.get("id"), str) or \
                data.get("state") not in LEASE_STATES:
            raise ImageError(f"ComfyUI at {_host(self.url)} answered {r.url.path} with "
                             f"something that is not a lease: is {self.url} the comfy "
                             f"helper?")
        return data


LEASE_STATES = ("waiting", "starting", "ready", "failed")


@dataclass
class Lease:
    """A lease from the comfy helper as its batch holds it. `lost`: the helper no longer
    holds it ready (it restarted, the lease expired, ComfyUI stopped), so the batch's
    next item asks for a new one."""
    id: str | None = None
    lost: bool = False


@contextlib.asynccontextmanager
async def lease(comfy: Comfy, holder: str, kind: str,
                on_wait: Callable[[dict[str, Any]], Awaitable[None]],
                tg: anyio.abc.TaskGroup) -> AsyncIterator[Lease]:
    """The GPU of a ComfyUI that starts on demand, held (the module docstring): a lease
    asked for, looked at every LEASE_POLL seconds until it is ready, with no limit of its
    own (a Stop ends the wait), then renewed every LEASE_RENEW seconds in `tg` while
    held. `on_wait(lease)` hears every look while it waits or starts. A lease the helper
    does not know while it waits (it restarted) is asked for again. `failed` raises
    ImageError with the helper's sentence; a helper that does not answer raises httpx's
    error. On exit, also after a Stop or an error, the renewal is cancelled and waited
    for, then the lease is released (shielded, CLEANUP_SECONDS at most each), so no
    renewal outlives the lease or its client."""
    held = Lease()
    renewal = anyio.CancelScope()
    renewing: anyio.Event | None = None     # set when the renewal has ended
    try:
        got: dict[str, Any] | None = None
        while True:
            if got is None:
                got = await comfy.take_lease(holder, kind)
            lid: str = got["id"]
            held.id = lid
            if got["state"] == "ready":
                break
            if got["state"] == "failed":
                raise ImageError(got.get("why") or "ComfyUI could not be started.")
            await on_wait(got)
            await anyio.sleep(LEASE_POLL)
            got = await comfy.renew_lease(lid)
        renewing = anyio.Event()
        tg.start_soon(_renew, comfy, held, lid, renewal, renewing)
        yield held
    finally:
        renewal.cancel()
        if renewing is not None:
            with anyio.CancelScope(shield=True), anyio.move_on_after(CLEANUP_SECONDS):
                await renewing.wait()
        if held.id is not None:
            await _release(comfy, held.id)


def waiting_note(lease: dict[str, Any]) -> str:
    """The note of an item whose batch waits for its lease: "Waiting for the GPU", the
    leases ahead, and the helper's sentence."""
    ahead = lease.get("ahead") or 0
    head = f"Waiting for the GPU, {ahead} ahead" if ahead else "Waiting for the GPU"
    why = _plain(lease.get("why"))
    return f"{head}. {why}" if why else head


async def _renew(comfy: Comfy, held: Lease, lid: str, scope: anyio.CancelScope,
                 ended: anyio.Event) -> None:
    """Renews lease `lid` every LEASE_RENEW seconds until `scope` is cancelled (the
    lease's exit), then sets `ended`. An answer that the helper does not hold it ready
    marks it lost and ends the renewal; a renewal that fails is tried again at the next
    one (a lease lives 90 s past its last one) and never fails the batch."""
    try:
        with scope:
            while True:
                await anyio.sleep(LEASE_RENEW)
                try:
                    got = await comfy.renew_lease(lid)
                except Exception as e:
                    log.info("lease not renewed", extra={
                        "event": "comfyui.lease_not_renewed",
                        "error": str(e) or type(e).__name__})
                    continue
                if got is None or got["state"] != "ready":
                    held.lost = True
                    log.info("lease lost", extra={"event": "comfyui.lease_lost",
                                                  "state": got and got["state"]})
                    return
    finally:
        ended.set()


async def _release(comfy: Comfy, lid: str) -> None:
    with anyio.CancelScope(shield=True), anyio.move_on_after(CLEANUP_SECONDS):
        try:
            await comfy.release_lease(lid)
        except (httpx.HTTPError, ImageError) as e:
            log.info("lease not released", extra={"event": "comfyui.lease_not_released",
                                                  "error": str(e) or type(e).__name__})


async def object_info(c: httpx.AsyncClient, url: str, classes: set[str],
                      headers: dict[str, str] | None = None) -> dict[str, Any]:
    """{class: its /object_info entry, or None when the server has no such node}.
    ValueError: an answer that is not JSON; httpx.HTTPStatusError with ComfyUI's words:
    an error answer (an HTTP error still, for the Settings check's key and credit
    sentences, settings/kinds.py)."""
    info: dict[str, Any] = {}
    for cls in sorted(classes):
        r = await c.get(f"{url.rstrip('/')}/object_info/{cls}", headers=headers)
        if r.is_error:
            raise httpx.HTTPStatusError(_who(r), request=r.request, response=r)
        info[cls] = (r.json() or {}).get(cls)
    return info


def _host(url: str) -> str:
    from urllib.parse import urlsplit
    return urlsplit(url).netloc or url


def _who(r: httpx.Response) -> str:
    return answered(f"ComfyUI at {_host(str(r.url))}", r)


def _ok(r: httpx.Response) -> httpx.Response:
    """`r`, or ImageError with ComfyUI's words when it is an error answer."""
    if r.is_error:
        raise ImageError(_who(r))
    return r


def _refused(r: httpx.Response) -> str:
    """ComfyUI's 400 for a workflow it will not run, in one or two sentences."""
    out = f"ComfyUI refused the workflow: {_plain(said(r.content, r.status_code).text)}."
    try:
        data = r.json()
    except ValueError:
        return out
    nodes = data.get("node_errors") if isinstance(data, dict) else None
    for node in (nodes if isinstance(nodes, dict) else {}).values():
        for e in node.get("errors") or []:
            detail = _plain(e.get("details") or e.get("message"))
            if detail:
                return f"{out} {node.get('class_type', 'A node')}: {detail[:200]}."
    return out


def _failed(status: dict[str, Any]) -> str:
    for kind, data in status.get("messages") or []:
        if kind == "execution_error":
            why = _plain(data.get("exception_message"))[:300]
            return f"ComfyUI failed in {data.get('node_type', 'a node')}: {why}."
        if kind == "execution_interrupted":
            return "ComfyUI stopped the prompt before it was done."
    return "ComfyUI ended the prompt with an error."


def _plain(text: Any) -> str:
    """One line without a closing full stop, for a sentence of our own."""
    return " ".join(str(text or "").split()).rstrip(".")


def _output_ref(outputs: dict[str, Any]) -> dict[str, Any] | None:
    for out in outputs.values():
        for item in (out.get("images") or []) + (out.get("audio") or []):
            if isinstance(item, dict) and item.get("filename"):
                return item
    return None


async def discover(c: httpx.AsyncClient, url: str, headers: dict[str, str]) -> list[dict]:
    """Every preset, images and music, as a ComfyUI provider's models: [{id, name,
    caps, missing?}]. A preset whose node or file the server lacks says why."""
    found = [p for kind in presets_mod.FOLDERS for p in presets_mod.of(kind).values()]
    info = await object_info(c, url, {n["class_type"] for p in found
                                      for n in p.workflow.values()}, headers)
    out = []
    for p in found:
        entry: dict[str, Any] = {"id": p.id, "name": p.label, "caps": [p.role]}
        why = p.missing(info)
        if why:
            entry["missing"] = why
        out.append(entry)
    return out


async def free_shared(cfg, llm) -> int:
    """At startup: every ComfyUI provider that shares its GPU frees its models. A
    hard stop mid-batch (a kill, a power cut) left them loaded, and nothing else
    frees them until the next batch. Returns how many answered."""
    router = llm.router
    s = router.s
    n = 0
    for pid, p in (s.providers if s is not None else {}).items():
        shared = (p.get("options") or {}).get("shares_gpu_with")
        if p.get("kind") != "comfyui" or not shared:
            continue
        comfy = Comfy(p["base_url"], provider_headers(s, pid), router.transport)
        try:
            with anyio.fail_after(CLEANUP_SECONDS):
                await comfy.free()
            n += 1
        except Exception as e:
            log.info("comfyui not freed at startup", extra={
                "event": "comfyui.free_failed", "provider": pid, "error": type(e).__name__})
        finally:
            await comfy.close()
    if n:
        log.info("comfyui freed at startup", extra={"event": "comfyui.freed", "count": n})
    return n


async def run(comfy: Comfy, graph: dict[str, Any], note, timeout: float) -> tuple[bytes, str]:
    """Queue one prompt, follow it, fetch what it made. A prompt that did not finish
    (a Stop, an error, the timeout) is interrupted and taken out of ComfyUI's queue
    before this returns or raises. Freeing ComfyUI's memory is the batch's, at its end."""
    prompt_id: str | None = None
    finished = False
    try:
        prompt_id = await comfy.queue(graph, uuid4().hex)
        try:
            with anyio.fail_after(timeout):
                entry = await _follow(comfy, prompt_id, note)
        except TimeoutError:
            raise ImageError(f"ComfyUI did not finish within {timeout:.0f} s. The prompt "
                             f"was stopped.") from None
        finished = True
        status = entry.get("status") or {}
        if status.get("status_str") == "error":
            raise ImageError(_failed(status))
        ref = _output_ref(entry.get("outputs") or {})
        if ref is None:
            raise ImageError("The workflow finished without an output.")
        return await comfy.output(ref)
    finally:
        if prompt_id is not None and not finished:
            with anyio.CancelScope(shield=True), anyio.move_on_after(CLEANUP_SECONDS):
                try:
                    await comfy.stop(prompt_id)
                except httpx.HTTPError as e:
                    log.info("comfyui stop failed", extra={
                        "event": "comfyui.stop_failed", "error": type(e).__name__})


async def _follow(comfy: Comfy, prompt_id: str, note) -> dict[str, Any]:
    """Until the prompt is in /history as completed or failed. Its note says only what
    the user cannot see: a wait in ComfyUI's queue; nothing while it is being made (the
    picture block shows that)."""
    said = None
    while True:
        entry = await comfy.history(prompt_id)
        status = (entry or {}).get("status") or {}
        if entry is not None and (status.get("completed")
                                  or status.get("status_str") == "error"):
            return entry
        running, ahead = await comfy.position(prompt_id)
        line = ("" if running else
                f"Waiting in the queue, {ahead} ahead" if ahead else "Waiting in the queue")
        if line != said:
            said = line
            await note(line)
        await anyio.sleep(POLL)


async def _probe(cfg, role: str) -> dict[str, str]:
    """Settings, Features (images, music): a ComfyUI provider is saved and answers
    /object_info; the detail says when the role has no preset yet."""
    s = cfg.settings
    comfy = [(pid, p) for pid, p in s.providers.items() if p.get("kind") == "comfyui"]
    if not comfy:
        return {"state": "unset", "detail": "Type the ComfyUI address below."}
    pid, p = comfy[0]
    url = str(p.get("base_url", "")).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=PROBE, headers=provider_headers(s, pid)) as c:
            r = await c.get(f"{url}/object_info/CheckpointLoaderSimple")
    except httpx.HTTPError as e:
        from ..errors import agent_unreachable, unreachable_words
        return {"state": "missing", "detail": unreachable_words("ComfyUI", url, e),
                "why": agent_unreachable("ComfyUI", url)}
    if r.is_error:
        return {"state": "missing", "detail": _who(r), "why": _who(r)}
    from ..settings.registry import ROLES
    note = ("" if s.chain(role) else
            f" No preset is picked for {ROLES[role]['label']} yet (Settings, Images "
            f"& Music).")
    return {"state": "ready", "detail": f"ComfyUI at {_host(url)} answers.{note}"}


async def probe_images(cfg, services) -> dict[str, str]:
    return await _probe(cfg, "image_generation")


async def probe_music(cfg, services) -> dict[str, str]:
    return await _probe(cfg, "music_generation")
