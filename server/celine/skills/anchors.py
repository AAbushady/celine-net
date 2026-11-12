"""Hashline anchors: each line of a text as `<n>:<hash>| <text>`, and anchored edits
(replace, insert, delete) checked against the text as it is now. The notebook's notes
(notebook__edit) uses them."""
from __future__ import annotations

import difflib
import json
import re
import zlib
from typing import Annotated, Literal, Required, TypedDict

# a note's first line is its Last changed stamp (notebook.py), which an edit cannot touch;
# it is shown plain, with no anchor to copy
STAMP_KEPT = "Line 1, the Last changed line, is kept up to date automatically."
_STAMP = re.compile(r"^\s*last changed:", re.I)


def is_stamp(line: str) -> bool:
    return bool(_STAMP.match(line))


class Edit(TypedDict, total=False):
    """One change of notebook__edit, anchored on `n:hash` from notebook__read."""
    op: Required[Annotated[Literal["replace", "insert", "delete"],
                           "replace: lines start to end become `text`. insert: `text` goes "
                           "below `after`. delete: lines start to end go."]]
    start: Annotated[str, "replace, delete: the anchor of the first line, such as "
                          "\"12:ab\"."]
    end: Annotated[str, "replace, delete: the anchor of the last line. Leave it out for "
                        "one line."]
    after: Annotated[str, "insert: the anchor of the line to insert below, \"start\" for "
                          "the top of the note or \"end\" for its bottom."]
    text: Annotated[str, "replace, insert: the new lines, plain, without anchors."]


# Hashline anchors: `<n>:<hash>`, the hash two base32 characters of the line's text.
# A stale anchor no longer matches: a line that changed since it was read is refused, one
# that only moved is followed through the note's earlier texts (_followed).
_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
_ANCHOR = re.compile(r"^\s*(\d+)\s*:\s*([0-9a-z]{2})\b")


class EditRefused(Exception):
    pass


def line_hash(line: str) -> str:
    h = zlib.crc32(line.rstrip().encode())
    return _ALPHABET[h & 31] + _ALPHABET[(h >> 5) & 31]


