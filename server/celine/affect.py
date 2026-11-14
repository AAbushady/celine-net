"""Feeling probes and steering: a server running the affect patch reads directions of feeling
from the agent's own activations while it writes, sends the readings with the reply, and can
add those directions to them (steering). A provider with Feeling probes on (its `affect`
option, settings/registry.py) gets `"affect": {"probe": [...]}` on every request of the
agent's own turns (turns/dispatcher.py binds them with `fields`), the names its Probes option
lists (PROBES as it came; the server refuses a name it does not have). With Feeling steering
on too (`affect_steer`), the request also carries `"steer": [{name, scale}]`, the pushes of
its mood and needs then (skills/mind.py `pushes`, which its [your mind] block names on that
turn). The reply's top-level `affect` ({probe: {name: {mean, last, text}}, tokens,
text_tokens, layers, poles}) comes back in Done.raw, and `keep` stores the readings of the
probes asked, each with every summary its server sent, with what was steered, one row per
reply message (affect_readings). Nothing reads them back into the agent: they are raw
values."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

from .core.ids import now

# the directions read as the Probes setting comes: the patch's longing is never asked for, at
# the agent's own request (it read unreliably, and what is not read cannot creep into anything)
PROBES = ("valence", "arousal", "dominance", "tired", "lonely")


async def fields(store, cid: str, steering: bool, probes: list[str] | tuple[str, ...] = PROBES,
                 now_: datetime | None = None) -> dict[str, Any]:
    """The request field of one of the agent's own replies in conversation `cid`: the probes
    its provider reads, and with `steering` the pushes of its state now (an empty list is
    left out)."""
    ask: dict[str, Any] = {"probe": list(probes)}
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


def _count(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _summaries(r: Any) -> dict[str, float | None] | None:
    """One probe's readings as its server sent them: every summary (mean, last, text, ...)
    that is a number or null, a value that is no finite number null. None when not one of
    them is a number."""
    if not isinstance(r, dict):
        return None
    got = {str(k): _number(v) for k, v in r.items()
           if v is None or (isinstance(v, (int, float)) and not isinstance(v, bool))}
    return got if any(v is not None for v in got.values()) else None


def readings(raw: dict | None, asked: list[str] | tuple[str, ...] = PROBES
             ) -> tuple[dict[str, Any], int, int | None] | None:
    """The readings of a reply's raw answer for the probes `asked`, the tokens they cover,
    and the tokens of its visible text (None when the server does not say); None when it
    has none. A probe the server could not compute is null; one not asked is not kept."""
    a = (raw or {}).get("affect")
    if not isinstance(a, dict) or not isinstance(a.get("probe"), dict):
        return None
    probe = a["probe"]
    out = {name: _summaries(probe.get(name)) for name in asked}
    return out, _count(a.get("tokens")) or 0, _count(a.get("text_tokens"))


async def keep(store, cid: str, turn_id: str | None, mid: str, raw: dict | None,
               sent: dict | None = None) -> None:
    """Store the readings of reply `mid`, when its raw answer has them, with the pushes its
    request carried (`sent`: the request's `affect` field, its probes the ones kept)."""
    got = readings(raw, (sent or {}).get("probe") or PROBES)
    if got is None:
        return
    values, tokens, text_tokens = got
    model = str((raw or {}).get("model") or "")
    pushes = (sent or {}).get("steer") or []
    await store.write(lambda c: c.execute(
        "INSERT OR REPLACE INTO affect_readings(message_id, conversation_id, turn_id, at, "
        "model, tokens, text_tokens, readings, steer) VALUES(?,?,?,?,?,?,?,?,?)",
        (mid, cid, turn_id, now(), model, tokens, text_tokens, json.dumps(values),
         json.dumps(pushes))))
