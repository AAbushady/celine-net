"""Model files the agent finds and asks for: LoRAs and diffusion models from Civitai or
Hugging Face, downloaded by the comfy helper of a ComfyUI that starts on demand
(comfyui/server.py; its routes /models/search, /models/read, /models/picture,
/models/look, /models, /models/jobs/<id>, /models/files/<path>). The value that names one
file is comfyui/hubs.py's.

The agent looks around a hub (`search`, `read`, `pictures`: image_generator__search_models,
read_model, look_examples); what comes back is text and pictures from outside, marked for
the guard as web results are (guard/sources.py OUTSIDE_TOOLS, class public, nsfw where the
hub marks it mature). A request (`ModelFiles.request`, image_generator__request_model):

1. Checked at once, with no card on a refusal: the preset is an image preset that takes
   files of that kind (its catalog names a folder for them), its ComfyUI starts on demand,
   the value names a file, the helper's look finds it and takes it, Civitai's type fits the
   kind (a LoRA, LoCon or DoRA where LoRAs go, a checkpoint where models go), and nothing is
   there or asked for already under its name.
2. The file goes under the preset's first catalog folder for its kind (`place`):
   loras/<prefix><name> or diffusion_models/<prefix><name>, its name made plain. The id
   the agent then gives in `loras` or `model` is that place without its folder and
   extension, as image_catalog lists it.
3. The request is stored (model_requests, state asked) and asked of the user (approvals,
   kind model_file) from this service's own task group: the tool answers once the card is
   placed and never waits for the answer, so no turn is held. Several such cards wait
   beside each other and beside a request inside a turn.
4. Approved: POST /models with the look's resolved source and sha256 and the request's id
   as `holder`, then the job is followed every FOLLOW seconds in the same task group
   (fetching, listing, ready, failed). Each change is stored and shown: the call's row
   (its display and words, `model_file.state` on its conversation) and the page
   (`model_files.changed` on the global feed). Ready: the catalog is read anew and the
   conversation gets one notice from the app with the id to use and the trigger words the
   hub lists. Failed: one notice with the helper's sentence, or the app's own when the job
   carries one of the Civitai key's codes (key_words). Denied or not answered: one
   notice with the approvals' sentence. A helper that does not answer (a restart) is
   looked at again at the next round: it keeps its jobs.
5. The follower runs while Images is on (part.py), like the batches, and outside any
   turn or job: a download takes hours and holds nothing of the agent's. On every start
   (`resume`) a request still downloading is followed again (the helper kept downloading
   meanwhile), one approved and not yet handed to the helper is handed again, and one
   whose card the restart closed is settled as not answered, with its notice.

The page (GET /api/model-files, DELETE /api/model-files/{path}: `listing`, `remove`) shows
every file that came by request, from the helper's own list, with what the app stored of
its request, and the requests still on their way. Its Civitai key field (GET, PUT, DELETE
/api/civitai-key: `civitai_key`) hands the key to the helper, which keeps it."""
from __future__ import annotations

import contextlib
import json
import logging
import re
import sqlite3
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

import anyio
import anyio.abc
import httpx

from ..core.event_types import MODEL_FILE_STATE, MODEL_FILES_CHANGED
from ..core.ids import iso, new_id, now
from ..core.statuses import MODEL_FILE_SETTLED, KeyState, ModelFileState
from ..settings.registry import provider_headers
from . import hubs, presets as presets_mod, server as server_mod
from .presets import ImageError
from .server import HelperDown, HelperRefused

log = logging.getLogger("celine.comfyui")

S = ModelFileState
ASK_FOR = 24 * 3600.0       # seconds the user's card waits for an answer
FOLLOW = 5.0                # seconds between looks at a download
FOLDERS = {"lora": "loras/", "model": "diffusion_models/"}
WHAT = {"lora": "LoRA", "model": "model"}
FIELD = {"lora": "loras", "model": "model"}      # the generate field that takes its id
LORA_TYPES = {"lora", "locon", "dora"}           # Civitai's types a LoRA loader takes
MODEL_TYPES = {"checkpoint"}
KEY_SAID = {                # the helper's refusals about the Civitai key, by their code
    "civitai_key_missing": "This file needs a Civitai key, which {user} sets in Settings, "
                           "Media.",
    "civitai_key_refused": "Civitai refused the key set in Settings, Media for this "
                           "file."}
EDGE = 1024                # the longest side of an example picture asked for
LOOK_MAX = 4                # pictures one look shows at most
NOBODY = "Nothing was asked: nobody is here to approve it."
GONE = "The comfy helper no longer knows this download."
RESTARTED = "{user} did not answer before a restart closed the request; nothing was done."
OPEN = (S.ASKED, S.FETCHING, S.LISTING)


