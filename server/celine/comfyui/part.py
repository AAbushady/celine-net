"""Images and music as the start-up sees them (core/parts.py): the routes, the batches
while either is on, and the model files the agent asked for while Images is on."""
from __future__ import annotations

import anyio

from ..core.parts import Part, Run
from ..core.services import Services
from . import api, batches, model_files


def _runs(services: Services) -> dict[str, Run]:
    async def run() -> None:
        await batches.sweep(services.store)          # what a crash or restart left behind
        async with anyio.create_task_group() as g:
            services.batches = batches.Batches(services, g)
            try:
                await anyio.sleep_forever()
            finally:
                services.batches = None

    async def files() -> None:
        # after the workers: a request it settles at the start sends a notice that wakes a turn
        async with anyio.create_task_group() as g:
            services.model_files = model_files.ModelFiles(services, g)
            try:
                await services.model_files.resume()
                await anyio.sleep_forever()
            finally:
                services.model_files = None
    return {"media": Run(run, features=("images", "music", "video"), early=True),
            "model_files": Run(files, features=("images",))}


part = Part(name="comfyui", routes=(api.router,), runs=_runs)
