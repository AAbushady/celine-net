"""What a new user message brings back from its notebook, graded: `[your memory]`.

Candidates: the notes whose `keys:` line comes up in the talk (the message and the first
turns/loads.KEY_REPLY characters of its last reply before it; loads.key_in: a whole
word or phrase, any case), cut into sections at their `## ` headings (the lines before
the first heading are a section too), at most NOTE_SECTIONS of one note and CANDIDATES in
all, taken from every note alike. Each section is graded by the cosine similarity of
its embedding to the talk's (the Embeddings task, kb/semantic.query_embedder). A
section's vector is kept in memory by its text and the model that made it (KEPT of
them, the least recently used go first), so a message embeds the talk and only the
sections no earlier message embedded, in one call. The message waits EMBED_SECONDS for
it; a call still running then goes on, and the next message finds what it made (on a CPU
embedder a section takes about a second). A section comes back only at or over `memory_floor`,
the best first, at most `memory_sections` of them in at most `memory_tokens` tokens
(var/settings/context.toml, turns/loads.read_shares).

Never: a note it carries in full now (an always note, one it or the user loaded, its
note on a loaded skill), a note with `load: off`, one left out here or outside a
sub-agent's folder, nor a section shown on this branch since the last compaction, unless
it changed since. A note this conversation's own calls changed last says so in its
section's line, with when (OWN): what it says about this conversation is its own account.
When the embedding model does not answer, the block says so and
nothing comes back: there is no word-only substitute.

The block is added to the end of the message once (turns/message_context.py: stored, sent
byte-identically after) by the notebook skill's message_context hook, where its mind runs
(skills/places.py, the mind skill's places) and never with automatic context off. Its report
(`message_context.report.notebook`): the query, the floor, every candidate section with
its key, score and state (`shown`, `below the floor`, `shown before`, `no room`), and
the error when the embedding model did not answer.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import threading
from array import array
from collections import OrderedDict
from datetime import datetime
from typing import Callable

from ..clock import zone
from ..core.ids import when

HEAD = "[your memory]"
OWN = "you changed it in this chat"   # on a section's line, before when
SECTION_CHARS = 2000     # a longer section is cut there, with where the rest is
CANDIDATES = 40          # the most sections graded for one message
NOTE_SECTIONS = 8        # the most of them from one note, so every note gets a chance
EMBED_SECONDS = 10.0     # a message waits this long for its vectors
CALL_SECONDS = 300.0     # one embedding call may take this long, after the message stopped waiting
KEPT = 4000              # section vectors kept in memory (about 4 KB each)
LATE = (f"the embedding model took more than {EMBED_SECONDS:g} s; what it makes is kept "
        "for the next message")
SHOWN = "shown"
BELOW = "below the floor"
BEFORE = "shown before"
NO_ROOM = "no room"
SKILL_NOTE = re.compile(r"skills/[^/]+\.md")
_HEADING = re.compile(r"^## +(.+?)\s*$", re.M)


def sections(text: str) -> list[tuple[str, str]]:
    """A note as (heading, text) sections, cut at its `## ` headings; the lines before the
    first heading are a section with the heading "". Empty sections are left out."""
    cuts = [m.start() for m in _HEADING.finditer(text)]
    out = []
    for a, b in zip([0, *cuts], [*cuts, len(text)]):
        part = text[a:b].strip()
        m = _HEADING.match(part)
        heading = m[1] if m else ""
        if part[m.end():].strip() if m else part:
            out.append((heading, part))
    return out


def _cut(text: str, rel: str) -> str:
    if len(text) <= SECTION_CHARS:
        return text
    at = text.rfind("\n", 0, SECTION_CHARS)
    return text[:at if at > 0 else SECTION_CHARS].rstrip() + \
        f'\nrest via notebook__read(path="{rel}")'


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def _facts(c: sqlite3.Connection, cid: str, tid: str, mid: str, limit: int | None,
) -> dict | None:
    """What the block needs from the database, in one read: the conversation's switch,
    folder and left-out notes, the place, its last reply before `mid`, the sections shown
    on this branch since the last compaction ({id: hash}), and the notes its own calls
    changed (own_changes)."""
    from .places import place_of
    conv = c.execute("SELECT auto_context, notebook_folder, notes_off FROM conversations "
                     "WHERE id=?", (cid,)).fetchone()
    if conv is None:
        return None
    from ..turns.message_context import reports
    reply, shown = "", {}
    for role, content, value in reports(c, cid, mid, "$.notebook.shown", limit):
        if role == "assistant" and not reply and content.strip():
            reply = content
        for s in value or ():
            shown.setdefault(s["id"], s["hash"])
    return {"auto": bool(conv[0]), "folder": conv[1],
            "off": set(json.loads(conv[2] or "[]")), "place": place_of(c, cid, tid),
            "reply": reply, "shown": shown, "own": own_changes(c, cid)}


def own_changes(c: sqlite3.Connection, cid: str) -> dict[str, str]:
    """{note: when the newest call of conversation `cid` that wrote it ended}. A call of
    the notebook or the mind that changed a note names it in its row (`display.path`)."""
    return {r[0]: r[1] for r in c.execute(
        "SELECT json_extract(tc.display, '$.path'), max(tc.finished_at) "
        "FROM tool_calls tc JOIN messages m ON m.id = tc.message_id "
        "WHERE m.conversation_id = ? AND tc.state = 'ok' AND tc.mutating = 1 "
        "AND (tc.name LIKE 'notebook!_!_%' ESCAPE '!' OR tc.name LIKE 'mind!_!_%' ESCAPE '!') "
        "GROUP BY 1", (cid,)) if r[0] and r[1]}


def _own(changed: str | None, mtime: float, cfg) -> str:
    """", you changed it in this chat, Tue 10 Nov 17:25" for a note whose last change is
    this conversation's own call (it ended at `changed`, and the file is no newer)."""
    if not changed or mtime > datetime.fromisoformat(changed).timestamp() + 2:
        return ""
    return f", {OWN}, {when(changed, zone(cfg))}"