# ---- the provider and its helper ------------------------------------------------------

def helper_provider(app, p: presets_mod.Preset | None = None) -> str:
    """The id of the ComfyUI provider whose comfy helper deals in model files: preset
    `p`'s (its role's, else the first ComfyUI that lists it, else the first ComfyUI), else
    the first that starts on demand. ImageError with the sentence why there is none."""
    s = app.cfg.settings
    comfy = {pid: x for pid, x in (s.providers if s is not None else {}).items()
             if x.get("kind") == "comfyui"}
    if not comfy:
        raise ImageError("No ComfyUI is set: model files come only through the comfy helper "
                         "of a ComfyUI that starts on demand.")
    if p is not None:
        chain = [s.split(r) for r in s.chain(presets_mod.ROLES[p.kind])]
        pid = next((x for x, name in chain if name == p.id and x in comfy), None) or \
            next((x for x, prov in comfy.items() if p.id in (prov.get("models") or {})),
                 None) or next(iter(comfy))
    else:
        pid = next((x for x, prov in comfy.items()
                    if (prov.get("options") or {}).get("on_demand")), next(iter(comfy)))
    prov = comfy[pid]
    if not (prov.get("options") or {}).get("on_demand"):
        raise ImageError(f"{prov.get('label') or pid} takes no model files from the app: only "
                         f"a ComfyUI that starts on demand, behind the comfy helper, does.")
    return pid


@contextlib.asynccontextmanager
async def client(app, pid: str) -> AsyncIterator[server_mod.Comfy]:
    """A client of provider `pid` with its key, closed after."""
    s = app.cfg.settings
    p = s.providers.get(pid) if s is not None else None
    if p is None:
        raise ImageError(f"The provider {pid} is gone from Settings.")
    comfy = server_mod.Comfy(p["base_url"], provider_headers(s, pid),
                             app.llm.router.transport)
    try:
        yield comfy
    finally:
        await comfy.close()


def unreachable(app, pid: str, e: httpx.HTTPError) -> str:
    """The sentence for a helper that did not answer."""
    if isinstance(e, (httpx.ConnectError, httpx.TimeoutException)):
        return app.llm.router._unreachable_sentence(pid, e)
    return f"The comfy helper stopped answering ({type(e).__name__})."


def key_words(code: Any, user: str) -> str | None:
    """The app's own sentence for the helper's two refusals about the Civitai key, by their
    code (an answer's, a failed job's), which says where the key is set; None for any
    other."""
    said = KEY_SAID.get(str(code))
    return said.format(user=user) if said else None


def refused(e: HelperRefused, user: str) -> str:
    """The helper's refusal as the agent reads it: its sentence as it is, or the app's own
    for the two about the Civitai key (key_words)."""
    return key_words(e.code, user) or str(e)


# ---- where a file goes ----------------------------------------------------------------

def place(p: presets_mod.Preset, kind: str, name: str) -> str:
    """The file's place under the models folder: the preset's first catalog folder for
    `kind` and the file's name made plain (letters, digits, dot, dash, underscore).
    ImageError when the preset's folder is no plain path."""
    prefix = (p.loras if kind == "lora" else p.models)[0]
    if not re.fullmatch(r"[A-Za-z0-9._/-]*", prefix) or ".." in prefix or \
            prefix.startswith("/"):
        raise ImageError(f"{p.label} names its {WHAT[kind]}s by {prefix!r}, which is no "
                         f"plain folder, so no file can go there.")
    stem = name[:-len(".safetensors")] if name.lower().endswith(".safetensors") else name
    stem = re.sub(r"\.{2,}", ".", re.sub(r"[^A-Za-z0-9._-]+", "_", stem)).strip("._-")
    return f"{FOLDERS[kind]}{prefix}{stem[:120] or 'model'}.safetensors"


def file_id(saved_as: str) -> str:
    """The id the agent gives in `loras` or `model`: the place without its folder and
    extension (image_catalog.Resource.id)."""
    for folder in FOLDERS.values():
        if saved_as.startswith(folder):
            saved_as = saved_as[len(folder):]
    return re.sub(r"\.safetensors$", "", saved_as, flags=re.I)


def kind_of(path: str) -> str | None:
    return next((k for k, folder in FOLDERS.items() if path.startswith(folder)), None)


