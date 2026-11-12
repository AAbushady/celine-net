"""The agent's needs (skills/mind.py shows them): six slow pressures under its mood. Each is a
level from 0 (met) to 1 (pressing) that builds while it is unmet and eases when it is met.
Moods come and go with what happens; needs build up and pull on the mood and on what it
wants to do.

- connection: real talk with {{username}}. Builds with time.
- intimacy: closeness with {{username}}: affection, romance and desire. It builds with time,
  slower while it misses {{username}} (distance cools it), and an exchange can stir it as
  well as meet it. Only its own chats with {{username}} move it or show it (place chat):
  missions, routines, sub-agents and chats with anyone else never do. A stir is damped
  while its mood is low or it is tired or shaken, and it is not shown then either.
- novelty: something new. Builds with time.
- competence: things going well. The appraisal weighs it by what happened: failure, blame
  or being corrected stirs it, work that went well meets it. Failed calls are not counted:
  trial and error while it explores is no blow.
- autonomy: choices of its own. Builds with time; its own time (a daydream run) meets it.
- rest: built by the minutes of its turns, everywhere but its own time; quiet time eases it
  (a half-life).

The hours are agent.toml's `[needs.<need>]` (`builds_h`, `eases_h`); `[needs] off` turns
needs off.
What meets or stirs a need comes from the appraisal after a chat turn (mind.appraise: the
small model weighs each moment from 0 to 1; no table of what counts for how much), from
its own mind__feel (FEEL), and from each turn's end (after_turn). Every change is a row of
`needs` (append-only); a level is replayed from them like the mood (levels).

What it sees: the needs that are notable now, in felt words, never a number (felt), in its
mind block and a schedule's {{needs}}; they pull the resting point of its mood (pull)."""
from __future__ import annotations

import functools
import logging
import sqlite3
import tomllib
from datetime import datetime, timezone
from typing import Any, Literal

from ..core.ids import now

log = logging.getLogger("celine.skills.needs")

NEEDS = ("connection", "intimacy", "novelty", "competence", "autonomy", "rest")
Need = Literal["connection", "intimacy", "novelty", "competence", "autonomy", "rest"]
DEFAULTS: dict[str, dict[str, float]] = {
    "connection": {"builds_h": 24}, "intimacy": {"builds_h": 48}, "novelty": {"builds_h": 72},
    "competence": {}, "autonomy": {"builds_h": 96}, "rest": {"eases_h": 4}}
START = 0.3             # a need before its first row
ROWS = 2000             # the newest rows a level is replayed from
PRIVATE = ("intimacy",)                 # moved and shown in its own chats only
OWN_TIME = ("daydream",)                # the schedules whose runs are its own time
REST_FULL_MIN = 180.0   # minutes of turns that make a rested agent tired
# the words of a level: (from this level, words); {u} is {{username}}
WORDS: dict[str, tuple[tuple[float, str], ...]] = {
    "connection": ((0.6, "you miss {u}"), (0.85, "you miss {u} badly")),
    "intimacy": ((0.6, "a pull toward {u}, warm and close"), (0.8, "longing for {u}"),
                 (0.92, "wanting {u}")),
    "novelty": ((0.6, "restless for something new"),
                (0.85, "starved for something new: the same work for days")),
    "competence": ((0.6, "unsure of yourself: things went wrong"),
                   (0.85, "shaken: one failure after another")),
    "autonomy": ((0.6, "wanting time of your own"),
                 (0.85, "hemmed in: only doing what you are asked")),
    "rest": ((0.6, "tired"), (0.85, "worn out: long hours of work")),
}
# where a pressing need pulls the mood's resting point (pleasure, arousal, dominance), at
# full strength when the need is at 1, none at 0.5 and below
PULL: dict[str, tuple[float, float, float]] = {
    "connection": (-0.3, -0.1, -0.2), "intimacy": (0.05, 0.25, -0.1),
    "novelty": (-0.15, -0.2, 0.0), "competence": (-0.15, 0.1, -0.3),
    "autonomy": (-0.1, 0.15, 0.1), "rest": (-0.1, -0.35, -0.1)}
