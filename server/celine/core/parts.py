"""What a feature hands to the start-up: its part.

The start-up (__main__.py, the composition root) builds the services and knows the parts
and their order, and a part knows its own feature. The phases are the start-up's ordering
rule, kept in one place:

- `routes`: its routers, added when the app is built.
- `wire`: runs once every service exists and before the turn workers start. What must
  hear every turn's end is attached here (`dispatcher.on_end`): a turn resumed at start
  that ends while the start-up still runs is heard too.
- `runs`: what runs while a feature is on, by the feature's id (core/runners.py starts
  and stops each with its switch). Asked once, after `wire` and before the workers. An
  `early` one starts before the workers; the others start after every part's `start`.
- `start`: runs after the workers and the intercom's timer started. Work that queues or
  starts turns goes here."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter

from .services import Services


@dataclass(frozen=True)
class Run:
    what: Callable[[], Awaitable[Any]]
    features: tuple[str, ...] = ()      # on while any of them is on; () its own id
    on_stop: Callable[[], Awaitable[Any]] | None = None   # after its switch went off
    early: bool = False                 # starts before the turn workers


@dataclass(frozen=True)
class Part:
    name: str
    routes: tuple[APIRouter, ...] = ()
    wire: Callable[[Services], None] | None = None
    runs: Callable[[Services], Mapping[str, Run]] | None = None
    start: Callable[[Services], Awaitable[None]] | None = None
