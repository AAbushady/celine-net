"""The guard as the start-up sees it (core/parts.py): the routes, and Bastet's answers and
patrol while the guard is on."""
from __future__ import annotations

from ..core.parts import Part, Run
from ..core.services import Services
from . import api, patrol, store as decisions, talk


def _runs(services: Services) -> dict[str, Run]:
    async def run() -> None:
        await decisions.close_asked(services.store)    # its asks of the last run
        talk.start(services)                           # Bastet answers the agent on the Intercom
        services.dispatcher.work.notify()
        try:
            await patrol.run(services)
        finally:
            for kind in (talk.KIND, talk.NOTE):
                services.dispatcher.work.handlers.pop(kind, None)
    return {"guard": Run(run)}


part = Part(name="guard", routes=(api.router,), runs=_runs)
