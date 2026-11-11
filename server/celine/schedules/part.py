"""Routines as the start-up sees them (core/parts.py): the schedules' and the triggers'
routes, and what runs while Routines is on."""
from __future__ import annotations

import anyio

from ..core.parts import Part, Run
from ..core.services import Services
from ..triggers import Triggers, api as triggers_api
from . import api
from .runs import Scheduler


def _runs(services: Services) -> dict[str, Run]:
    scheduler = Scheduler(services)          # made once: it hears every turn's end

    async def run() -> None:                 # the skills' triggers, then the schedules
        try:
            async with anyio.create_task_group() as g:
                services.triggers = Triggers(services)
                await services.triggers.run(g)
                services.schedules = scheduler
                await scheduler.run(g)
                await anyio.sleep_forever()
        finally:
            services.triggers = services.schedules = None
    return {"routines": Run(run, on_stop=scheduler.forget)}


part = Part(name="routines", routes=(api.router, triggers_api.router), runs=_runs)
