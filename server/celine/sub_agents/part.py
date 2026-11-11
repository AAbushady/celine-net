"""Sub-agents as the start-up sees them (core/parts.py): the routes, the report a
sub-agent sends up when it is done, and the sub-agents that were queued before a restart."""
from __future__ import annotations

from ..core.parts import Part
from ..core.services import Services
from . import api, report
from .skill import release_all


def _wire(services: Services) -> None:
    async def done(cid: str, _turn: str | None) -> None:    # a sub-agent that is done reports
        await report.on_end(services, cid)
    services.dispatcher.on_end(done)


part = Part(name="sub_agents", routes=(api.router,), wire=_wire, start=release_all)
