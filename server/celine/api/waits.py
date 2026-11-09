"""Waiting for you over HTTP (celine/waits.py): GET /api/waits, a logged-in session
only."""
from __future__ import annotations

from fastapi import APIRouter, Request

from .. import waits
from .deps import person_only

router = APIRouter(prefix="/api")


@router.get("/waits")
async def list_waits(request: Request) -> dict:
    """{items: everything that waits for you (waits.items), count: how many, unread: the
    Intercom threads with something you have not read (waits.unread)}."""
    person_only(request)
    services = request.app.state.services
    got = await waits.items(services)
    return {"items": got, "count": len(got), "unread": await services.store.read(waits.unread)}