def lines_of(text: str) -> tuple[list[str], str, bool]:
    """(lines without their ends, the file's line end, whether it ends with one)."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.split("\n")
    trailing = len(lines) > 1 and lines[-1] == ""
    if lines[-1] == "":
        lines.pop()
    return [ln.removesuffix("\r") for ln in lines], newline, trailing


def numbered(lines: list[str], first: int, last: int) -> list[str]:
    """Lines first..last (1-based, inclusive) as `n:hash| text`; a Last changed stamp on
    line 1 as it is."""
    return [lines[0] if i == 1 and is_stamp(lines[0]) else
            f"{i}:{line_hash(lines[i - 1])}| {lines[i - 1]}"
            for i in range(max(1, first), min(last, len(lines)) + 1)]


def _near(lines: list[str], n: int) -> str:
    return "\n".join(numbered(lines, n - 2, n + 2))


def _followed(n: int, h: str, lines: list[str], earlier) -> int:
    """Where line `n` of an earlier text, whose hash is `h`, is now: the newest earlier
    text (newest first) that has `h` on line `n` and keeps that line unchanged into
    `lines`; 0 when none does. The line itself is followed through the change, so a hash
    another line shares by chance never moves an edit there."""
    for old in earlier:
        if not (1 <= n <= len(old) and line_hash(old[n - 1]) == h):
            continue
        for tag, a1, a2, b1, _ in difflib.SequenceMatcher(None, old, lines,
                                                          autojunk=False).get_opcodes():
            if tag == "equal" and a1 <= n - 1 < a2:
                return b1 + (n - 1 - a1) + 1
        return 0                # the line it read was changed since: stale
    return 0


def _anchor(ref, lines: list[str], bad: list[str], earlier=(),
            followed: list[str] | None = None) -> int:
    """The line number of an `n:hash` anchor, or 0 after noting what is wrong in `bad`.
    An anchor read before an earlier save is followed to its line now (`_followed`),
    noted in `followed`."""
    m = _ANCHOR.match(str(ref or ""))
    if not m:
        raise EditRefused(f"{ref!r} is not an anchor. An anchor is `n:hash` from "
                          f"notebook__read, e.g. \"12:ab\".")
    n, h = int(m[1]), m[2]
    if 1 <= n <= len(lines) and line_hash(lines[n - 1]) == h:
        return n
    if (now := _followed(n, h, lines, earlier)):
        if followed is not None:
            followed.append(f"{n}:{h} is line {now} now")
        return now
    if not 1 <= n <= len(lines):
        where = f"the note has {len(lines)} lines"
        around = _near(lines, len(lines)) if lines else "(the note is empty)"
    else:
        where = f"line {n} has the anchor {n}:{line_hash(lines[n - 1])}"
        around = _near(lines, n)
    moved = [i for i, ln in enumerate(lines, 1) if line_hash(ln) == h and i != n]
    if moved:
        i = min(moved, key=lambda i: abs(i - n))
        where += f"; the line with hash {h} nearest to it is {i}"
        around = around + "\n...\n" + _near(lines, i)
    bad.append(f"{m[1]}:{h} does not match ({where}). The lines there now:\n{around}")
    return 0


def _number(ref) -> int | None:
    m = _ANCHOR.match(str(ref or ""))
    return int(m[1]) if m else None


# the fields each op takes (Edit), the ones it cannot do without, and what a missing one
# holds when its name alone does not say
TAKES = {"replace": ("start", "end", "text"), "insert": ("after", "text"),
         "delete": ("start", "end")}
NEEDS = {"replace": ("start", "text"), "insert": ("after", "text"), "delete": ("start",)}
HOLDS = {"after": "the anchor of the line the text goes under, \"start\" for the top or "
                  "\"end\" for the bottom"}


def _listed(names: list[str]) -> str:
    """"a, b and c"."""
    return ", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0]


def _op(i: int, e) -> str:
    """The op of edit `i`, once it is an object with an op and every field that op needs;
    else EditRefused, which names as well any field it has that the op does not take."""
    if not isinstance(e, dict):
        what = "text" if isinstance(e, str) else "a list" if isinstance(e, list) else \
            json.dumps(e, default=str)
        raise EditRefused(f"edit {i} is {what}, and an edit is an object with `op` and "
                          f"its fields.")
    op = e.get("op")
    if not isinstance(op, str) or op not in TAKES:
        said = "has no `op`" if op is None else f"has the `op` {json.dumps(op, default=str)}"
        raise EditRefused(
            f"edit {i} {said}: `op` is \"replace\", \"insert\" or \"delete\". To add at "
            f"the end, insert with `after` set to the last line's anchor.")
    missing = [k for k in NEEDS[op]
               if (not isinstance(e.get(k), str) if k == "text" else e.get(k) is None)]
    if missing:
        said = f"edit {i} ({op}) needs " + _listed(
            [f"`{k}` ({HOLDS[k]})" if k in HOLDS else f"`{k}`" for k in missing])
        other = [f"`{k}`" for k in e if k != "op" and k not in TAKES[op]]
        if other:
            said += (f", and {_listed(other)} "
                     f"{'is not a field' if len(other) == 1 else 'are not fields'} of {op}, "
                     f"which takes {_listed([f'`{k}`' for k in TAKES[op]])}")
        raise EditRefused(said + ".")
    return op


def plan(edits, lines: list[str], has_stamp: bool, earlier=(),
         moved: list[str] | None = None) -> list[tuple[int, int, list[str]]]:
    """Check every edit against the file as it is. Returns (first, last, new lines) per
    edit, sorted from the top: lines first..last are replaced; an insert is (a+1, a).
    With a stamp on line 1, the top of the note is below it; the stamp's own anchor
    only counts as a place to insert after, whatever its hash (it changes every save).
    `earlier`: the note's earlier texts as lines, newest first, which an anchor read
    before a save is followed through (`moved` says where it went). An insert right
    under lines another edit replaces joins that edit."""
    if not isinstance(edits, list) or not edits:
        raise EditRefused("`edits` needs at least one edit.")
    bad: list[str] = []
    planned = []
    for i, e in enumerate(edits, 1):
        op = _op(i, e)
        text = e.get("text") if op != "delete" else None
        new = text.replace("\r\n", "\n").split("\n") if text else []
        if new and new[-1] == "" and len(new) > 1:
            new.pop()
        if op == "insert":
            after = e["after"]
            if str(after) in ("start", "0") or (has_stamp and _number(after) == 1):
                a = 1 if has_stamp else 0
            elif str(after) == "end":
                a = len(lines)
            else:
                a = _anchor(after, lines, bad, earlier, moved)
                if not a:
                    continue
            planned.append((a + 1, a, new))
        else:
            if has_stamp and 1 in (_number(e["start"]), _number(e.get("end"))):
                raise EditRefused(STAMP_KEPT)
            s = _anchor(e["start"], lines, bad, earlier, moved)
            end = _anchor(e["end"], lines, bad, earlier, moved) \
                if e.get("end") is not None else s
            if not s or not end:
                continue
            if end < s:
                raise EditRefused(f"edit {i}: `end` ({end}) is above `start` ({s}).")
            planned.append((s, end, [] if op == "delete" else new))
    if bad:
        raise EditRefused("\n\n".join(bad) + "\n\nUse these anchors and send all the "
                          "edits again.")
    planned.sort(key=lambda x: (x[0], x[1]))
    joined: list[tuple[int, int, list[str]]] = []
    for s2, e2, new2 in planned:
        if joined:
            s1, e1, new1 = joined[-1]
            if e1 >= s1 and e2 < s2 and s2 - 1 == e1:
                joined[-1] = (s1, e1, new1 + new2)      # an insert right under a replace
                continue
            # an insert after line a is (a+1, a): it clashes with an edit that changes line a
            if s2 <= e1 or (e2 < s2 and s2 - 1 <= e1):
                raise EditRefused(f"two edits touch the same lines ({_span(s1, e1)} and "
                                  f"{_span(s2, e2)}). Put them into one edit.")
        joined.append((s2, e2, new2))
    return joined


def _span(s: int, e: int) -> str:
    return f"after line {e}" if e < s else f"line {s}" if s == e else f"lines {s} to {e}"


def apply(lines: list[str], planned) -> tuple[list[str], list[tuple[int, int]]]:
    """Apply from the bottom up, so the line numbers of the edits above hold. Returns
    the new lines and, per edit, the new lines to show (one line of context each side,
    close regions merged)."""
    out = list(lines)
    for s, e, new in reversed(planned):
        out[s - 1:e] = new
    regions: list[tuple[int, int]] = []
    shift = 0
    for s, e, new in planned:
        a = s + shift
        b = a + len(new) - 1
        shift += len(new) - (e - s + 1)
        a, b = max(1, a - 1), min(len(out), max(b, a) + 1)
        if regions and a <= regions[-1][1] + 1:
            regions[-1] = (regions[-1][0], max(b, regions[-1][1]))
        else:
            regions.append((a, b))
    return out, regions
