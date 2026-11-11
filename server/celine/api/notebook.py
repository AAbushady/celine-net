"""The notebook browser: the same files its tools see, for you. The notebook is its own,
the one notebook (agent.notebook)."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..core.dto import Answer
from ..errors import CelineError, NotFound, TooLarge
from ..skills import notebook_index
from ..skills.notebook import (NO_SUMMARY, history, move_note, mtime, trash, trashed,
                               unstamped, untrash, versions, write_note)
from ..core.route_params import TheConfig, TheServices, TheStore

router = APIRouter(prefix="/api")


def _notebook_path(ch, rel: str) -> Path:
    root = ch.notebook.resolve()
    p = (root / rel).resolve()
    if rel in ("", ".") or not p.is_relative_to(root) or p == root:
        raise CelineError("bad_request", f"{rel!r} is outside {ch.name}'s notebook.", 400)
    # the app's own files (.index.db, .versions, .trash) are not notes, as for the tools
    if any(part.startswith(".") for part in p.relative_to(root).parts):
        raise CelineError("bad_request", f"{rel!r} is not a note.", 400)
    return p


def _virtual(services) -> dict[str, str]:
    nb = _notebook_skill(services)
    return nb.virtual() if nb is not None else {}


def _refuse_virtual(rel: str) -> None:
    if rel == "tools" or rel.startswith("tools/"):
        raise CelineError("bad_request", "The app generates tools/. You cannot change it.", 400)


def _notebook_skill(services):
    return services.registry.skills.get("notebook")


def _owner(services, rel: str) -> str | None:
    nb = _notebook_skill(services)
    return nb.owner(rel) if nb is not None else None


@router.get("/notebook/tree")
async def notebook_tree(cfg: TheConfig, services: TheServices, store: TheStore) -> dict:
    """Every folder and note; a note with `updated_at` (ISO, UTC) and `line`, the summary
    its index keeps of it (null before it has one)."""
    ch = cfg.agent
    nb = _notebook_skill(services)
    lines = {r["path"]: r["summary"] for r in await notebook_index.list(store)
             if r["summary"] and r["summary"] != NO_SUMMARY}
    if nb is not None:  # a placeholder named after the user lands once there is a name
        await nb.seed_one(store)
    root = ch.notebook

    def walk(d: Path, rel: str) -> list[dict]:
        out = []
        for p in sorted(d.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
            if p.name.startswith("."):
                continue
            r = f"{rel}/{p.name}" if rel else p.name
            if p.is_dir():
                out.append({"name": p.name, "path": r, "kind": "dir",
                            "children": walk(p, r)})
            else:
                owner = _owner(services, r)
                out.append({"name": p.name, "path": r, "kind": "file",
                            "bytes": p.stat().st_size, "protected": owner is not None,
                            "owner": owner, "updated_at": mtime(p),
                            "line": lines.get(r)})
        return out

    tree = walk(root, "") if root.is_dir() else []
    virtual = _virtual(services)
    if virtual:
        tree.append({"name": "tools", "path": "tools", "kind": "dir", "virtual": True,
                     "children": [{"name": p.split("/", 1)[1], "path": p, "kind": "file",
                                   "bytes": len(c), "protected": True, "virtual": True}
                                  for p, c in virtual.items()]})
    return {"tree": tree}


@router.get("/notebook/entries/{path:path}")
async def notebook_read(services: TheServices, cfg: TheConfig, path: str) -> dict:
    """{path, content (the text as stored, for editing), shown (to read: its name, the
    user's and its pronouns filled in, the Last changed line left out, the head says
    it), protected, owner}."""
    ch = cfg.agent
    if (text := _virtual(services).get(path)) is not None:
        return {"path": path, "content": text, "shown": text, "protected": True,
                "virtual": True}
    p = _notebook_path(ch, path)
    if not p.is_file():
        raise NotFound("note")
    try:
        text = p.read_text()
    except UnicodeDecodeError:
        raise CelineError("not_text", f"{path} is not a text file.", 415) from None
    return {"path": path, "content": text, "shown": cfg.substitute(unstamped(text)),
            "protected": _owner(services, path) is not None,
            "owner": _owner(services, path)}


class NotebookWrite(BaseModel):
    content: str


@router.put("/notebook/entries/{path:path}")
async def notebook_write(store: TheStore, cfg: TheConfig, path: str, body: NotebookWrite) -> dict:
    """The note's whole text."""
    ch = cfg.agent
    _entry_path(path)
    if len(body.content.encode()) > cfg.r("max_message_bytes"):
        raise TooLarge("This note", len(body.content.encode()),
                       cfg.r("max_message_bytes"))
    _refuse_virtual(path)
    p = _notebook_path(ch, path)
    content = await write_note(cfg, store, ch, p, path, body.content)
    shown = cfg.substitute(unstamped(content))
    return {"ok": True, "bytes": len(content), "content": content, "shown": shown}


@router.get("/notebook/versions")
async def notebook_versions(cfg: TheConfig, path: str, q: str = "",
                            before: int | None = None) -> dict:
    """A note's History, as every long text's (web/src/lib/texts.ts): skills/notebook.py
    versions, the listing its notebook__list_versions shows too."""
    _notebook_path(cfg.agent, path)
    return versions(cfg.agent.notebook.resolve(), path, q, before)


