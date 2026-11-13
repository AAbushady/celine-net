"""Feeling probes and steering: a server running the affect patch reads directions of feeling
from the agent's own activations while it writes, sends the readings with the reply, and can
add those directions to them (steering). A provider with Feeling probes on (its `affect`
option, settings/registry.py) gets `"affect": {"probe": PROBES}` on every request of the
agent's own turns (turns/dispatcher.py binds them with `fields`); with Feeling steering on too
(`affect_steer`), the request also carries `"steer": [{name, scale}]`, the pushes of its mood
and needs then (skills/mind.py `pushes`, which its [your mind] block names on that turn). The
reply's top-level `affect` ({probe: {name: {mean, last}},
tokens, layers, poles}) comes back in Done.raw, and `keep` stores the readings with what was
steered, one row per reply message (affect_readings). Nothing reads them back into the agent
yet."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

from .core.ids import now

# the directions read: the patch's longing is never asked for, at the agent's own request
# (it read unreliably, and what is not read cannot creep into anything)
PROBES = ("valence", "arousal", "dominance", "tired", "lonely")


async def fields(store, cid: str, steering: bool,
                 now_: datetime | None = None) -> dict[str, Any]:
    """The request field of one of the agent's own replies in conversation `cid`: the probes,
    and with `steering` the pushes of its state now (an empty list is left out)."""
    ask: dict[str, Any] = {"probe": list(PROBES)}
    if steering:
        from .skills.mind import point_and_levels, pushes
        from .skills.places import place_of
        place = await store.read(lambda c: place_of(c, cid))
        point, levels = await point_and_levels(store, now_ or datetime.now(timezone.utc), place)
        if given := pushes(point, levels):
            ask["steer"] = given
    return {"affect": ask}


def _number(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def readings(raw: dict | None) -> tuple[dict[str, Any], int] | None:
    """The readings of a reply's raw answer and the tokens they cover, None when it has
    none. A probe the server could not compute is null."""
    a = (raw or {}).get("affect")
    if not isinstance(a, dict) or not isinstance(a.get("probe"), dict):
        return None
    probe = a["probe"]
    out: dict[str, Any] = {}
    for name in PROBES:
        r = probe.get(name)
        mean = _number(r.get("mean")) if isinstance(r, dict) else None
        last = _number(r.get("last")) if isinstance(r, dict) else None
        out[name] = None if mean is None and last is None else {"mean": mean, "last": last}
    tokens = a.get("tokens")
    return out, tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else 0


async def keep(store, cid: str, turn_id: str | None, mid: str, raw: dict | None,
               sent: dict | None = None) -> None:
    """Store the readings of reply `mid`, when its raw answer has them, with the pushes its
    request carried (`sent`: the request's `affect` field)."""
    got = readings(raw)
    if got is None:
        return
    values, tokens = got
    model = str((raw or {}).get("model") or "")
    pushes = (sent or {}).get("steer") or []
    await store.write(lambda c: c.execute(
        "INSERT OR REPLACE INTO affect_readings(message_id, conversation_id, turn_id, at, "
        "model, tokens, readings, steer) VALUES(?,?,?,?,?,?,?,?)",
        (mid, cid, turn_id, now(), model, tokens, json.dumps(values), json.dumps(pushes))))
