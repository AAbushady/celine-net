"""Conversations as the start-up sees them (core/parts.py): the routes, and how the
message tree learns whether a chat's model continues a reply in place."""
from __future__ import annotations

from ..core.parts import Part
from ..core.services import Services
from . import api, tree


def _wire(services: Services) -> None:
    tree.prefills = lambda cid: api.prefills(services, cid)


part = Part(name="conversations", routes=(api.router,), wire=_wire)
