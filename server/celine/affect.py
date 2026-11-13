"""Feeling probes and steering: a server running the affect patch reads directions of feeling
from the agent's own activations while it writes, sends the readings with the reply, and can
add those directions to them (steering). A provider with Feeling probes on (its `affect`
option, settings/registry.py) gets `"affect": {"probe": PROBES}` on every request of the
agent's own turns (turns/dispatcher.py binds them with `fields`); with Feeling steering on too
(`affect_steer`), the request also carries `"steer": [{name, scale}]`, the pushes of its mood
and needs then (`steer`). The reply's top-level `affect` ({probe: {name: {mean, last}},
tokens, layers, poles}) comes back in Done.raw, and `keep` stores the readings with what was
steered, one row per reply message (affect_readings). Nothing reads them back into the agent
yet."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

from .core.ids import now

# every direction the patch has; longing reads unreliably and stays out of what acts on them
PROBES = ("valence", "arousal", "dominance", "tired", "lonely", "longing")
# the server's calibration: 0.25 to 0.5 a tint, 1.0 unmistakable, past it her words degrade
STEER_MOST = 0.75           # the most one push is
DOMINANCE_MOST = 0.5        # dominance bends her sooner than the rest
STEER_LEAST = 0.1           # a smaller push is left out
# the need behind each push of a need; a need steers only above where needs start
NEED_PUSHES = (("rest", "tired"), ("connection", "lonely"))


def steer(point: tuple[float, ...] | None, levels: dict[str, float]) -> list[dict[str, Any]]:
    """The pushes the agent's state gives its reply. Its mood (a PAD point, None without a
    feeling yet) outside the neutral middle pushes valence, arousal and dominance by its
    coordinates; a need above where needs start pushes its direction by how far it is above
    (rest: tired, connection: lonely). Each at most STEER_MOST (dominance DOMINANCE_MOST); a
    push under STEER_LEAST is left out; longing is never pushed."""
    from .skills import needs
    from .skills.mind import NEUTRAL
    out: dict[str, float] = {}
    if point is not None and math.dist(point, (0.0, 0.0, 0.0)) >= NEUTRAL:
        p, a, d = point
        out["valence"], out["arousal"] = p * STEER_MOST, a * STEER_MOST
        out["dominance"] = max(-DOMINANCE_MOST, min(DOMINANCE_MOST, d * STEER_MOST))
    for need, name in NEED_PUSHES:
        level = levels.get(need)
        if level is not None and level > needs.START:
            out[name] = (level - needs.START) / (1 - needs.START) * STEER_MOST
    return [{"name": n, "scale": round(max(-STEER_MOST, min(STEER_MOST, s)), 3)}
            for n, s in out.items() if abs(s) >= STEER_LEAST]


async def fields(store, cid: str, steering: bool,
                 now_: datetime | None = None) -> dict[str, Any]:
    """The request field of one of the agent's own replies in conversation `cid`: the probes,
    and with `steering` the pushes of its state now (an empty list is left out)."""
    ask: dict[str, Any] = {"probe": list(PROBES)}
    if steering:
        from .skills.mind import point_and_levels
        from .skills.places import place_of
        place = await store.read(lambda c: place_of(c, cid))
        point, levels = await point_and_levels(store, now_ or datetime.now(timezone.utc), place)
        if pushes := steer(point, levels):
            ask["steer"] = pushes
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
