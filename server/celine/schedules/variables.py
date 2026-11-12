"""What a schedule's prompt can hold beyond the night's dates (schedules.fill), filled
when its run starts, each only when the prompt names it:

- {{mood}}: its mood now (skills/mind.mood_line);
- {{on_mind}}: the notes whose `keys:` come up in its last thoughts and its last
  exchange with {{username}} (turns/loads.key_in), each with its summary;
- {{tool_failures}}: per tool, the calls that failed that night: how many, and its most
  frequent errors, each with the conversations it happened in by title and short id (a
  digest instead of every transcript);
- {{night_runs}}: every run of that night, in order: its routine, time and state with
  why, and its note: when it was last written and how many lines it has, or that it is
  not in the notebook (what a review checks before it says a run left something);
- {{voice_counts}}: its chat replies of the last 7 days against {{username}}'s
  messages: slop hits per 1,000 characters (turns/slop.find), the phrases it uses far
  more (turns/slop.profile), the share of replies that start
  lowercase, and how long it answers after {{username}} asked for short.

A value that has nothing to say is "(none)" (schedules.NONE)."""
from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median

from ..core.ids import iso
from . import NONE, night_start

DAYS = 7                       # the voice counts' window
TOP = 20                       # phrases it uses far more than the user
ERROR_CHARS = 200              # of an error's text
ERRORS_SHOWN = 3               # the most frequent errors of a tool, each with where
THOUGHTS = 5                   # its last thoughts {{on_mind}} looks in
ON_MIND = 8                    # the most notes {{on_mind}} names
SHORT = re.compile(r"\b(short|shorter|brief|briefly|tl;?dr|kurz)\b", re.I)
# a message the user wrote: typed here, or sent from the composer through the intercom
FROM_USER = ("m.role = 'user' AND (m.author = 'user' OR m.intercom_id IN "
             "(SELECT id FROM intercom WHERE from_addr = 'user'))")


async def extras(app, text: str, night: date,
                 now: datetime | None = None) -> dict[str, str]:
    """The values of the variables `text` names, for a run of `night`."""
    from ..clock import zone
    now = now or datetime.now(timezone.utc)
    store, cfg = app.store, app.cfg
    out: dict[str, str] = {}
    if "{{mood}}" in text:
        from ..skills import mind
        out["mood"] = mind.mood_line(app) or NONE
    if "{{on_mind}}" in text:
        out["on_mind"] = await on_mind(app)
    if "{{tool_failures}}" in text:
        out["tool_failures"] = await store.read(
            lambda c: tool_failures(c, night, zone(cfg)))
    if "{{night_runs}}" in text:
        root = cfg.agent.notebook if getattr(cfg, "agent", None) else None
        out["night_runs"] = await store.read(lambda c: night_runs(c, cfg, night, root))
    if "{{voice_counts}}" in text:
        voice = cfg.agent.voice if getattr(cfg, "agent", None) else {}
        out["voice_counts"] = await store.read(lambda c: voice_counts(c, now, voice))
    return out


# ---- on_mind ----------------------------------------------------------------------------

def last_thoughts(root: Path | None, n: int = THOUGHTS) -> list[str]:
    """The agent's last `n` thought lines, from the newest thoughts/<date>.md notes."""
    if root is None or not (root / "thoughts").is_dir():
        return []
    lines: list[str] = []
    for p in sorted((root / "thoughts").glob("*.md"), reverse=True):
        lines = [ln for ln in p.read_text(errors="replace").splitlines()
                 if ln.startswith("- ")] + lines
        if len(lines) >= n:
            break
    return lines[-n:]


def last_exchange(c: sqlite3.Connection) -> list[str]:
    """The newest message the user wrote it in a chat, and its newest reply there; a
    locked chat (conversations/store.locked) is left out."""
    from ..conversations.store import locked_sql
    out = []
    for where in (FROM_USER, "m.role = 'assistant' AND m.author = 'model'"):
        r = c.execute(
            "SELECT m.content FROM messages m JOIN conversations c ON c.id = "
            f"m.conversation_id WHERE c.kind = 'chat' AND {where} AND "
            f"m.content != '' AND NOT {locked_sql()} "
            "ORDER BY m.created_at DESC LIMIT 1").fetchone()
        if r:
            out.append(r[0][:600])
    return out