_kept: OrderedDict[tuple, array] = OrderedDict()     # (model, text hash) -> vector
_kept_lock = threading.Lock()
_late: set[asyncio.Future] = set()      # calls no message waits for any more, held to their end


def _model(cfg) -> tuple[tuple, str, Callable[[list[str]], list[list[float]]]]:
    """(which vectors it makes: its name and fingerprint, its query prefix, a call that
    embeds texts) of the first model of the Embeddings task that answers; raises
    SemanticError when none does."""
    from ..kb import semantic
    e = semantic.query_embedder(cfg)
    return (e.ref, hash(e.fp)), e.s["query_prefix"], \
        lambda texts: e.embed(texts, timeout=CALL_SECONDS)


def _vectors(cfg, query: str, texts: list[str]) -> tuple[list[float], list[array]]:
    """(the query's vector, each text's), in a thread: the texts' from `_kept`, the query
    and every text not there in one call, which then keeps theirs."""
    key, prefix, embed = _model(cfg)
    hashes = [hashlib.sha1(t.encode()).digest() for t in texts]
    with _kept_lock:
        have = {}
        for h in hashes:
            if (v := _kept.get((key, h))) is not None:
                _kept.move_to_end((key, h))
                have[h] = v
    todo = {h: t for h, t in zip(hashes, texts) if h not in have}
    vecs = embed([prefix + query, *todo.values()])
    with _kept_lock:
        for h, v in zip(todo, vecs[1:]):
            have[h] = _kept[(key, h)] = array("f", v)
        while len(_kept) > KEPT:
            _kept.popitem(last=False)
    return vecs[0], [have[h] for h in hashes]


def _settled(work: asyncio.Future) -> None:
    """A call the message stopped waiting for: its vectors are kept, its error is
    nobody's to say."""
    _late.discard(work)
    if not work.cancelled():
        work.exception()


