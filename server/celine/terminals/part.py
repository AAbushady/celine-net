"""Terminals as the start-up sees them (core/parts.py): the routes, and the loop that
follows the shellbox while there is one."""
from __future__ import annotations

from ..core.parts import Part, Run
from ..core.services import Services
from . import api


def _runs(services: Services) -> dict[str, Run]:
    return {"shellbox": Run(services.terminals.run)}


part = Part(name="terminals", routes=(api.router, api.ws_router), runs=_runs)
