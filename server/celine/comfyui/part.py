"""Images and music as the start-up sees them (core/parts.py): the routes, and the
batches while either is on."""
from __future__ import annotations

import anyio

from ..core.parts import Part, Run
from ..core.services import Services
from . import api, batches


def _runs(services: Services) -> dict[str, Run]:
    async def run() -> None:
        await batches.sweep(services.store)          # what a crash or restart left behind
        async with anyio.create_task_group() as g:
            services.batches = batches.Batches(services, g)
            try:
                await anyio.sleep_forever()
            finally:
                services.batches = None
    return {"media": Run(run, features=("images", "music"), early=True)}


part = Part(name="comfyui", routes=(api.router,), runs=_runs)