async def block(cfg, store, cid: str, tid: str, message: dict
                ) -> tuple[str, dict] | None:
    """(the `[your memory]` block, its report) for the new user message `message`: the
    block empty when no section passed, None when nothing came up (no key in the talk,
    or not here)."""
    from ..kb.embed import SemanticError, cosine
    from . import notebook, notebook_index
    from .places import offered
    from ..turns import loads
    from ..backends import tokens
    from ..turns.message_context import history_limit
    s = loads.shares(cfg)
    f = await store.read(lambda c: _facts(c, cid, tid, message["id"], history_limit(cfg)))
    if f is None or not offered("mind", f["place"]) or not f["auto"]:
        return None
    talk = "\n".join([message.get("content") or "", f["reply"][:loads.KEY_REPLY]]).strip()
    root, user = cfg.agent.notebook, (getattr(cfg, "user", "") or "").lower()
    found: list[dict] = []
    for r in await notebook_index.list(store, f["folder"]):
        rel = r["path"]
        # a skill's note (skills/<name>.md) comes with skills__load and read_guide
        if rel in f["off"] or SKILL_NOTE.fullmatch(rel) or \
                not notebook.inside(rel, f["folder"]):
            continue
        key = loads.key_in(talk, notebook.keys_of(root, rel))
        text = notebook.read_note(root, rel) if key else None
        if text is None or notebook.load_of(rel, text, user) is not None:   # always or off
            continue
        try:
            mtime = (root / rel).stat().st_mtime
        except OSError:
            continue
        date = f"{datetime.fromtimestamp(mtime):%-d %b %Y}" + _own(f["own"].get(rel), mtime, cfg)
        for i, (heading, part) in enumerate(sections(cfg.substitute(text))[:NOTE_SECTIONS]):
            sid = f"{rel}#{heading}" if heading else rel
            found.append({"id": sid, "key": key, "date": date, "nth": i,
                          "text": _cut(part, rel), "hash": _hash(part)})
    if not found:
        return None
    # each note's first section, then each one's second, and so on: the cut takes from
    # every note alike (a stable sort keeps the notes' order within one rank)
    found = sorted(found, key=lambda x: x["nth"])[:CANDIDATES]
    report: dict = {"query": talk, "floor": s["memory_floor"],
                    "candidates": [], "shown": []}
    work = asyncio.ensure_future(asyncio.to_thread(
        _vectors, cfg, talk, [f"{x['id']}\n{x['text']}" for x in found]))
    try:
        try:
            q, vecs = await asyncio.wait_for(asyncio.shield(work), EMBED_SECONDS)
        except TimeoutError:
            _late.add(work)
            work.add_done_callback(_settled)
            raise SemanticError(LATE) from None
    except SemanticError as e:
        report["error"] = str(e)
        report["candidates"] = [{"id": x["id"], "key": x["key"]} for x in found]
        # it says that nothing came, so a memory it has cannot be laid at this block's door
        return f"{HEAD} no notes came with this message: {e}", report
    for x, v in zip(found, vecs):
        x["score"] = round(cosine(q, v), 3)
    counter = tokens.counter(None)
    lines, used = [], 0
    for x in sorted(found, key=lambda x: -x["score"]):
        entry = f"### {x['id']} ({x['score']:.2f}, {x['date']})\n{x['text']}"
        cost = counter.estimate(entry)
        if x["score"] < s["memory_floor"]:
            x["state"] = BELOW
        elif f["shown"].get(x["id"]) == x["hash"]:
            x["state"] = BEFORE
        elif len(lines) >= s["memory_sections"] or used + cost > s["memory_tokens"]:
            x["state"] = NO_ROOM
        else:
            x["state"] = SHOWN
            lines.append(entry)
            used += cost
            report["shown"].append({"id": x["id"], "hash": x["hash"]})
        report["candidates"].append({k: x[k] for k in ("id", "key", "score", "state")})
    report["tokens"] = used
    if not lines:
        return "", report
    return "\n\n".join([f"{HEAD} sections of your notes this message brings up, "
                        "the best match first (similarity from 0 to 1, last changed):",
                        *lines]), report