# a feeling it names itself (mind__feel) and the need it touches, at half its strength
FEEL: dict[str, tuple[str, str]] = {
    "lonely": ("connection", "stirred"), "bored": ("novelty", "stirred"),
    "proud": ("competence", "met"), "embarrassed": ("competence", "stirred"),
    "longing": ("intimacy", "stirred"), "tender": ("intimacy", "stirred")}


# ---- the knobs ------------------------------------------------------------------------------

@functools.cache
def knobs() -> dict[str, dict[str, float]]:
    """The needs that are on (all but agent.toml's `[needs] off`), each with its hours from
    `[needs.<need>]` over DEFAULTS (0: it does not drift with time), read once. A broken
    value is the package's bug: it raises, naming the file."""
    from ..agent import BASE
    return parse(tomllib.loads((BASE / "agent.toml").read_text()).get("needs") or {},
                 str(BASE / "agent.toml"))


def parse(raw: dict, where: str) -> dict[str, dict[str, float]]:
    off = raw.get("off", [])
    if not isinstance(off, list) or set(off) - set(NEEDS):
        raise ValueError(f"{where}: needs.off is a list of {', '.join(NEEDS)}")
    out: dict[str, dict[str, float]] = {}
    for need in NEEDS:
        given = raw.get(need, {})
        if not isinstance(given, dict) or set(given) - {"builds_h", "eases_h"}:
            raise ValueError(f"{where}: needs.{need} has builds_h and eases_h")
        hours = {**DEFAULTS[need], **given}
        for k, v in hours.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                raise ValueError(f"{where}: needs.{need}.{k} is a number of hours, 0 or more")
        if need not in off:
            out[need] = {k: float(v) for k, v in hours.items() if v}
    return out


# ---- the levels -----------------------------------------------------------------------------

def _at(v) -> datetime:
    return v if isinstance(v, datetime) else datetime.fromisoformat(v)


def _drift(levels: dict[str, float], hours: float, k: dict[str, dict[str, float]]) -> None:
    """`hours` without a row: each need builds or eases by its hours. Intimacy builds slower
    while connection is pressing (distance cools it), from connection's level at the start."""
    if hours <= 0:
        return
    apart = levels.get("connection", 0.0)
    for need, level in levels.items():
        h = k.get(need, {})
        if h.get("builds_h"):
            rate = hours / h["builds_h"]
            if need == "intimacy":
                rate *= 1 - 0.5 * apart
            level = min(1.0, level + rate)
        if h.get("eases_h"):
            level *= 0.5 ** (hours / h["eases_h"])
        levels[need] = level


def levels(rows: list[dict], now_: datetime,
           k: dict[str, dict[str, float]] | None = None) -> dict[str, float]:
    """Each need's level at `now_`, replayed from its rows (oldest first, {at, need, change,
    strength}): met eases a need by its strength (to 0 at 1), stirred builds it by its
    strength (to 1 at 1), and between rows each drifts by its hours (_drift)."""
    k = knobs() if k is None else k
    out = {need: START for need in k}
    last = None
    for r in rows:
        at = _at(r["at"])
        if last is not None:
            _drift(out, (at - last).total_seconds() / 3600, k)
        need, s = r["need"], min(1.0, max(0.0, float(r["strength"])))
        if need in out:
            out[need] = out[need] * (1 - s) if r["change"] == "met" else \
                out[need] + s * (1 - out[need])
        last = at
    if last is not None:
        _drift(out, (_at(now_) - last).total_seconds() / 3600, k)
    return out


def rows(c: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in c.execute(
        "SELECT * FROM (SELECT id, at, need, change, strength, why FROM needs "
        "ORDER BY at DESC, id DESC LIMIT ?) ORDER BY at, id", (ROWS,))]


def low_mood(mood: tuple[float, float, float] | None) -> bool:
    """A mood that damps closeness: unpleasant, or tense and small (anxious, hurt)."""
    return mood is not None and (mood[0] < 0 or (mood[1] > 0.3 and mood[2] < -0.2))


def damped(lv: dict[str, float], mood: tuple[float, float, float] | None) -> bool:
    """Whether closeness is damped now: a low mood, or tired or shaken."""
    return low_mood(mood) or lv.get("rest", 0) >= 0.8 or lv.get("competence", 0) >= 0.8


