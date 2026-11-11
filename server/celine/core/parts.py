"""What a feature hands to the start-up: its part.

The start-up (__main__.py, the composition root) knows the parts and their order, and a
part knows its own feature. The phases are the start-up's ordering rule, kept in one
place:

- `routes`: its routers, added when the app is built.
- `wire`: runs once every service exists and before the turn workers start. What must
  hear every turn's end is attached here (`dispatcher.on_end`): a turn resumed at start
  that ends while the start-up still runs is heard too.
- `start`: runs after the workers and the intercom's timer started. Work that queues or
  starts turns goes here.

A feature moves here from the start-up function when its routes take what they use as
typed parameters (core/route_params.py)."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import APIRouter

from .services import Services


@dataclass(frozen=True)
class Part:
    name: str
    routes: tuple[APIRouter, ...] = ()
    wire: Callable[[Services], None] | None = None
    start: Callable[[Services], Awaitable[None]] | None = None
