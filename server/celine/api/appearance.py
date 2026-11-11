"""Settings, Appearance: GET and PUT /api/appearance (; the
choices in settings/appearance.py APPEARANCE, stored in var/settings/appearance.toml). Every
key comes back with its effective value; a PUT takes the keys to change (null: back to its
default, so the defaults stay the server's), and a save sends
`settings.changed` (what: ["appearance"]) so every open page shows it at once."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..settings import appearance as appearance_mod, files
from ..settings import write
from .deps import json_body
from ..core.route_params import TheConfig, TheServices

router = APIRouter(prefix="/api")


def _root(cfg):
    return files.settings_dir(cfg)


@router.get("/appearance")
async def get_appearance(cfg: TheConfig) -> dict:
    return {"appearance": appearance_mod.effective(appearance_mod.read(_root(cfg))),
            "help": appearance_mod.HELP}


@router.put("/appearance")
async def put_appearance(request: Request, cfg: TheConfig, services: TheServices) -> dict:
    body = await json_body(request, need=("appearance",), allow=("appearance",))
    change = appearance_mod.validate(body["appearance"])
    async with write.lock():
        root = _root(cfg)
        saved = {k: v for k, v in {**appearance_mod.read(root), **change}.items() if v is not None}
        files.write(root, "appearance", saved)
    write.publish(services, ["appearance"])
    return {"appearance": appearance_mod.effective(saved)}
