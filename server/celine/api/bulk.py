"""POST /api/bulk-actions: one action on many things at once, from a list's Select or its
"Archive older than". Each thing goes through its own route, so every rule of one holds
for many: what can be archived, what is busy (a live turn is never
touched), what is in the Trash to delete for good. What could not be done is skipped
with the sentence its route said."""
from __future__ import annotations

import functools
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..core.ids import iso
from ..core.route_params import TheServices
from ..core.services import Services
from ..core.store import Store
from ..errors import CelineError

router = APIRouter(prefix="/api")

What = Literal["conversations", "documents", "folders", "notes", "chats", "terminals"]
Action = Literal["archive", "unarchive", "trash", "restore", "purge"]


class BulkIn(BaseModel):
    what: What
    action: Action
    # a chat of the Messages archive is "<kb>:<chat id>", a note its path (trash) or its
    # Trash id (restore, purge), a folder its path
    ids: list[str] = Field(default_factory=list, max_length=5000)
    # with action archive on conversations: instead of ids, every one of `kind` not
    # pinned and last changed more than this many days ago
    older_than: int | None = Field(default=None, ge=1, le=3660)
    kind: Literal["chat", "mission"] | None = None
    # the ids it would act on, nothing done
    dry_run: bool = False


@functools.cache
def _routes() -> dict:
    """(what, action) -> the route that does it to one: (services, id) -> anything. Each
    route is handed what it takes, by name."""
    from ..conversations import api as conv
    from ..terminals import api as terms
    from . import connectors, documents as docs, kb, notebook

    def chat(fn):
        async def one(s: Services, key: str):
            kid, _, n = key.rpartition(":")
            return await fn(s, kid, int(n))
        return one

    return {
        ("conversations", "archive"): lambda s, i: conv.archive_conversation(
            store=s.store, services=s, cid=i),
        ("conversations", "unarchive"): lambda s, i: conv.unarchive_conversation(store=s.store, cid=i),
        ("conversations", "trash"): lambda s, i: conv.delete_conversation(
            store=s.store, cfg=s.cfg, cid=i),
        ("conversations", "restore"): lambda s, i: conv.restore_conversation(store=s.store, cid=i),
        ("conversations", "purge"): lambda s, i: conv.delete_conversation(
            store=s.store, cfg=s.cfg, cid=i, for_good=True),
        ("documents", "trash"): lambda s, i: docs.patch_item(
            services=s, iid=int(i), body=docs.ItemPatch(trashed=True)),
        ("documents", "restore"): lambda s, i: docs.patch_item(
            services=s, iid=int(i), body=docs.ItemPatch(trashed=False)),
        ("documents", "purge"): lambda s, i: docs.delete_item(services=s, iid=int(i)),
        ("folders", "trash"): lambda s, p: docs.patch_folder(
            services=s, path=p, body=docs.FolderPatch(trashed=True)),
        ("folders", "restore"): lambda s, p: docs.patch_folder(
            services=s, path=p, body=docs.FolderPatch(trashed=False)),
        ("folders", "purge"): lambda s, p: docs.delete_folder(services=s, path=p),
        ("notes", "trash"): lambda s, p: notebook.notebook_delete(store=s.store, cfg=s.cfg, path=p),
        ("notes", "restore"): lambda s, t: notebook.notebook_untrash(store=s.store, cfg=s.cfg, tid=t),
        ("notes", "purge"): lambda s, t: notebook.notebook_purge(cfg=s.cfg, tid=t),
        ("chats", "trash"): chat(lambda s, k, n: kb.patch_chat(
            services=s, sid=k, chat_id=n, body=kb.ChatPatch(trashed=True))),
        ("chats", "restore"): chat(lambda s, k, n: kb.patch_chat(
            services=s, sid=k, chat_id=n, body=kb.ChatPatch(trashed=False))),
        ("chats", "purge"): chat(lambda s, k, n: connectors.delete_chat(
            services=s, sid=k, chat_id=n)),
        ("terminals", "trash"): lambda s, i: terms.delete_terminal(store=s.store, cfg=s.cfg, id=i),
        ("terminals", "restore"): lambda s, i: terms.restore_terminal(store=s.store, id=i),
        ("terminals", "purge"): lambda s, i: terms.delete_terminal(
            store=s.store, cfg=s.cfg, id=i, for_good=True),
    }


async def older(store: Store, kind: str, days: int) -> list[str]:
    """The conversations of `kind` an "Archive older than" takes: in the list (not a
    sub-agent, not archived, not deleted, not pinned), last changed more than `days`
    days ago. The oldest first."""
    cutoff = iso(datetime.now(timezone.utc) - timedelta(days=days))
    return await store.read(lambda c: [r[0] for r in c.execute(
        "SELECT id FROM conversations WHERE agent_of IS NULL AND trashed_at IS NULL AND "
        "archived_at IS NULL AND pinned = 0 AND kind = ? AND updated_at < ? "
        "ORDER BY updated_at, id", (kind, cutoff))])


@router.post("/bulk-actions")
async def bulk(services: TheServices, body: BulkIn) -> dict:
    """{what, action, ids} or, to archive, {what: conversations, action: archive, kind,
    older_than}. -> {done: [ids], skipped: [{id, reason}]}; with `dry_run` only {ids}:
    what it would act on (the ones a rule refuses are found when it runs)."""
    run = _routes().get((body.what, body.action))
    if run is None:
        raise CelineError("bad_request", f"{body.what} cannot {body.action}.", 400)
    if body.older_than is not None:
        if (body.what, body.action) != ("conversations", "archive") or body.kind is None:
            raise CelineError("bad_request", "older_than archives conversations of a kind.",
                              400)
        ids = await older(services.store, body.kind, body.older_than)
    else:
        ids = list(dict.fromkeys(body.ids))
    if body.dry_run:
        return {"ids": ids}
    done, skipped = [], []
    for i in ids:
        try:
            await run(services, i)
        except CelineError as e:
            skipped.append({"id": i, "reason": str(e)})
        except ValueError:
            skipped.append({"id": i, "reason": "Not found."})
        else:
            done.append(i)
    return {"done": done, "skipped": skipped}
