"""The LLM request queue, for the UI: what runs and what waits, per model."""
from __future__ import annotations

from fastapi import APIRouter

from ..core.event_types import LLM_QUEUE
from ..core.route_params import TheServices

router = APIRouter(prefix="/api")


@router.get("/llm-queue")
async def queue(services: TheServices) -> dict:
    """The model queue as it is now: {type, running, waiting} (Queue.snapshot)."""
    llm = services.llm
    if llm is None:
        return {"type": LLM_QUEUE, "running": [], "waiting": []}
    return llm.scheduler.snapshot()


@router.get("/loaded-models")
async def loaded(services: TheServices) -> dict:
    """What the model servers had loaded when the app last asked them (a model's first
    answer, a load, Check provider); nothing is asked here: {models: [{ref, loaded,
    loading, load_seconds}]} (backends/router.py loaded_view). `model.loaded` says the same when
    it changes."""
    llm = services.llm
    if llm is None:
        return {"models": []}
    return llm.router.loaded_view()