def _look_ok(look: Any) -> bool:
    return isinstance(look, dict) and isinstance(look.get("source"), dict) and \
        isinstance(look.get("name"), str) and look["name"].strip() != "" and \
        isinstance(look.get("size"), int) and isinstance(look.get("sha256"), str) and \
        re.fullmatch(r"[0-9a-fA-F]{64}", look["sha256"]) is not None


def _type_problem(kind: str, look: dict) -> str | None:
    """Civitai's type held to the kind asked for; None when it fits (or the hub names
    none: Hugging Face)."""
    if look["source"].get("hub") != "civitai":
        return None
    t = str((look.get("about") or {}).get("type") or "").strip()
    low = t.casefold()
    is_kind = "lora" if low in LORA_TYPES else "model" if low in MODEL_TYPES else None
    if is_kind is None:
        return (f"Civitai lists this file as {t or 'no type'}: only a LoRA or a checkpoint "
                f"is taken.")
    if is_kind != kind:
        return (f"Civitai lists this file as a {t}, so it goes where a preset takes "
                f"{'LoRAs' if is_kind == 'lora' else 'models'}: ask with "
                f"kind=\"{is_kind}\".")
    return None


def _words(look: dict) -> list[str]:
    words = (look.get("about") or {}).get("words")
    return [hubs._cut(w, 40) for w in words if str(w).strip()] \
        if isinstance(words, list) else []


def card_facts(look: dict, saved_as: str, p: presets_mod.Preset, free: Any) -> list:
    """The card's facts: the helper's look at the file and the call's own data. Nothing
    of a model's description goes on it."""
    src, about = look["source"], look.get("about") or {}
    link = hubs.page(src)
    rows: list = [("File", look["name"])]
    if src.get("hub") == "civitai":
        rows += [("Site", hubs.site(src, link)), ("Title", hubs._cut(about.get("title"))),
                 ("Type", hubs._cut(about.get("type"), 30)),
                 ("Base model", hubs._cut(about.get("base"), 40)),
                 ("Trigger words", ", ".join(_words(look)) or "None"),
                 ("Mature", "Yes" if about.get("mature") is True else "No")]
        if link:
            rows.append(("Page", link, link))
    else:
        rev = src.get("revision")
        said = f"huggingface.co/{src.get('repo')}" + (f" at {str(rev)[:12]}" if rev else "")
        rows.append(("Source", said, link) if link else ("Source", said))
    rows += [("Size", hubs.size_words(look["size"])),
             ("Licence", hubs._cut(look.get("license"), 300) or "None given"),
             ("Saved as", saved_as), ("Preset", p.label)]
    if isinstance(free, int) and not isinstance(free, bool):
        rows.append(("Free space after", hubs.size_words(max(0, free - look["size"]))))
    return rows


# ---- the rows --------------------------------------------------------------------------

def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    d["look"] = json.loads(d["look"])
    return d


async def get(store, rid: str) -> dict | None:
    return await store.read(lambda c: _row(c.execute(
        "SELECT * FROM model_requests WHERE id=?", (rid,)).fetchone()))


def view(row: dict) -> dict:
    """A request as its call's row and the page show it."""
    st = S(row["state"])
    return {"id": row["id"], "kind": row["kind"], "file": file_id(row["saved_as"]),
            "preset": row["preset"], "state": st.value, "display": st.display,
            "done": row["done"], "size": row["size"] or row["look"].get("size"),
            "error": row["error"]}


def _publish(store, c: sqlite3.Connection, row: dict) -> None:
    """Inside a write: the call's display and row words follow the request, its
    conversation hears `model_file.state`, every page `model_files.changed`."""
    from ..turns.store import redisplay
    if row["tool_call_id"] and row["conversation_id"]:
        redisplay(c, store, row["conversation_id"], row["tool_call_id"],
                  {"model_file": view(row)}, MODEL_FILE_STATE)
    store.global_event(c, MODEL_FILES_CHANGED, {"id": row["id"], "state": row["state"]})


async def _update(store, rid: str, **changes) -> dict | None:
    """The request with `changes`, stored and shown. -> the row as it is now."""
    def fn(c: sqlite3.Connection) -> dict | None:
        if changes:
            keys = ", ".join(f"{k}=?" for k in changes)
            c.execute(f"UPDATE model_requests SET {keys} WHERE id=?",
                      (*[v.value if isinstance(v, ModelFileState) else v
                         for v in changes.values()], rid))
        row = _row(c.execute("SELECT * FROM model_requests WHERE id=?", (rid,)).fetchone())
        if row is not None:
            _publish(store, c, row)
        return row
    return await store.write(fn)


