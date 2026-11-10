"""What was sent to a model, searched.

Every request of a reply's step is kept as it went out (turns/store.py pack_body, the
`requests` table), so "was this text ever sent to it?" has a record to answer from.
`first_sent` finds the first request of a conversation that holds a text and says what
kind of piece held it: its instructions, the summary of earlier messages, a message to
it, a tool's result, or its own earlier words and calls sent back to it. For a message,
`message_part` says which stored part held it: what was written to it, the block the app
put under it (its memory, its mind) or the turn's tail. `own_first` finds where it first
wrote the text itself, in its stored replies and calls. past_conversations__find_sent tells
it all three (skills/conversations.py)."""
from __future__ import annotations

import json
import re
import sqlite3
import zlib
from dataclasses import dataclass

import anyio

MIN = 8                     # characters of text: a shorter one matches by chance
MAX_REQUESTS = 600          # the newest this many requests of a conversation are searched
SUMMARY = "Earlier in this conversation"    # how the summary starts in the instructions (turns/context.py)
LABEL = re.compile(r"^\[([^\]\n]{3,60})\]")         # a block's head: "[your memory] sections of ..."
NOTE = re.compile(r"### (\S[^\n]*?) \(")            # a recalled section's head (skills/recall.py)
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})


def norm(text: str) -> str:
    """A text as the search compares it: one case, single spaces, straight quotes."""
    return " ".join(text.translate(_QUOTES).split()).casefold()


@dataclass(frozen=True)
class Piece:
    """One stretch of a request: `kind` is instructions, summary, message (someone
    wrote to it, with what the app put under it), result (a tool's), own (its own
    earlier words or thinking) or call (its own earlier call). `name`: the tool."""
    kind: str
    text: str
    name: str | None = None


def _strings(v) -> list[str]:
    """Every text of a content value: a string, or the text parts of a list."""
    if isinstance(v, str):
        return [v] if v else []
    if isinstance(v, list):
        return [p["text"] for p in v if isinstance(p, dict) and isinstance(p.get("text"), str)
                and p["text"]]
    return []


def _leaves(v) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [s for x in v.values() for s in _leaves(x)]
    if isinstance(v, list):
        return [s for x in v for s in _leaves(x)]
    return []


def _arguments(v) -> str:
    """A call's arguments as text to search: the JSON as sent, and its string values
    unescaped (a quote or a line break in a value reads as it was written)."""
    if isinstance(v, str):
        try:
            return v + "\n" + "\n".join(_leaves(json.loads(v)))
        except ValueError:
            return v
    return "\n".join(_leaves(v))


def _instructions(text: str) -> list[Piece]:
    at = text.find(SUMMARY)
    if at < 0:
        return [Piece("instructions", text)]
    return [Piece("instructions", text[:at]), Piece("summary", text[at:])]


def pieces(body: dict) -> list[Piece]:
    """A stored request body as pieces, in the shapes the backends send: OpenAI and
    Ollama (messages with roles, tool_calls, tool rows) and Anthropic (a system text,
    content blocks: text, thinking, tool_use, tool_result)."""
    out: list[Piece] = []
    names: dict[str, str] = {}          # a call's id -> its tool, for a result that names the id
    for t in _strings(body.get("system")):
        out += _instructions(t)
    for m in body.get("messages") or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        for call in m.get("tool_calls") or []:
            fn = call.get("function") or {}
            names[call.get("id") or ""] = fn.get("name") or ""
            out.append(Piece("call", _arguments(fn.get("arguments")), fn.get("name")))
        if role == "assistant":
            for key in ("reasoning_content", "reasoning"):
                if isinstance(m.get(key), str) and m[key]:
                    out.append(Piece("own", m[key]))
        content = m.get("content")
        parts = content if isinstance(content, list) else [{"type": "text", "text": content}]
        for p in parts:
            if not isinstance(p, dict):
                continue
            kind = p.get("type")
            if kind == "tool_use":
                names[p.get("id") or ""] = p.get("name") or ""
                out.append(Piece("call", _arguments(p.get("input")), p.get("name")))
            elif kind == "tool_result":
                out += [Piece("result", t, names.get(p.get("tool_use_id") or "") or None)
                        for t in _strings(p.get("content"))]
            elif kind == "thinking":
                if isinstance(p.get("thinking"), str) and p["thinking"]:
                    out.append(Piece("own", p["thinking"]))
            elif isinstance(p.get("text"), str) and p["text"]:
                t = p["text"]
                if role == "system":
                    out += _instructions(t)
                elif role == "assistant":
                    out.append(Piece("own", t))
                elif role == "tool":
                    out.append(Piece("result", t, m.get("name")
                                     or names.get(m.get("tool_call_id") or "") or None))
                else:
                    out.append(Piece("message", t))
    return out


def find(body: dict, needle: str, seen: dict | None = None) -> list[tuple[str, str | None]]:
    """The kinds of piece of a request body that hold `needle` (already `norm`ed), in
    the request's order, each (kind, tool) once. `seen` remembers the pieces looked at
    before: a request repeats most of the one before it."""
    seen = {} if seen is None else seen
    out: list[tuple[str, str | None]] = []
    for p in pieces(body):
        key = (p.kind, p.name, hash(p.text))
        hit = seen.get(key)
        if hit is None:
            hit = seen[key] = needle in norm(p.text)
        if hit and (p.kind, p.name) not in out:
            out.append((p.kind, p.name))
    return out


