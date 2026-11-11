"""The browser as the start-up sees it (core/parts.py): the routes, and the watch that
tells the pages its state while the browser is on."""
from __future__ import annotations

from ..core.parts import Part, Run
from ..core.services import Services
from . import api, sync


def _runs(services: Services) -> dict[str, Run]:
    return {"browser": Run(lambda: sync.watch(services.cfg, services.store))}


part = Part(name="browser", routes=(api.router, api.ws_router), runs=_runs)
