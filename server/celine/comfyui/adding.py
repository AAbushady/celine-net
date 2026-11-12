"""The one save of a preset the user adds from a workflow with placeholders: Settings,
Media's Upload workflow (api/presets.py) and a preset the agent proposed once the
user approved it (comfyui/proposals.py)."""
from __future__ import annotations

from typing import Any

from .. import reload
from ..settings import write
from . import presets as presets_mod


async def add(app, kind: str, label: str, text: str, values: dict[str, Any] | None = None,
              *, proposed: bool = False, replaces: str | None = None) -> presets_mod.Preset:
    """The preset checked and written as the user's (presets.drafted, presets.save). A new
    one is added to the first ComfyUI provider's models and at the end of its role;
    `replaces`: the id of the user's preset it is written over, which keeps its place.
    PresetError with every sentence, and nothing written. -> the preset."""
    async with write.lock():
        pid, data, graph = presets_mod.drafted(kind, label, text, values, pid=replaces,
                                               proposed=proposed)
        p = presets_mod.save(kind, pid, data, graph, "own")
        s = app.cfg.settings
        comfy = next((x for x, prov in (s.providers if s else {}).items()
                      if prov.get("kind") == "comfyui"), None)
        if replaces is None and comfy is not None:
            providers = {**s.providers, comfy: {**s.providers[comfy], "models": {
                **(s.providers[comfy].get("models") or {}),
                p.id: {"label": p.label, "caps": [p.role]}}}}
            role = s.roles.get(p.role) or {}
            roles = {**s.roles, p.role: {**role, "chain": [*(role.get("chain") or []),
                                                           f"{comfy}/{p.id}"]}}
            await reload.commit(app, {"providers": providers, "roles": roles})
        else:
            reload.apply(app)
        write.publish(app, ["presets"])
    return p