def _scan(raw: bytes, needle: str, seen: dict) -> list[tuple[str, str | None]]:
    return find(json.loads(zlib.decompress(raw)), needle, seen)


async def first_sent(store, cid: str, text: str) -> dict:
    """The first request of conversation `cid` that held `text`:
    {searched, skipped (older requests past MAX_REQUESTS, not searched), oldest (the
    time of the oldest searched, None: none are kept), first ({at, kinds: the (kind,
    tool) pairs of `find`}, None: no request held it), now (the newest request holds
    it)}."""
    needle = norm(text)
    rows = await store.read(lambda c: c.execute(
        "SELECT q.message_id, q.step, q.created_at FROM requests q JOIN turns t ON "
        "t.id = q.turn_id WHERE t.conversation_id=? ORDER BY q.created_at, q.step",
        (cid,)).fetchall())
    kept = rows[-MAX_REQUESTS:]
    out = {"searched": len(kept), "skipped": len(rows) - len(kept),
           "oldest": kept[0]["created_at"] if kept else None, "first": None, "now": False}
    seen: dict = {}

    async def look(r) -> list[tuple[str, str | None]]:
        raw = await store.read(lambda c: c.execute(
            "SELECT body FROM requests WHERE message_id=? AND step=?",
            (r["message_id"], r["step"])).fetchone())
        if raw is None or raw[0] is None:
            return []
        # a body is some hundred kilobytes of JSON: off the event loop
        return await anyio.to_thread.run_sync(_scan, raw[0], needle, seen)

    for i, r in enumerate(kept):
        kinds = await look(r)
        if kinds:
            out["first"] = {"at": r["created_at"], "kinds": kinds}
            out["now"] = True if i == len(kept) - 1 else bool(await look(kept[-1]))
            break
    return out


def _block(text: str, needle: str, spans: bool) -> tuple[str | None, str | None]:
    """(the label of the block of `text` that holds `needle`, the note section recalled
    there). `spans`: a labelled block runs on to the next label (the block under a
    message); else a label covers its own paragraph alone (a tail's lines)."""
    label = note = None
    for para in text.split("\n\n"):
        head = LABEL.match(para)
        if head:
            label, note = head.group(1), None
        elif not spans:
            label = None
        entry = NOTE.match(para)
        if entry:
            note = entry.group(1)
        if needle in norm(para):
            return label, note if label is not None else None
    return None, None                   # it runs across two paragraphs


def message_part(c: sqlite3.Connection, cid: str, text: str) -> dict | None:
    """The oldest message to it in conversation `cid` with `text` in one of its stored
    parts: {at, author (the message's: user, intercom ...), part (`text`: what was
    written to it; `block`: what the app put under it, turns/message_context.py;
    `tail`: its turn's tail, turns/prompt.py), label (the block's, None without one),
    note (the note section a memory block recalled)}. None: no stored part holds it."""
    needle = norm(text)
    like = _like(text)
    for r in c.execute(
            "SELECT m.created_at, m.author, m.content, x.text AS block, x.tail FROM messages m "
            "LEFT JOIN message_context x ON x.message_id = m.id WHERE m.conversation_id=? "
            "AND m.role='user' AND (m.content LIKE ? OR x.text LIKE ? OR x.tail LIKE ?) "
            "ORDER BY m.created_at, m.rowid", (cid, like, like, like)):
        base = {"at": r["created_at"], "author": r["author"], "label": None, "note": None}
        if needle in norm(r["content"] or ""):
            return {**base, "part": "text"}
        if needle in norm(r["block"] or ""):
            label, note = _block(r["block"], needle, spans=True)
            return {**base, "part": "block", "label": label, "note": note}
        if needle in norm(r["tail"] or ""):
            label, _ = _block(r["tail"], needle, spans=False)
            return {**base, "part": "tail", "label": label}
    return None


def _like(text: str) -> str:
    """A cheap first cut for SQLite: the text's longest plain word, anywhere."""
    words = re.findall(r"[A-Za-z0-9]{4,}", text)
    return f"%{max(words, key=len)}%" if words else "%"


def own_first(c: sqlite3.Connection, cid: str, text: str) -> dict | None:
    """Where it first wrote `text` itself in conversation `cid`: {at, kind (reply,
    thinking, call), name (the call's tool)}, None when it never did."""
    needle = norm(text)
    like = _like(text)
    found: list[tuple[str, str, str | None]] = []
    for r in c.execute(
            "SELECT created_at, content, reasoning FROM messages WHERE conversation_id=? AND "
            "role='assistant' AND (content LIKE ? OR reasoning LIKE ?) ORDER BY created_at",
            (cid, like, like)):
        if needle in norm(r["content"] or ""):
            found.append((r["created_at"], "reply", None))
            break
        if needle in norm(r["reasoning"] or ""):
            found.append((r["created_at"], "thinking", None))
            break
    for r in c.execute(
            "SELECT tc.started_at, m.created_at, tc.name, tc.arguments FROM tool_calls tc JOIN "
            "messages m ON m.id = tc.message_id WHERE m.conversation_id=? AND "
            "tc.arguments LIKE ? ORDER BY m.created_at, tc.rowid", (cid, like)):
        if needle in norm(_arguments(r["arguments"] or "")):
            found.append((r["started_at"] or r["created_at"], "call", r["name"]))
            break
    if not found:
        return None
    at, kind, name = min(found, key=lambda f: f[0])
    return {"at": at, "kind": kind, "name": name}