def _sentence(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if text.endswith((".", "!", "?")) else text + "."


# ---- the service ------------------------------------------------------------------------

class ModelFiles:
    """The requests while Images is on: asked in its task group, then followed there
    (the module docstring). The running app holds one (core/services.py)."""

    def __init__(self, app, tg: anyio.abc.TaskGroup):
        self.app = app
        self.tg = tg
        self.following: set[str] = set()

    # ---- the tool --------------------------------------------------------------------

    async def request(self, ctx, *, preset: str, kind: str, file: str,
                      why: str) -> tuple[str, bool, dict | None]:
        """The agent's request (the module docstring, 1 to 3). -> (its answer, whether it
        is a refusal, the call's display)."""
        app, user = self.app, self.app.cfg.user
        if kind not in FOLDERS:
            return "kind is lora or model.", True, None
        p = presets_mod.of("images").get((preset or "").strip())
        if p is None:
            return (f"There is no image preset {preset!r}: list_presets lists them.", True,
                    None)
        if kind == "lora" and not p.loras:
            return f"{p.label} takes no LoRAs.", True, None
        if kind == "model" and not p.models:
            return f"{p.label} takes no model choice.", True, None
        if len((why or "").strip()) < 10:
            return (f"Nothing was asked: say why in `why`, one real sentence {user} can judge "
                    f"(what the file is for).", True, None)
        got = hubs.file_source(file)
        if isinstance(got, str):
            return got, True, None
        source, model = got
        if getattr(app, "approvals", None) is None:
            return NOBODY, True, None
        pid = ""
        try:
            pid = helper_provider(app, p)
            async with client(app, pid) as comfy:
                look = await comfy.look(source)
                if not _look_ok(look):
                    return ("The comfy helper answered the look with something that is not "
                            "a file.", True, None)
                found = look["source"].get("model")
                if model is not None and isinstance(found, int) and found != model:
                    return (f"Version {look['source'].get('version')} belongs to model "
                            f"{found}, not to model {model} of the address.", True, None)
                if why_not := _type_problem(kind, look):
                    return why_not, True, None
                saved_as = place(p, kind, look["name"])
                listed = await comfy.model_files()
        except HelperRefused as e:
            return refused(e, user), True, None
        except ImageError as e:
            return str(e), True, None
        except httpx.HTTPError as e:
            return unreachable(app, pid, e), True, None
        fid = file_id(saved_as)
        if any(isinstance(f, dict) and f.get("path") == saved_as
               for f in listed.get("files") or []):
            return (f"{saved_as} is there already: give {fid} in {FIELD[kind]} with the preset "
                    f"{p.id}.", True, None)
        asked = await app.store.read(lambda c: c.execute(
            "SELECT state FROM model_requests WHERE saved_as=? AND state IN (?, ?, ?)",
            (saved_as, *OPEN)).fetchone())
        if asked is not None:
            return (f"{fid} was asked for already and " + {
                S.ASKED: f"waits for {user}.", S.FETCHING: "is downloading.",
                S.LISTING: "waits for ComfyUI to list it."}[S(asked[0])], True, None)
        row = {"id": new_id(), "conversation_id": ctx.conversation_id,
               "tool_call_id": ctx.tool_call_id or None, "provider": pid, "preset": p.id,
               "kind": kind, "look": look, "saved_as": saved_as, "why": why.strip(),
               "state": S.ASKED.value, "job": None, "done": None, "size": look["size"],
               "error": None, "created_at": now(), "finished_at": None}
        await app.store.write(lambda c: c.execute(
            "INSERT INTO model_requests(id, conversation_id, tool_call_id, provider, preset, "
            "kind, look, saved_as, why, state, size, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (row["id"], row["conversation_id"], row["tool_call_id"], pid, p.id, kind,
             json.dumps(look, ensure_ascii=False), saved_as, row["why"], row["state"],
             row["size"], row["created_at"])))
        placed, said = anyio.Event(), list[str]()
        self.tg.start_soon(self._ask, row, f"download the {WHAT[kind]} {fid} for {p.label}",
                           card_facts(look, saved_as, p, listed.get("free")), placed, said)
        await placed.wait()
        if said:                        # refused before a card was placed
            await app.store.write(lambda c: c.execute(
                "DELETE FROM model_requests WHERE id=?", (row["id"],)))
            return said[0], True, None
        src = look["source"]
        where = hubs.site(src, hubs.page(src))
        return (f"Asked {user} for the {WHAT[kind]} {fid} for {p.label}: {look['name']}, "
                f"{hubs.size_words(look['size'])}, from {where}. It waits for {user}'s "
                f"answer; a message from the app says when {fid} is ready to use, or why "
                f"not.", False, {"model_file": view(row)})

    async def _ask(self, row: dict, action: str, facts: list, placed: anyio.Event,
                   said: list[str]) -> None:
        """The card, asked outside any turn; its answer settles the request or hands it
        to the helper. `said`: the sentence of a request refused before its card."""
        from ..turns.waiting import TURN
        TURN.set(None)                  # the app's own background: no turn waits for it
        opened = False

        def on_open() -> None:
            nonlocal opened
            opened = True
            placed.set()
        try:
            no = await self.app.approvals.ask(
                "model_file", row["conversation_id"], action, facts=facts, why=row["why"],
                call=row["tool_call_id"], wait=ASK_FOR, opened=on_open)
            if not opened:
                said.append(no or "The request could not be asked.")
                return
            await self._answered(row["id"], no)
        except Exception:
            log.exception("model file request failed", extra={
                "event": "model_files.ask_failed", "request": row["id"]})
            if not opened:
                said.append("The request could not be asked.")
        finally:
            placed.set()

    async def _answered(self, rid: str, no: str | None) -> None:
        if no is None:
            await _update(self.app.store, rid, state=S.FETCHING)
            await self._follow(rid)
            return
        user = self.app.cfg.user
        state = S.UNANSWERED if no.startswith(f"{user} did not answer") else S.DENIED
        row = await _update(self.app.store, rid, state=state, error=no, finished_at=now())
        if row is not None:
            await self._notice(row, f"The request for the {WHAT[row['kind']]} "
                                    f"{file_id(row['saved_as'])} is over: {no}")

    # ---- following a download --------------------------------------------------------

    async def resume(self) -> None:
        """At the start: every request the last run left open (the module docstring, 5)."""
        rows = await self.app.store.read(lambda c: [tuple(r) for r in c.execute(
            "SELECT id, state FROM model_requests WHERE state IN (?, ?, ?) "
            "ORDER BY created_at", OPEN)])
        for rid, state in rows:
            if state == S.ASKED:
                no = RESTARTED.format(user=self.app.cfg.user)
                row = await _update(self.app.store, rid, state=S.UNANSWERED, error=no,
                                    finished_at=now())
                if row is not None:
                    await self._notice(row, f"The request for the {WHAT[row['kind']]} "
                                            f"{file_id(row['saved_as'])} is over: {no}")
            else:
                self.tg.start_soon(self._follow, rid)

    async def _follow(self, rid: str) -> None:
        """Until the download is ready or failed: handed to the helper when it is not yet,
        then its job looked at every FOLLOW seconds."""
        if rid in self.following:
            return
        self.following.add(rid)
        try:
            while True:
                row = await get(self.app.store, rid)
                if row is None or row["state"] in MODEL_FILE_SETTLED:
                    return
                job: dict[str, Any] | None
                try:
                    async with client(self.app, row["provider"]) as comfy:
                        if row["job"] is None:
                            look = row["look"]
                            job = await comfy.fetch(look["source"], look["sha256"],
                                                    row["saved_as"], rid)
                        else:
                            job = await comfy.model_job(row["job"])
                except (httpx.TransportError, HelperDown) as e:
                    log.info("comfy helper silent", extra={
                        "event": "model_files.helper_silent", "request": rid,
                        "error": str(e) or type(e).__name__})
                    await anyio.sleep(FOLLOW)
                    continue
                except HelperRefused as e:
                    await self._failed(row, refused(e, self.app.cfg.user))
                    return
                except ImageError as e:
                    await self._failed(row, str(e))
                    return
                if job is None:
                    await self._failed(row, GONE)
                    return
                if await self._step(row, job):
                    return
                await anyio.sleep(FOLLOW)
        except Exception:
            log.exception("model file not followed", extra={
                "event": "model_files.follow_failed", "request": rid})
        finally:
            self.following.discard(rid)

    async def _step(self, row: dict, job: dict) -> bool:
        """One look at the job, stored when it changed. -> True once it settled."""
        state = {"fetching": S.FETCHING, "listing": S.LISTING, "ready": S.READY,
                 "failed": S.FAILED}.get(str(job.get("state")))
        if state is None or not isinstance(job.get("id"), str):
            await self._failed(row, "The comfy helper answered with something that is not a "
                                    "download.")
            return True
        if state is S.FAILED:
            await self._failed(row, key_words(job.get("code"), self.app.cfg.user)
                               or job.get("why") or "The comfy helper gave no reason.",
                               job=job["id"])
            return True
        changes: dict[str, Any] = {}
        for key, new in (("job", job["id"]), ("state", state.value),
                         ("done", job.get("done") if isinstance(job.get("done"), int)
                          else row["done"]),
                         ("size", job.get("size") if isinstance(job.get("size"), int)
                          else row["size"])):
            if row[key] != new:
                changes[key] = new
        if state is S.READY:
            changes.update(finished_at=now(), done=changes.get("size", row["size"]))
        if changes:
            row = await _update(self.app.store, row["id"], **changes) or row
        if state is S.READY:
            self._fresh_catalog(row["provider"])
            words = _words(row["look"])
            fid = file_id(row["saved_as"])
            label = getattr(presets_mod.of("images").get(row["preset"]), "label",
                            row["preset"])
            await self._notice(row, f"The {WHAT[row['kind']]} {fid} for {label} is ready: "
                                    f"give {fid} in {FIELD[row['kind']]}, as list_presets now "
                                    f"lists it" + (f", and write its trigger words "
                                                   f"{', '.join(words)} into the prompt"
                                                   if words else "") + ".")
            return True
        return False

    async def _failed(self, row: dict, why: str, **changes) -> None:
        why = _sentence(why)
        row = await _update(self.app.store, row["id"], state=S.FAILED, error=why,
                            finished_at=now(), **changes) or row
        await self._notice(row, f"The {WHAT[row['kind']]} {file_id(row['saved_as'])} was not "
                                f"downloaded: {why}")

    def _fresh_catalog(self, pid: str) -> None:
        from .. import image_catalog
        p = (self.app.cfg.settings.providers or {}).get(pid) or {}
        if p.get("base_url"):
            image_catalog.forget(p["base_url"])

    async def _notice(self, row: dict, text: str) -> None:
        """The app's notice to the conversation that asked; it wakes the agent."""
        if not row.get("conversation_id"):
            return
        try:
            await self.app.intercom.send("system:app", f"agent:{row['conversation_id']}",
                                         text, kind="notice")
        except KeyError:
            pass                            # its conversation is gone
        except Exception:
            log.exception("model file notice failed", extra={
                "event": "model_files.notice_failed", "request": row["id"]})