async def on_mind(app) -> str:
    """The notes its last thoughts and its last exchange name a key of, the most
    recently touched ON_MIND."""
    from ..skills import notebook_index
    from ..skills.notebook import keys_of
    from ..turns.loads import key_in
    root = app.cfg.agent.notebook if getattr(app.cfg, "agent", None) else None
    said = "\n".join([*last_thoughts(root), *await app.store.read(last_exchange)])
    if root is None or not said.strip():
        return NONE
    hits = [r for r in await notebook_index.list(app.store, order="touched")
            if key_in(said, keys_of(root, r["path"]))]
    return "\n".join(f"- {r['path']}: {r['summary']}" for r in hits[:ON_MIND]) or NONE


# ---- tool_failures ---------------------------------------------------------------------

def tool_failures(c: sqlite3.Connection, night: date, tz) -> str:
    """The failed calls of `night` (12:00 the day before to 12:00 on its date): a line
    per tool with how many of its calls that night failed, the most failures first, and
    under it a line per error (its first
    ERROR_CHARS, the most frequent ERRORS_SHOWN) with the conversations it happened in,
    each by title and its short id as past_conversations takes it; none of a locked
    conversation (conversations/store.locked). The error of a call that went on as a job is
    the job's result: the call's own is the line that said the job began."""
    from ..conversations.store import locked_sql
    lo = night_start(night, tz)
    rows = c.execute(
        "SELECT tc.name, coalesce(j.result, r.content) AS error, cv.id AS cid, "
        "coalesce(nullif(cv.title, ''), 'Untitled') AS title FROM tool_calls tc "
        "JOIN messages m ON m.id = tc.message_id "
        "JOIN conversations cv ON cv.id = m.conversation_id "
        "LEFT JOIN messages r ON r.id = tc.result_message_id "
        "LEFT JOIN jobs j ON j.id = tc.job_id "
        "WHERE tc.state = 'error' AND tc.started_at >= ? AND tc.started_at < ? "
        f"AND NOT {locked_sql('cv')} ORDER BY tc.started_at", (iso(lo), iso(lo + timedelta(days=1)))).fetchall()
    # every call of the night per tool, failed or not: the count a failure rate is of
    calls = dict(c.execute(
        "SELECT tc.name, count(*) FROM tool_calls tc JOIN messages m ON m.id = tc.message_id "
        "JOIN conversations cv ON cv.id = m.conversation_id WHERE tc.started_at >= ? AND "
        f"tc.started_at < ? AND NOT {locked_sql('cv')} GROUP BY tc.name",
        (iso(lo), iso(lo + timedelta(days=1)))).fetchall())
    # tool -> error -> [count, {conversation: its label}], in the order first seen
    tools: dict[str, dict[str, list]] = defaultdict(dict)
    for r in rows:
        error = " ".join((r["error"] or "").split())[:ERROR_CHARS] or "(no text)"
        seen = tools[r["name"]].setdefault(error, [0, {}])
        seen[0] += 1
        seen[1][r["cid"]] = f"{r['title']} {r['cid'][:8]}"
    if not tools:
        return NONE

    def count(errors: dict) -> int:
        return sum(n for n, _ in errors.values())
    out = []
    for name, errors in sorted(tools.items(), key=lambda x: -count(x[1])):
        n, of = count(errors), calls.get(name) or count(errors)
        out.append(f"- {name}: {n} failed of {of} call{'' if of == 1 else 's'} "
                   f"({round(100 * n / of)}%).")
        ranked = sorted(errors.items(), key=lambda x: -x[1][0])
        for error, (n, where) in ranked[:ERRORS_SHOWN]:
            times = "once" if n == 1 else f"{n} times"
            out.append(f"  - {times}: {error} In: {', '.join(where.values())}.")
        if rest := ranked[ERRORS_SHOWN:]:
            out.append(f"  - {sum(n for _, (n, _) in rest)} more failed with "
                       f"{len(rest)} other errors.")
    return "\n".join(out)


# ---- night_runs --------------------------------------------------------------------------

