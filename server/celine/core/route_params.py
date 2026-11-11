"""What a route takes as a typed parameter.

    async def list_jobs(store: TheStore, cfg: TheConfig, state: str | None = None) -> ...

FastAPI fills each from the running app's services (core/services.py), for a socket
route as for an HTTP one. A route then reaches for nothing through the request, and mypy
holds its body to these types. `store_of`, `cfg_of` and `services_of` give the same from
a request or a socket, for the code that has only that (the login, a plugin's route)."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from starlette.requests import HTTPConnection

from ..config import Config
from .services import Services
from .store import Store


def services_of(conn: HTTPConnection) -> Services:
    return conn.app.state.services


def cfg_of(conn: HTTPConnection) -> Config:
    return conn.app.state.services.cfg


def store_of(conn: HTTPConnection) -> Store:
    return conn.app.state.services.store


TheServices = Annotated[Services, Depends(services_of)]
TheConfig = Annotated[Config, Depends(cfg_of)]
TheStore = Annotated[Store, Depends(store_of)]