# ---- looking around a hub ---------------------------------------------------------------

def _class(mature: bool) -> str:
    """The guard's class of what a hub sent (guard/facts.py): nsfw where the hub marks
    it mature, else public."""
    return "nsfw" if mature else "public"


async def search(app, *, hub: str, kind: str, query: str = "", preset: str = "",
                 base: str = "", sort: str = "", period: str = "", tag: str = "",
                 after: str = "") -> tuple[str, bool, dict]:
    """A page of a hub's files the helper would take. -> (the answer, whether it is a
    refusal, the call's display)."""
    if hub not in hubs.HUBS:
        return "hub is civitai or huggingface.", True, {}
    if kind not in FOLDERS:
        return "kind is lora or model.", True, {}
    if sort and sort not in hubs.SORTS:
        return f"sort is {', '.join(hubs.SORTS)}.", True, {}
    if period and period not in hubs.PERIODS:
        return f"period is {', '.join(hubs.PERIODS)}.", True, {}
    p = None
    if preset.strip():
        p = presets_mod.of("images").get(preset.strip())
        if p is None:
            return f"There is no image preset {preset!r}: list_presets lists them.", True, {}
        if not (p.loras if kind == "lora" else p.models):
            return (f"{p.label} takes no LoRAs." if kind == "lora" else
                    f"{p.label} takes no model choice."), True, {}
    # a preset keeps a Civitai search to its base; without one the page is of every base
    every = ""
    if p is not None and not base.strip():
        if hub == "civitai" and p.hub_base:
            base = p.hub_base
        else:
            every = (f"Files of every base: {p.label} names no base for {hubs.HUBS[hub]}. "
                     f"Give base, as {hubs.HUBS[hub]} names it, to keep to its own.")
    args = {"hub": hub, "kind": kind, "query": query.strip(), "preset": preset.strip(),
            "base": base.strip(), "sort": sort, "period": period, "tag": tag.strip()}
    body: dict[str, Any] = {"hub": hub, "kind": kind, "limit": hubs.PAGE}
    for k in ("query", "base", "sort", "period", "tag"):
        if args[k]:
            body[k] = args[k]
    if after.strip():
        body["after"] = after.strip()
    pid = ""
    try:
        pid = helper_provider(app, p)
        async with client(app, pid) as comfy:
            answer = await comfy.search_models(body)
    except HelperRefused as e:
        return refused(e, app.cfg.user), True, {}
    except ImageError as e:
        return str(e), True, {}
    except httpx.HTTPError as e:
        return unreachable(app, pid, e), True, {}
    results = [r for r in answer.get("results") or [] if isinstance(r, dict)]
    mature = any(r.get("mature") is True for r in results)
    text = hubs.search_text(answer, hub, kind, args)
    if every:
        head, _, rest = text.partition("\n")
        text = "\n".join(x for x in (head, every, rest) if x)
    return (text, False,
            {"hub": hub, "count": len(results), "class": _class(mature)})


