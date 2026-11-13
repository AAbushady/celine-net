"""Feeling probes: a server running the affect patch reads directions of feeling from the
agent's own activations while it writes, and sends the readings with the reply. A provider
with Feeling probes on (its `affect` option, settings/registry.py) gets
`"affect": {"probe": PROBES}` on every request of the agent's own turns (turns/dispatcher.py
binds them with `affect=True`). The reply's top-level `affect` ({probe: {name: {mean, last}},
tokens, layers, poles}) comes back in Done.raw, and `keep` stores the readings, one row per
reply message (affect_readings). Nothing reads them back into the agent yet: they are its
baseline."""
from __future__ import annotations

import json
import math
from typing import Any

from .core.ids import now

# every direction the patch has; longing reads unreliably and stays out of what acts on them
PROBES = ("valence", "arousal", "dominance", "tired", "lonely", "longing")


def body() -> dict[str, Any]:
    """The request field that asks for the readings."""
    return {"affect": {"probe": list(PROBES)}}


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


async def keep(store, cid: str, turn_id: str | None, mid: str, raw: dict | None) -> None:
    """Store the readings of reply `mid`, when its raw answer has them."""
    got = readings(raw)
    if got is None:
        return
    values, tokens = got
    model = str((raw or {}).get("model") or "")
    await store.write(lambda c: c.execute(
        "INSERT OR REPLACE INTO affect_readings(message_id, conversation_id, turn_id, at, "
        "model, tokens, readings) VALUES(?,?,?,?,?,?,?)",
        (mid, cid, turn_id, now(), model, tokens, json.dumps(values))))
