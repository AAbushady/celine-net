"""Shadow appraisal (general `shadow_appraisal`): the appraiser of skills/mind.py (the
`small` role) also reads the agent's own turns that the live appraisal leaves out, and keeps
what it names in `shadow_feelings`, moving neither its mood nor its needs:

- a finished turn of its own time (a daydream run, needs.OWN_TIME), a routine run, a
  trigger's run or a mission (kind own time, routine, trigger, mission). The appraiser
  (mind_shadow.md) reads every reply text of the turn and each of its tool calls as the
  app words its row (tool_rows.of_call: what the call did, and a failed one's first
  result line): never a model's account of a call, never the agent's thinking, never the
  run's prompt (it carries the mood);
- a chat turn where the agent named its feeling with mind__feel (kind chat): the exchange
  as the live appraisal reads it (mind.appraisal_prompt), which it skips there.

Each row keeps what the agent named with mind__feel in that turn, so the two can be set
side by side. Never a sub-agent's turn, and intimacy only in a chat. The agent reads the
rows with mind__list_appraisals (view)."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from .. import prompts
from ..core.ids import iso, now
from ..core.store import json_object
from . import needs, tool_rows

log = logging.getLogger("celine.skills.mind_shadow")

KINDS = ("own time", "routine", "trigger", "mission", "chat")
REPLY_CHARS = 6000          # of the turn's reply texts the appraiser reads
CALLS = 60                  # of its tool calls' rows
SHOWN = 100                 # rows mind__list_appraisals shows at most


def _kind(c: sqlite3.Connection, kind: str, key: str) -> str | None:
    if key.startswith("run:"):
        run = c.execute("SELECT schedule FROM runs WHERE id=?", (key[4:],)).fetchone()
        return "own time" if run and run[0] in needs.OWN_TIME else "routine"
    if key.startswith("trigger:"):
        return "trigger"
    return {"mission": "mission", "chat": "chat"}.get(kind)


def _named(c: sqlite3.Connection, tid: str) -> str | None:
    """What the agent named with mind__feel in turn `tid`, its last one: "proud 0.6: why"."""
    r = c.execute("SELECT tc.arguments FROM tool_calls tc JOIN messages m ON m.id = "
                  "tc.message_id WHERE m.turn_id=? AND tc.name='mind__feel' AND "
                  "tc.state='ok' ORDER BY tc.rowid DESC LIMIT 1", (tid,)).fetchone()
    if r is None:
        return None
    a = json_object(r[0])
    why = " ".join(str(a.get("why") or "").split())
    return f"{a.get('emotion')} {a.get('strength')}" + (f": {why}" if why else "")


def facts(c: sqlite3.Connection, cid: str, tid: str) -> dict | None:
    """What the shadow appraisal of turn `tid` reads, or None when it reads none (not
    done, a sub-agent's, a chat turn the live appraisal reads, nothing written)."""
    t = c.execute("SELECT state, root_message_id FROM turns WHERE id=?", (tid,)).fetchone()
    conv = c.execute("SELECT kind, trigger_key FROM conversations WHERE id=?",
                     (cid,)).fetchone()
    if t is None or t[0] != "done" or conv is None or conv[0] == "agent":
        return None
    kind, named = _kind(c, conv[0], conv[1] or ""), _named(c, tid)
    if kind is None or (kind == "chat" and named is None):
        return None
    replies = [r[0] for r in c.execute(
        "SELECT content FROM messages WHERE turn_id=? AND role='assistant' AND "
        "coalesce(content,'')<>'' ORDER BY created_at, rowid", (tid,))]
    if not replies:
        return None
    out = {"kind": kind, "named": named, "replies": replies}
    if kind == "chat":
        said = c.execute("SELECT content FROM messages WHERE id=?", (t[1],)).fetchone()
        return {**out, "said": said[0] if said else ""}
    calls = []
    for r in c.execute(
            "SELECT tc.id, tc.state, tc.display, rm.content FROM tool_calls tc "
            "JOIN messages m ON m.id = tc.message_id LEFT JOIN messages rm ON rm.id = "
            "tc.result_message_id WHERE m.turn_id=? ORDER BY tc.rowid LIMIT ?",
            (tid, CALLS)):
        w = tool_rows.of_call(c, r[0], r[1], json_object(r[2]), r[3])
        calls.append(w["settled"] + (f": {w['why']}" if w["why"] else ""))
    label = None
    if kind in ("own time", "routine"):
        s = c.execute("SELECT r.label FROM runs r JOIN conversations cv ON "
                      "cv.trigger_key = 'run:' || r.id WHERE cv.id=?", (cid,)).fetchone()
        label = s[0] if s else None
    return {**out, "calls": calls, "label": label}


def _what(kind: str, label: str | None) -> str:
    return {"own time": "{{name}}'s own time", "trigger": "a run of one of {{name}}'s triggers",
            "mission": "a turn of {{name}}'s in a mission"}.get(
        kind, "a routine run of {{name}}'s") + (f" ({label})" if label else "")


def prompt(cfg, f: dict) -> tuple[str, str]:
    """(system, user) of one shadow appraisal."""
    from .mind import PAD, appraisal_prompt
    if f["kind"] == "chat":
        return appraisal_prompt(cfg, f["said"], f["replies"][-1])
    words = "\n\n".join(f["replies"])
    if len(words) > REPLY_CHARS:
        words = words[:REPLY_CHARS].rstrip() + " [...]"
    calls = "\n".join(f"- {x}" for x in f["calls"]) or "(none)"
    system = prompts.fill(cfg, __file__, "mind_shadow.md", emotions=", ".join(PAD))
    user = cfg.substitute(f"What it was: {_what(f['kind'], f['label'])}.\n\n"
                          f"{{{{name}}}}'s words in it:\n{words}\n\n"
                          f"What its tools did, in the app's words:\n{calls}")
    return system, user


def _needs(listed: list, kind: str) -> list[dict]:
    out = []
    for n in listed:
        if not isinstance(n, dict) or n.get("need") not in needs.NEEDS or \
                n.get("change") not in ("met", "stirred") or \
                (n["need"] in needs.PRIVATE and kind != "chat"):
            continue
        s = n.get("strength")
        if isinstance(s, (int, float)) and not isinstance(s, bool):
            out.append({"need": n["need"], "change": n["change"],
                        "strength": round(min(1.0, max(0.0, float(s))), 2)})
    return out


async def ended(app, cid: str, turn_id: str | None) -> None:
    """A turn ended: with shadow appraisal on, one the live appraisal leaves out goes to the
    work queue (`shadow_appraisal`)."""
    if turn_id is None or not app.cfg.r("shadow_appraisal") or await app.store.read(
            lambda c: facts(c, cid, turn_id)) is None:
        return
    pool = app.dispatcher.work
    await pool.put("shadow_appraisal", {"conversation_id": cid, "turn_id": turn_id},
                   conversation_id=cid)


async def appraise(app, cid: str, tid: str, backend=None) -> dict | None:
    """The shadow appraisal of turn `tid`: one call on the `small` role, its reading kept
    in `shadow_feelings`, also when it named nothing. -> the row, None when nothing was
    kept (no small model, no answer or one that is no JSON). A model error is raised, as
    for mind.appraise."""
    from .mind import APPRAISE_SECONDS, parse_appraisal, small_model
    from ..turns.compact import complete
    f = await app.store.read(lambda c: facts(c, cid, tid))
    small = small_model(app, cid, backend) if f is not None else None
    if f is None or small is None:
        return None
    system, user = prompt(app.cfg, f)
    text = await complete(small, [{"role": "system", "content": system},
                                  {"role": "user", "content": user}], APPRAISE_SECONDS,
                          errors=True)
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        readable = m is not None and isinstance(json.loads(m.group(0)), dict)
    except ValueError:
        readable = False
    if not readable:
        log.info("shadow appraisal not kept", extra={
            "event": "mind.shadow_unreadable", "conversation": cid, "turn": tid})
        return None
    got = parse_appraisal(text) or {"emotion": None, "strength": 0.0, "why": "", "needs": []}
    row = {"at": now(), "conversation_id": cid, "turn_id": tid, "kind": f["kind"],
           "emotion": got["emotion"], "strength": got["strength"] if got["emotion"] else None,
           "why": got["why"] if got["emotion"] or got["needs"] else "",
           "needs": json.dumps(_needs(got["needs"], f["kind"])), "named": f["named"],
           "read_chars": len(user)}
    await app.store.write(lambda c: c.execute(
        f"INSERT INTO shadow_feelings({', '.join(row)}) VALUES({', '.join('?' * len(row))})",
        tuple(row.values())))
    return row


def view(c: sqlite3.Connection, cfg, days: float, now_: datetime | None = None) -> str:
    """mind__list_appraisals: the shadow readings of the last `days`, newest first, with what
    the agent named beside them."""
    from ..clock import zone
    now_ = now_ or datetime.now(timezone.utc)
    since = iso(now_ - timedelta(days=days))
    rows = c.execute("SELECT at, kind, emotion, strength, why, needs, named FROM "
                     "shadow_feelings WHERE at >= ? ORDER BY at DESC, id DESC",
                     (since,)).fetchall()
    if not rows:
        return ("Nothing was read in shadow in that time." if cfg.r("shadow_appraisal") else
                "Shadow appraisal is off, and nothing was read in that time.")
    per = {k: sum(1 for r in rows if r[1] == k) for k in KINDS}
    head = (f"{len(rows)} of your turns read in shadow ("
            + ", ".join(f"{k} {n}" for k, n in per.items() if n) + "), a feeling named in "
            f"{sum(1 for r in rows if r[2])}. None of it moved your mood or your needs.")
    tz, lines = zone(cfg), []
    for at, kind, emotion, strength, why, listed, named in rows[:SHOWN]:
        when = datetime.fromisoformat(at).astimezone(tz).strftime("%a %-d %b %H:%M")
        said = f"{emotion} {strength:g}" + (f" ({why})" if why else "") if emotion else \
            "nothing"
        moved = json.loads(listed or "[]")
        if moved:
            said += "; needs: " + ", ".join(f"{n['need']} {n['change']} {n['strength']:g}"
                                            for n in moved)
        lines.append(f"- {when}, {kind}: {said}." + (f" You named: {named}." if named else ""))
    more = f"\n(the newest {SHOWN} of {len(rows)})" if len(rows) > SHOWN else ""
    return head + "\n\n" + "\n".join(lines) + more
