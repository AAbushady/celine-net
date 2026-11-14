"""Files between the app and its shellbox (shellbox/shellboxd.py, PUT and GET /file),
streamed both ways, never whole in memory.

While the shellbox is on, every file the user uploads (a chat attachment, a Documents
upload) is copied into its home at ~/uploads/<sha>/<name> (<sha> the first 12 characters
of its bytes' sha256, so the same bytes uploaded again land on the same file) right
after it is stored: in the background, so the upload never
waits for the box nor fails with it. A box that is down or full only costs a line in the
app log. A chat attachment keeps where its copy landed (attachments.box_path), and its
context names it. Files it makes or fetches are not copied by themselves: shell__get_file
copies one of its own into ~/uploads/ the same way (copy_in).

~/uploads/ follows the app (`mirror`, every hour with the app's cleanup look): a copy goes
when the app no longer holds its bytes, so a file the agent made and did not keep leaves the
box when it is cleared, and a chat's files when the chat is purged.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from pathlib import Path

import anyio
import httpx

from . import shellbox
from .errors import ToolRefused, said
from .settings import features

log = logging.getLogger("celine.box_files")

UPLOADS = "uploads"
CHUNK = 1 << 20
TRANSFER = 600          # seconds one chunk of a file may take
MIRROR_MARGIN = 3600.0  # seconds a folder is left alone after it changed: a copy on its way
_tasks: set[asyncio.Task] = set()      # running copies, kept from the garbage collector


def safe_name(filename: str) -> str:
    """A plain file name: its base name only, no control characters, no leading dots."""
    name = re.sub(r"[\x00-\x1f\x7f]", "", (filename or "").replace("\\", "/")).split("/")[-1]
    return name.strip().lstrip(".").strip() or "file"


async def _chunks(src: Path):
    async with await anyio.open_file(src, "rb") as f:
        while chunk := await f.read(CHUNK):
            yield chunk


async def put(cfg, src: Path | bytes, rel: str, *, origin: str = "",
              transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """src (a file, or the bytes themselves) into its home at rel, tagged with `origin`
    when given (where others' text came from, for the guard). {"path", "bytes"};
    RuntimeError with the box's sentence."""
    size = len(src) if isinstance(src, bytes) else src.stat().st_size
    async with shellbox.client(cfg, TRANSFER, transport) as c:
        r = await c.put(shellbox.url(cfg) + "/file",
                        params={"path": rel,
                                **({"origin": origin} if origin else {})},
                        content=src if isinstance(src, bytes) else _chunks(src),
                        headers={"Content-Length": str(size)})
    if r.status_code != 200:
        raise RuntimeError(str(shellbox.refusal(cfg, r)))
    return r.json()


async def stream(cfg, rel: str, *, transport: httpx.AsyncBaseTransport | None = None):
    """The agent's file at rel, as chunks of bytes. FileNotFoundError with the box's sentence on
    a 404, RuntimeError on another refusal, both before the first chunk."""
    async with shellbox.client(cfg, TRANSFER, transport) as c, \
            c.stream("GET", shellbox.url(cfg) + "/file", params={"path": rel}) as r:
        if r.status_code != 200:
            await r.aread()
            if r.status_code == 404:
                raise FileNotFoundError(said(r.content).text
                                        or f"No file {rel} in the shellbox home.")
            raise RuntimeError(str(shellbox.refusal(cfg, r)))
        async for chunk in r.aiter_bytes(CHUNK):
            yield chunk


async def copy_in(cfg, src: Path, filename: str, *, origin: str = "",
                  transport: httpx.AsyncBaseTransport | None = None) -> str:
    """A file into its home at ~/uploads/<sha12>/<name>, tagged with `origin` (put); bytes
    already there are not sent again, so that file stays as it is, its edits too. -> where
    it is ("~/uploads/..."). httpx.HTTPError, RuntimeError (the box's sentence), OSError."""
    sha = await anyio.to_thread.run_sync(_sha, src)
    rel = f"{UPLOADS}/{sha[:12]}/{safe_name(filename)}"
    if not await _there(cfg, rel, transport):
        await put(cfg, src, rel, origin=origin, transport=transport)
    return f"~/{rel}"


async def copy_upload(cfg, src: Path, filename: str, store=None, aid: str | None = None,
                      transport: httpx.AsyncBaseTransport | None = None) -> str | None:
    """The user's upload into ~/uploads/ (copy_in); with aid, the attachment keeps where it
    landed. Where it is in its home, or None when it did not get there (said in the log)."""
    if not shellbox.url(cfg):
        return None
    try:
        path = await copy_in(cfg, src, filename, origin=f"upload:{filename}", transport=transport)
    except (httpx.HTTPError, RuntimeError, OSError) as e:
        log.warning("%s did not reach the box: %s", filename,
                    e if isinstance(e, RuntimeError) else "the shellbox is not reachable.")
        return None
    if store is not None and aid is not None:
        from .attachments import set_box_path
        await set_box_path(store, aid, path)
    return path


async def _there(cfg, rel: str, transport: httpx.AsyncBaseTransport | None) -> bool:
    """Whether the file at rel is in its home (its first byte asked for)."""
    async with shellbox.client(cfg, TRANSFER, transport) as c:
        r = await c.get(shellbox.url(cfg) + "/file", params={"path": rel},
                        headers={"Range": "bytes=0-0"})
    if r.status_code == 404:
        return False
    if r.status_code not in (200, 206, 416):        # 416: an empty file
        raise RuntimeError(str(shellbox.refusal(cfg, r)))
    return True


def _sha(src: Path) -> str:
    with src.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _held(c) -> set[str]:
    """The folders of ~/uploads/ whose bytes the app still holds: an attachment that is not
    cleared (a kept or temporary file it made, a file on a message), the agent's portrait."""
    return {r[0][:12] for r in c.execute(
        "SELECT sha256 FROM attachments WHERE life IS NULL OR life != 'cleared' "
        "UNION SELECT portrait FROM agent WHERE portrait IS NOT NULL")}


async def mirror(cfg, store, documents=None, *, now: float | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> int:
    """Every folder ~/uploads/<sha12>/ whose bytes the app no longer holds (`_held`, and a
    Documents file of `documents`), removed with what is in it, once MIRROR_MARGIN after it
    last changed. Only while the shellbox is on; a box that does not answer or refuses is
    asked again at the next look. -> how many folders went."""
    if not features.on(cfg, "shellbox") or not shellbox.url(cfg):
        return 0
    held = await store.read(_held)
    if documents is not None:
        held |= {s[:12] for s in await documents.shas()}
    t = time.time() if now is None else now
    try:
        status, listed = await shellbox.call(cfg, "GET", "/uploads", transport=transport)
        if status != 200:
            return 0                     # a box without the listing
        gone = 0
        for f in listed.get("folders") or []:
            name = str(f.get("name") or "")
            if name in held or t - float(f.get("mtime") or t) < MIRROR_MARGIN:
                continue
            status, res = await shellbox.call(cfg, "DELETE", f"/{UPLOADS}/{name}",
                                              transport=transport)
            gone += int(status == 200 and res.get("removed") is True)
    except (httpx.HTTPError, ToolRefused) as e:
        log.info("the box's uploads were not tidied: %s", e if isinstance(e, ToolRefused)
                 else "the shellbox is not reachable.")
        return 0
    if gone:
        log.info("box copies removed", extra={"event": "box_files.mirrored", "count": gone})
    return gone


def copy_later(cfg, src: Path, filename: str, store=None, aid: str | None = None) -> None:
    """copy_upload in the background, while the shellbox is on: the upload's answer never
    waits for it."""
    if not features.on(cfg, "shellbox") or not shellbox.url(cfg):
        return
    t = asyncio.get_running_loop().create_task(copy_upload(cfg, src, filename, store, aid))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
