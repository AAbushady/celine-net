"""The knowledge bases as the start-up sees them (core/parts.py): the Documents folder
with its index and its watcher while Documents is on, and each connector's archive, feed
and MCP server while its feature is on."""
from __future__ import annotations

import logging

import anyio

from ..core.event_types import KB_CHANGED
from ..core.parts import Part, Run
from ..core.services import Services
from ..settings import features

log = logging.getLogger("celine.kb")


async def _watch(documents, store) -> None:
    """Edits made outside the app (an editor, git) reach the index within a second."""
    import watchfiles
    try:
        async for _ in watchfiles.awatch(documents.root, debounce=800, recursive=True,
                                          rust_timeout=500):
            try:
                changed = await anyio.to_thread.run_sync(documents.rescan)
            except Exception:
                log.exception("documents rescan failed")
                continue
            if any(changed.get(k) for k in ("new", "changed", "moved", "removed")):
                store.emit_global({"type": KB_CHANGED, "kb": documents.id})
    except Exception:
        log.exception("documents watcher stopped")


def _documents(services: Services) -> Run:
    kbs, work = services.kbs, services.dispatcher.work

    async def describe_again(p: dict) -> None:       # kb/documents_describe.py
        if (kb := kbs.get(p["kb"])) is not None:
            await anyio.to_thread.run_sync(kb.kick_describe)

    async def run() -> None:
        for kid in features.get("documents").kbs:
            await anyio.to_thread.run_sync(kbs.open, kid)
        try:
            work.handle("documents.describe", ("small",), describe_again)
            work.notify()
            if (documents := kbs.documents()) is not None:
                documents.work = work
                scanned = await anyio.to_thread.run_sync(documents.rescan)
                log.info("documents scanned", extra={"event": "documents.scanned", **scanned})
                await _watch(documents, services.store)
            await anyio.sleep_forever()
        finally:
            for kid in features.get("documents").kbs:
                kbs.close(kid)
    return Run(run, early=True)


def _connector(services: Services, f: features.Feature) -> Run:
    """Discord, WhatsApp: its archive open, its feed, its MCP server."""
    kbs, connectors = services.kbs, services.connectors

    async def run() -> None:
        for kid in f.kbs:
            kbs.open(kid)
        for sid in f.mcp:
            connectors.open(sid)
        try:
            async with anyio.create_task_group() as g:
                for sid in f.mcp:
                    g.start_soon(services.mcp.run_one, services.cfg.mcp.get(sid))
        finally:
            for sid in f.mcp:
                connectors.close(sid)
            for kid in f.kbs:
                kbs.close(kid)
    return Run(run, early=True)


def _runs(services: Services) -> dict[str, Run]:
    # a plugin's servers run under the plugin's own runner (api/plugins.py)
    runs = {f.id: _connector(services, f) for f in features.BASE if f.mcp}
    runs["documents"] = _documents(services)
    return runs


part = Part(name="kb", runs=_runs)