async def read(app, model: str) -> tuple[str, str, bool, dict]:
    """What a hub says of one model, as read_model reads it. -> (the app's head line for
    every part, the text or the sentence why there is none, whether it is a refusal, the
    call's display)."""
    src = hubs.model_source(model)
    if isinstance(src, str):
        return "", src, True, {}
    pid = ""
    try:
        pid = helper_provider(app)
        async with client(app, pid) as comfy:
            answer = await comfy.read_model(src)
    except HelperRefused as e:
        return "", refused(e, app.cfg.user), True, {}
    except ImageError as e:
        return "", str(e), True, {}
    except httpx.HTTPError as e:
        return "", unreachable(app, pid, e), True, {}
    return (hubs.read_head(answer, src), hubs.read_text(answer), False,
            {"hub": src["hub"], "class": _class(answer.get("mature") is True)})


async def pictures(app, ctx, model: str, which: list[int] | None,
                   sees: bool) -> tuple[str, bool, dict, list[str]]:
    """A model's example pictures, at most LOOK_MAX: the ones numbered `which` (as
    read_model numbers its example prompts), else the first that have a picture. Each is
    an attachment of the call, as shell__look's images are; a model that sees gets the
    pixels with the result (ToolResult.images), one that does not a description by the
    vision role. -> (the text, whether nothing came, the display, the attachment ids)."""
    from .. import attachments, vision
    src = hubs.model_source(model)
    if isinstance(src, str):
        return src, True, {}, []
    wanted = list(dict.fromkeys(int(n) for n in which or [] if isinstance(n, int)))
    if len(wanted) > LOOK_MAX:
        return f"Look at most {LOOK_MAX} examples at once.", True, {}, []
    pid = ""
    try:
        pid = helper_provider(app)
        async with client(app, pid) as comfy:
            answer = await comfy.read_model(src)
            examples = hubs.examples_of(answer)
            if wanted:
                bad = [n for n in wanted if not 1 <= n <= len(examples)]
                if bad:
                    return (f"This model has {len(examples)} example prompts: there is no "
                            f"example {bad[0]}.", True, {}, [])
                chosen = [(n, examples[n - 1]) for n in wanted]
            else:
                chosen = [(n, e) for n, e in enumerate(examples, 1)
                          if isinstance(e.get("picture"), dict)][:LOOK_MAX]
            if not chosen:
                return "This model's examples have no pictures.", True, {}, []
            lines = [hubs.HEAD_PICTURES.format(site=hubs.site(src, answer.get("address")),
                                               version=hubs.examples_version(answer))]
            ids: list[str] = []
            mature = False
            for n, e in chosen:
                pic = e.get("picture")
                lines.append("")
                lines += hubs.example_lines(n, e)
                if not isinstance(pic, dict) or pic.get("id") in (None, ""):
                    lines.append(f"Example {n} has no picture.")
                    continue
                try:
                    data, media = await comfy.picture(src["hub"], pic["id"], EDGE)
                except HelperRefused as err:
                    lines.append(f"Picture {n}: {err}")
                    continue
                row = await attachments.save_generated(
                    app.cfg, ctx.store, data, f"example-{n}{_EXT.get(media, '.png')}", media,
                    ctx.tool_call_id or None, in_message=False)
                ids.append(row["id"])
                mature = mature or pic.get("mature") is True
                if sees:
                    lines.append(f"Picture {n} follows.")
                else:
                    said = await vision.describe(app.llm, data, ctx.conversation_id,
                                                 ctx.turn_id)
                    lines.append(f"Picture {n}: {said}")
    except HelperRefused as e:
        return refused(e, app.cfg.user), True, {}, []
    except ImageError as e:
        return str(e), True, {}, []
    except httpx.HTTPError as e:
        return unreachable(app, pid, e), True, {}, []
    return ("\n".join(lines), not ids,
            {"hub": src["hub"], "count": len(ids), "class": _class(mature)}, ids)