def night_runs(c: sqlite3.Connection, cfg, night: date, root: Path | None) -> str:
    """Every run of `night`, in the order of their times: "- Watch videos (watch), 01:00:
    done. Note watched/2026-11-12.md, written 12 Nov 01:43, 54 lines." A run with an
    error or a reason says it; a note that is not there says so."""
    from ..clock import zone
    from ..skills.anchors import lines_of
    from . import fill, run_date, shown
    from .runs import results
    tz = zone(cfg)
    res = results(c, night.isoformat())
    schedules = getattr(cfg, "schedules", None) or {}
    out = []
    for r in c.execute("SELECT schedule, slot, state, error, reason FROM runs WHERE night = ? "
                       "ORDER BY slot", (night.isoformat(),)):
        s = schedules.get(r["schedule"])
        slot = datetime.fromisoformat(r["slot"])
        line = (f"- {shown(s) if s else r['schedule']} ({r['schedule']}), "
                f"{slot.astimezone(tz):%H:%M}: {r['state']}")
        why = r["error"] or r["reason"]
        line += f" ({why})." if why else "."
        if s is not None and s.note:
            path = fill(cfg, s.note, run_date(s, slot, tz), res)
            p = root / path if root is not None else None
            if p is not None and p.is_file():
                at = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).astimezone(tz)
                n = len(lines_of(p.read_text(errors="replace"))[0])
                line += f" Note {path}, written {at:%d %b %H:%M}, {n} lines."
            else:
                line += f" Note {path}: not in the notebook."
        out.append(line)
    return "\n".join(out) or NONE


# ---- voice_counts ------------------------------------------------------------------------

def _texts(c: sqlite3.Connection, since: str) -> tuple[list[dict], list[dict]]:
    """(its chat replies, the user's messages there), each {cid, at, text}, oldest
    first."""
    def rows(where: str) -> list[dict]:
        return [dict(r) for r in c.execute(
            "SELECT m.conversation_id AS cid, m.created_at AS at, m.content AS text FROM "
            "messages m JOIN conversations c ON c.id = m.conversation_id WHERE c.kind = "
            f"'chat' AND {where} AND m.content != '' AND "
            "m.created_at >= ? ORDER BY m.created_at", (since,))]
    return rows("m.role = 'assistant' AND m.author = 'model'"), rows(FROM_USER)


def _per_k(texts: list[str], voice: dict) -> float:
    from ..turns import slop
    chars = sum(len(t) for t in texts)
    return 1000 * sum(len(slop.find(t, voice)) for t in texts) / chars if chars else 0.0


def voice_counts(c: sqlite3.Connection, now: datetime, voice: dict) -> str:
    from ..turns import slop
    own, theirs = _texts(c, iso(now - timedelta(days=DAYS)))
    if not own:
        return NONE
    h, u = [m["text"] for m in own], [m["text"] for m in theirs]
    lines = [f"Your chat replies of the last {DAYS} days: {len(h)} ({sum(map(len, h)):,} "
             f"characters). {{{{username}}}}'s messages: {len(u)}.",
             f"Slop hits per 1,000 characters: yours {_per_k(h, voice):.1f}, "
             f"{{{{username}}}}'s {_per_k(u, voice):.1f}."]
    lettered = [t for t in h if re.search(r"[^\W\d_]", t)]
    low = sum(1 for t in lettered if re.search(r"[^\W\d_]", t)[0].islower())
    lines.append(f"Replies that start lowercase: {round(100 * low / len(lettered))}%."
                 if lettered else "Replies that start lowercase: (none).")
    after = []
    for m in theirs:
        if SHORT.search(m["text"]):
            nxt = next((r for r in own if r["cid"] == m["cid"] and r["at"] > m["at"]), None)
            if nxt:
                after.append(len(nxt["text"].split()))
    lines.append(f"After {{{{username}}}} asked for short, your reply was "
                 f"{median(after):.0f} words (median of {len(after)})." if after else
                 "{{username}} did not ask for short.")
    top = list(slop.profile(h, u))[:TOP]
    lines.append("What you write far more than {{username}}: " + (", ".join(
        f'"{p[0]}" ({p[1]:.1f} times)' for p in top) if top else "(none)") + ".")
    return "\n".join(lines)
