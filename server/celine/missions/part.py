"""Missions as the start-up sees them (core/parts.py): the routes, the rule that keeps a
mission going at each turn's end, and the check-ins while Missions is on."""
from __future__ import annotations

from ..core.parts import Part, Run
from ..core.services import Services
from . import api, watch


def _runs(services: Services) -> dict[str, Run]:
    return {"missions": Run(lambda: watch.run(services))}


part = Part(name="missions", routes=(api.router,), wire=watch.start, runs=_runs)