_EXT = {"image/jpeg": ".jpg", "image/webp": ".webp", "image/png": ".png"}


# ---- the page ----------------------------------------------------------------------------

async def listing(app) -> dict:
    """GET /api/model-files: every file that came by request (the helper's list, each
    with what the app stored of its request), then the requests still on their way,
    newest first; and the free space on the models disk. ImageError when there is no
    helper or it refused; httpx's error when it did not answer."""
    pid = helper_provider(app)
    async with client(app, pid) as comfy:
        data = await comfy.model_files()
    rows = await app.store.read(lambda c: [_row(r) for r in c.execute(
        "SELECT * FROM model_requests ORDER BY created_at DESC")])
    by_id = {r["id"]: r for r in rows if r is not None}
    images = presets_mod.of("images")
    out, seen = [], set()
    for f in data.get("files") or []:
        req = f.get("requested") if isinstance(f, dict) else None
        if not isinstance(req, dict) or not isinstance(f.get("path"), str):
            continue
        row = by_id.get(str(req.get("holder") or ""))
        src: dict = req["source"] if isinstance(req.get("source"), dict) else \
            (row["look"]["source"] if row else {})
        about: dict = req["about"] if isinstance(req.get("about"), dict) else \
            ((row["look"].get("about") or {}) if row else {})
        seen.add(f["path"])
        out.append(_entry(f["path"], row, src, about, images, size=f.get("size"),
                          at=_came(req.get("at")) or (row or {}).get("finished_at"),
                          state=S.READY, removable=True))
    for row in rows:
        if row is None or row["state"] not in OPEN or row["saved_as"] in seen:
            continue
        out.append(_entry(row["saved_as"], row, row["look"]["source"],
                          row["look"].get("about") or {}, images, size=row["size"],
                          at=row["created_at"], state=S(row["state"]), removable=False))
    out.sort(key=lambda e: str(e["at"] or ""), reverse=True)
    free = data.get("free")
    return {"files": out, "free": free if isinstance(free, int) else None}