@router.get("/notebook/versions/{vid}")
async def notebook_version(cfg: TheConfig, vid: int, path: str) -> dict:
    """One text the note had: {id, at, by, change, size, text}, as stored; Restore this
    version puts it into the editor and Save writes it."""
    _notebook_path(cfg.agent, path)
    found = next((r for r in history(cfg.agent.notebook.resolve(), path) if r["id"] == vid),
                 None)
    if found is None:
        raise NotFound("version")
    return found


class NotebookPath(BaseModel):
    path: str = Field(min_length=1, max_length=400)


@router.post("/notebook/folders")
async def notebook_mkdir(cfg: TheConfig, body: NotebookPath) -> dict:
    _refuse_virtual(body.path)
    p = _notebook_path(cfg.agent, body.path)
    if p.exists() and not p.is_dir():
        raise CelineError("bad_request", f"{body.path} is a file.", 400)
    p.mkdir(parents=True, exist_ok=True)
    return {"ok": True}


class NotebookChange(BaseModel):
    path: str | None = Field(default=None, min_length=1, max_length=400)
    protected: bool | None = None


@router.patch("/notebook/entries/{path:path}")
async def notebook_change(cfg: TheConfig, services: TheServices, store: TheStore, path: str, body: NotebookChange) -> dict:
    """{path}: the note or folder moves there, its history and protection along;
    {protected}: write protection for it on a note (the seeded ones, skills/notebook.py
    SEEDS, stay). -> {ok, owner}."""
    _entry_path(path)
    ch = cfg.agent
    nb = _notebook_skill(services)
    if body.path is not None:
        await _move(store, ch, nb, path, body.path)
        path = body.path
    if body.protected is not None:
        _refuse_virtual(path)
        if nb is None:
            raise NotFound("notebook")
        if nb.owner(path) == "config":
            raise CelineError("bad_request",
                              f"{path} is always read-only for {ch.name}.", 400)
        _notebook_path(ch, path)
        await nb.mark(store, path, body.protected)
    return {"ok": True, "owner": nb.owner(path) if nb is not None else None}


async def _move(store, ch, nb, src_rel: str, dst_rel: str) -> None:
    _refuse_virtual(src_rel)
    _refuse_virtual(dst_rel)
    src, dst = _notebook_path(ch, src_rel), _notebook_path(ch, dst_rel)
    if not src.exists():
        raise NotFound("path")
    if dst.exists():
        raise CelineError("bad_request", f"{dst_rel} already exists.", 400)
    if src.is_dir() and dst.is_relative_to(src):
        raise CelineError("bad_request", "A folder cannot move into itself.", 400)
    move_note(ch.notebook.resolve(), src, dst, src_rel, dst_rel)
    await notebook_index.move(store, src_rel, dst_rel)
    if nb is not None:
        base = src_rel.rstrip("/")
        for rel in sorted(nb.marked):
            if rel == src_rel or rel.startswith(base + "/"):
                await nb.mark(store, rel, False)
                await nb.mark(store, dst_rel.rstrip("/") + rel[len(base):], True)


def _entry_path(path: str) -> None:
    if not path or len(path) > 400:
        raise CelineError("bad_request", "A notebook path is 1 to 400 characters.", 400,
                          {"field": "path"})


@router.delete("/notebook/entries/{path:path}")
async def notebook_delete(store: TheStore, cfg: TheConfig, path: str) -> dict:
    """The note or folder moves to .trash/<time>/<path>, as its notebook__delete does."""
    ch = cfg.agent
    _refuse_virtual(path)
    p = _notebook_path(ch, path)
    if not p.exists():
        raise NotFound("path")
    trash(ch.notebook.resolve(), p, path, "user")
    removed = await notebook_index.delete_prefix(store, path)
    return {"ok": True, "index_rows": removed}


class TrashedNote(Answer):
    """A deleted note or folder in the trash (skills/notebook.py trashed), as its
    notebook__list_deleted shows it too."""
    id: str
    path: str
    kind: Literal["file", "dir"]
    at: str
    by: Literal["agent", "user"]


class NotebookTrash(Answer):
    trash: list[TrashedNote]


@router.get("/notebook/trash")
async def notebook_trash(cfg: TheConfig) -> NotebookTrash:
    """What the trash holds, newest first."""
    return NotebookTrash(trash=[TrashedNote.of(e)
                                for e in trashed(cfg.agent.notebook.resolve())])


@router.post("/notebook/trash/{tid}/restore")
async def notebook_untrash(store: TheStore, cfg: TheConfig, tid: str) -> dict:
    """A deleted note or folder back where it was, indexed again, its notes' earlier texts
    the agent's again (skills/notebook.py untrash, as its notebook__restore does)."""
    try:
        entry = await untrash(store, cfg.agent.notebook.resolve(), tid, "user")
    except LookupError:
        raise NotFound("deleted note") from None
    except FileExistsError as e:
        raise CelineError("bad_request", f"{e.args[0]} exists again. Move it away first.",
                          400) from None
    return {"ok": True, "path": entry["path"]}


@router.delete("/notebook/trash/{tid}")
async def notebook_purge(cfg: TheConfig, tid: str) -> dict:
    """One deleted note or folder gone for good, for you: its .trash folder goes. The agent's
    own delete only ever moves a note to the trash."""
    root = cfg.agent.notebook.resolve()
    entry = next((e for e in trashed(root) if e["id"] == tid), None)
    if entry is None:
        raise NotFound("deleted note")
    shutil.rmtree(root / ".trash" / tid)
    return {"ok": True, "path": entry["path"]}