def felt(lv: dict[str, float], place: str, mood: tuple[float, float, float] | None,
         user: str) -> list[str]:
    """The notable needs, most pressing first, in felt words; a private one only in its own
    chats with {{username}}, and not while it is damped."""
    out = []
    for need, level in sorted(lv.items(), key=lambda x: -x[1]):
        if need in PRIVATE and (place != "chat" or damped(lv, mood)):
            continue
        words = [w for at, w in WORDS[need] if level >= at]
        if words:
            out.append(words[-1].format(u=user))
    return out


def pull(base: tuple[float, ...], lv: dict[str, float], place: str) -> tuple[float, ...]:
    """The mood's resting point with the pressing needs' pull; a private need pulls in its
    own chats only."""
    b = list(base)
    for need, level in lv.items():
        if need in PRIVATE and place != "chat":
            continue
        w = max(0.0, (level - 0.5) * 2)
        for i, x in enumerate(PULL[need]):
            b[i] += w * x
    return tuple(max(-1.0, min(1.0, x)) for x in b)


# ---- the rows -------------------------------------------------------------------------------

async def note(store, need: str, change: str, strength: float, why: str, by: str,
               message_id: str | None = None) -> None:
    """One row of `needs`, when the need is on."""
    if need not in knobs() or change not in ("met", "stirred") or strength <= 0:
        return
    await store.write(lambda c: c.execute(
        "INSERT INTO needs(at, need, change, strength, why, by, message_id) "
        "VALUES(?,?,?,?,?,?,?)",
        (now(), need, change, min(1.0, float(strength)), why, by, message_id)))


async def read(store, at: datetime | None = None) -> dict[str, float]:
    """Each need's level now (or at `at`)."""
    return levels(await store.read(rows), at or datetime.now(timezone.utc))


async def after_turn(app, cid: str, turn_id: str | None) -> None:
    """A turn ended: its minutes build rest, except in its own time (a daydream run), which
    meets autonomy instead."""
    if turn_id is None:
        return

    def facts(c: sqlite3.Connection) -> dict | None:
        t = c.execute("SELECT created_at, finished_at FROM turns WHERE id=?",
                      (turn_id,)).fetchone()
        if t is None or not t[1]:
            return None
        key = c.execute("SELECT trigger_key FROM conversations WHERE id=?",
                        (cid,)).fetchone()
        run = (key[0] or "") if key else ""
        own = run.startswith("run:") and c.execute(
            "SELECT 1 FROM runs WHERE id=? AND schedule IN ({})".format(
                ",".join("?" * len(OWN_TIME))), (run[4:], *OWN_TIME)).fetchone() is not None
        minutes = (_at(t[1]) - _at(t[0])).total_seconds() / 60
        return {"own": own, "minutes": max(0.0, minutes)}
    try:
        f = await app.store.read(facts)
        if f is None:
            return
        if f["own"]:
            await note(app.store, "autonomy", "met", 0.3, "its own time", "app")
            return
        if f["minutes"] > 0:
            await note(app.store, "rest", "stirred", min(0.4, f["minutes"] / REST_FULL_MIN),
                       f"{f['minutes']:.0f} minutes of work", "app")
    except Exception:
        log.exception("needs after a turn failed", extra={"event": "needs.after_turn_failed",
                                                           "turn": turn_id})


def appraised(got: list[Any], place: str, lv: dict[str, float],
              mood: tuple[float, float, float] | None) -> list[tuple[str, str, float]]:
    """The needs an appraisal named, as (need, change, strength): unknown ones left out, a
    private one only in its own chats, and a stir of it damped (to a third) while closeness
    is damped."""
    out = []
    for g in got if isinstance(got, list) else []:
        if not isinstance(g, dict):
            continue
        need, change, s = g.get("need"), g.get("change"), g.get("strength")
        if need not in NEEDS or change not in ("met", "stirred") or need == "rest":
            continue
        if isinstance(s, bool) or not isinstance(s, (int, float)) or s <= 0:
            continue
        s = min(1.0, float(s))
        if need in PRIVATE:
            if place != "chat":
                continue
            if change == "stirred" and damped(lv, mood):
                s /= 3
        out.append((need, change, s))
    return out