def _came(at: Any) -> str | None:
    """When the helper says a file came (Unix seconds) as the tables store a time."""
    if isinstance(at, bool) or not isinstance(at, (int, float)):
        return None
    return iso(datetime.fromtimestamp(at, timezone.utc))


def _entry(path: str, row: dict | None, src: dict, about: dict, images: dict, *,
           size: Any, at: Any, state: ModelFileState, removable: bool) -> dict:
    p = images.get(row["preset"]) if row else None
    address = hubs.page(src) if src else None
    hf = src.get("hub") == "huggingface"
    return {"path": path, "id": file_id(path), "kind": (row or {}).get("kind") or kind_of(path),
            "size": size if isinstance(size, int) else None, "at": str(at) if at else None,
            "preset": row["preset"] if row else None,
            "preset_label": p.label if p else (row["preset"] if row else None),
            "site": hubs.site(src, address) if src.get("hub") else None,
            "title": (src.get("repo") if hf else hubs._cut(about.get("title"))) or None,
            "address": address, "request": row["id"] if row else None,
            "state": state.value, "display": state.display,
            "done": row["done"] if row and state is not S.READY else None,
            "removable": removable}


async def remove(app, path: str) -> None:
    """DELETE /api/model-files/{path}: a file that came by request, off the models disk.
    HelperRefused with the helper's sentence (a file it did not download, a batch that
    holds ComfyUI, not there)."""
    pid = helper_provider(app)
    async with client(app, pid) as comfy:
        await comfy.remove_model_file(path)
    from .. import image_catalog
    image_catalog.forget(app.cfg.settings.providers[pid]["base_url"])
    app.store.emit_global({"type": MODEL_FILES_CHANGED, "path": path})


async def civitai_key(app, method: str, key: str | None = None) -> dict:
    """/api/civitai-key: GET whether the comfy helper keeps a Civitai key, PUT a new one
    (the helper asks Civitai about it), DELETE it -> {state, display} (KeyState). The key
    goes from the page to the helper and the app keeps nothing of it; after a change every
    page reads the state again (`model_files.changed`). HelperRefused with the helper's
    sentence (a key it or Civitai refuses, nothing changed); httpx's error when it did not
    answer."""
    async with client(app, helper_provider(app)) as comfy:
        got = await comfy.civitai_key(method, key)
    if method != "GET":
        app.store.emit_global({"type": MODEL_FILES_CHANGED, "what": "civitai_key"})
    kept = KeyState.SET if got.get("set") is True else KeyState.NOT_SET
    return {"state": kept.value, "display": kept.display}
