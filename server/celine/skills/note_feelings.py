"""The feelings the agent keeps with what it writes in its notebook: `felt` on notebook__write
and notebook__edit, in its own words, only when it gives one. The app's mood is never written
here, and a sub-agent's call keeps none.

A feeling belongs to the text it was written with. A save with `felt` gives it to each
section (recall.sections) that the save made new or changed, under the hash of that
section's text (its Last changed line and head lines left out). A section changed later,
with a feeling or without one, has a new hash, so the old feeling no longer shows on it.
The rows stay (`note_feelings`), so an earlier text that comes back brings its feeling back.

Shown, never ranked: a word beside the section in `[your memory]` (skills/recall.py) and in
notebook__read's head. Recall's order does not use it."""
from __future__ import annotations

import hashlib
import re
import sqlite3

from ..core.ids import now
from .anchors import is_stamp
from .recall import sections

FELT_MAX = 80       # characters: a word or a few
_HEAD = re.compile(r"^\s*(?:-{3,}|(?:load|keys|class)\s*:.*)\s*$", re.I)


def _bare(part: str) -> str:
    """A section's text as its feeling is kept by: line ends as \\n, without the note's
    Last changed line and the head lines under it (`keys:`, `class:`, `load:`, `---`)."""
    lines = part.replace("\r\n", "\n").split("\n")
    if lines and is_stamp(lines[0]):
        lines = lines[1:]
    while lines and (not lines[0].strip() or _HEAD.match(lines[0])):
        lines = lines[1:]
    return "\n".join(lines).strip()


def hashes(text: str) -> list[tuple[str, str | None]]:
    """(heading, hash) per section of a note, in order; the hash None for a section with
    nothing but head lines."""
    out: list[tuple[str, str | None]] = []
    for heading, part in sections(text):
        bare = _bare(part)
        out.append((heading, hashlib.sha1(bare.encode()).hexdigest()[:16] if bare else None))
    return out


def checked(felt: str) -> str | None:
    """The sentence why `felt` is refused, or None when it is fine (or empty)."""
    if len(felt.strip()) > FELT_MAX:
        return (f"felt is a word or a few, at most {FELT_MAX} characters, and this one has "
                f"{len(felt.strip())}. Nothing was saved.")
    return None


async def keep(store, path: str, old: str | None, new: str, felt: str,
               conversation_id: str) -> int:
    """`felt` given to each section of `new` that `old` did not have word for word. -> how
    many sections it went to."""
    felt = " ".join(felt.split())
    if not felt:
        return 0
    before = {h for _, h in hashes(old)} if old else set()
    fresh = [h for _, h in hashes(new) if h and h not in before]
    if not fresh:
        return 0
    at = now()

    def fn(c: sqlite3.Connection) -> None:
        c.executemany(
            "INSERT INTO note_feelings(hash, path, felt, at, conversation_id) "
            "VALUES(?,?,?,?,?) ON CONFLICT(hash) DO UPDATE SET path=excluded.path, "
            "felt=excluded.felt, at=excluded.at, conversation_id=excluded.conversation_id",
            [(h, path, felt, at, conversation_id) for h in fresh])
    await store.write(fn)
    return len(fresh)


def felt_of(c: sqlite3.Connection, keys: list[str]) -> dict[str, str]:
    """The feeling kept with each of these section hashes that has one."""
    out: dict[str, str] = {}
    keys = list(dict.fromkeys(keys))
    for i in range(0, len(keys), 500):
        part = keys[i:i + 500]
        out.update(c.execute(
            f"SELECT hash, felt FROM note_feelings WHERE hash IN ({','.join('?' * len(part))})",
            part).fetchall())
    return out


async def of_note(store, text: str) -> list[tuple[str, str]]:
    """(heading, feeling) for each section of a note that has one, in order; the heading ""
    for the lines before the first one."""
    hs = hashes(text)
    felt = await store.read(lambda c: felt_of(c, [h for _, h in hs if h]))
    return [(heading, felt[h]) for heading, h in hs if h and h in felt]


def line(kept: list[tuple[str, str]]) -> str:
    """notebook__read's head line for a note's kept feelings; "" without any."""
    if not kept:
        return ""
    said = "; ".join(f'"## {heading}" felt "{f}"' if heading else f'the top felt "{f}"'
                     for heading, f in kept)
    return f"(What you felt as you wrote its sections: {said}.)\n\n"
