"""What a route takes as a typed parameter.

    async def list_jobs(store: TheStore, cfg: TheConfig, state: str | None = None) -> ...

FastAPI fills each from the running app's services (core/services.py). A route then
reaches for nothing through the request, and mypy holds its body to these types.
`store_of`, `cfg_of` and `services_of` give the same from a request, for the code that
has only the request (a helper, a socket)."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from ..config import Config
from .services import Services
from .store import Store


def services_of(request: Request) -> Services:
    return request.app.state.services


def cfg_of(request: Request) -> Config:
    return request.app.state.services.cfg


def store_of(request: Request) -> Store:
    return request.app.state.services.store


TheServices = Annotated[Services, Depends(services_of)]
TheConfig = Annotated[Config, Depends(cfg_of)]
TheStore = Annotated[Store, Depends(store_of)]
