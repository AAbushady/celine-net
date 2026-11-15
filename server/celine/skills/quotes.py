"""The quote check: words the agent gives the user that it wrote itself.

Words it puts in the user's mouth (`you said "..."`, `Alex: "..."`, `your words "..."`,
or unquoted after "you said" or "you told me") are checked against who wrote them first.
They are its own when the first place they appear at all is one of its replies or its
intercom messages (a sub-agent's are not its own), and nothing the user wrote is like
them (rapidfuzz, so a quote with the user's typos cleaned up is still the user's). Words a
tool showed it first (a page, a film's subtitles, an archive) or another agent wrote are
left alone: the check names only the case the record settles.

It never changes what the agent wrote. It tells the agent: in the result of the tool that
wrote the words (notebook__write, notebook__edit, intercom__send), and for a reply in a
chat at the end of the user's next message (`[quote check]`, PastConversations
message_context), which the user does not see in the chat. A daydream that read a line of
its own as the user's, and acted on it as the user's wish, is the case it was made for.
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone

from ..core.ids import iso, when

HEAD = "[quote check]"
MOST = 3                    # findings told at once
MIN_WORDS, MIN_CHARS = 3, 12
CLAUSE_WORDS, CLAUSE_CHARS = 4, 16      # unquoted words after "you said"
LIKE_USER = 85.0            # rapidfuzz partial_ratio: the user wrote words like these
GAP = 25                    # characters between a cue and its quotation

QUOTE = re.compile(r"[\"“„]([^\"“”„\n]{8,300})[\"”“]")
SPEECH = (r"(?:said|says|say|wrote|writes|write|told me|tells me|asked|asks|put it|"
          r"called it|calls it|answered|replied|typed|meant)")
FILLER = r"(?:\s+(?:just|once|also|even|literally|actually|already|then|yourself))?"
WORDS = r"(?:words|line|answer|reply|question|message|phrase)"


@dataclass(frozen=True)
class Misquote:
    words: str                  # as it quoted them
    to_user: bool               # given to the user by "you" or their name, not a pronoun
    at: str                     # when it wrote them first
    conversation_id: str | None
    message_id: str | None
    intercom_id: int | None


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).casefold().replace("’", "'")
    return " ".join(re.sub(r"[^\w\s]", " ", s).split())


PRONOUNS = {"he": ("he", "his"), "she": ("she", "her"), "they": ("they", "their")}


def own_pronoun(cfg) -> str:
    """The agent's own pronouns (she, he, they, it): never the user in its notes."""
    agent = getattr(cfg, "agent", None)
    return str(getattr(agent, "pronouns", None) or getattr(cfg, "pronouns", "she"))


def _cues(user: str, you: bool, they: bool, own: str
          ) -> tuple[re.Pattern[str], re.Pattern[str], re.Pattern[str] | None]:
    """(a cue right before a quotation, a cue right after one, a cue before unquoted
    words). `you`: "you" is the user (a reply or a message to them). `they`: "he", "she"
    or "they" may be the user (its notes), all but its `own`, which is itself."""
    name = re.escape(user)
    others = [p for k, p in PRONOUNS.items() if k != own] if they else []
    who = [name] + (["you"] if you else []) + [p[0] for p in others]
    whose = [f"{name}(?:'|’)s"] + (["your"] if you else []) + [p[1] for p in others]
    said = rf"\b(?:{'|'.join(who)})(?:'ve|'d)?{FILLER}\s+{SPEECH}\b"
    before = re.compile(
        rf"(?:(?:{said}|\b(?:{'|'.join(whose)})\s+(?:own\s+)?{WORDS}\b)"
        rf"[^\"“”\n.!?]{{0,{GAP}}}"
        rf"|\b{name}(?:,[^\"“\n]{{0,25}})?:\s*)$", re.I)
    after = re.compile(rf"\s*[,.!?]?\s*(?:{'|'.join(who)})\s+{SPEECH}\b", re.I)
    unquoted = None
    if you:
        unquoted = re.compile(
            rf"\b(?:you|{name})(?:'ve|'d)?{FILLER}\s+(?:said|wrote|told me|asked)\s+"
            rf"(?:that\s+|to\s+)?(?![\"“„])([^,.;:!?\n\"“”]{{{CLAUSE_CHARS},200}})",
            re.I)
    return before, after, unquoted


def claims(text: str, user: str, *, you: bool, they: bool, own: str = "she"
           ) -> list[tuple[str, bool]]:
    """The words `text` gives the user: (the words as written, given by "you" or the
    user's name rather than a pronoun)."""
    before, after, unquoted = _cues(user, you, they, own)
    pronoun = re.compile(r"\b(?:he|she|they|his|her|their)\b[^\"“]*$", re.I)
    out: list[tuple[str, bool]] = []
    for m in QUOTE.finditer(text):
        pre, post = text[max(0, m.start() - 80):m.start()], text[m.end():m.end() + 40]
        cue = before.search(pre) or after.match(post)
        if not cue:
            continue
        n = _norm(m[1])
        if len(n) >= MIN_CHARS and len(n.split()) >= MIN_WORDS:
            out.append((m[1].strip(" ,.;:"), not pronoun.match(cue[0].lstrip(" ,.!?"))))
    for m in unquoted.finditer(text) if unquoted else ():
        n = _norm(m[1])
        if len(n) >= CLAUSE_CHARS and len(n.split()) >= CLAUSE_WORDS:
            out.append((m[1].strip(), True))
    return out


def _who(c: sqlite3.Connection, addr: str | None) -> str:
    """An intercom sender as the check reads it: user, agent (one of its own chats, not a
    sub-agent's) or other."""
    if addr == "user":
        return "user"
    if addr and addr.startswith("agent:"):
        r = c.execute("SELECT agent_of FROM conversations WHERE id=?", (addr[6:],)).fetchone()
        return "agent" if r is not None and r[0] is None else "other"
    return "other"


def _first(c: sqlite3.Connection, n: str, skip_messages: set[str],
           skip_intercom: set[int], before: str) -> dict | None:
    """The first place before `before` that the words `n` (normalized) appear, as {who,
    at, conversation_id, message_id, intercom_id}; who is user, agent, tool or other."""
    key = max(n.split(), key=len)
    found: list[dict] = []
    for r in c.execute(
            "SELECT m.id, m.conversation_id, m.role, m.author, m.intercom_id, m.created_at, "
            "m.content, c.agent_of FROM messages m JOIN conversations c ON c.id = "
            "m.conversation_id WHERE m.content LIKE ? AND m.created_at < ?",
            (f"%{key}%", before)):
        if r["id"] in skip_messages or r["intercom_id"] in skip_intercom \
                or n not in _norm(r["content"] or ""):
            continue
        if r["role"] == "tool":
            who = "tool"
        elif r["role"] == "assistant":
            who = "agent" if r["agent_of"] is None else "other"
        elif r["author"] == "user":
            who = "user"
        else:
            iv = c.execute("SELECT from_addr FROM intercom WHERE id=?",
                           (r["intercom_id"],)).fetchone()
            who = _who(c, iv[0] if iv else None)
        found.append({"who": who, "at": r["created_at"], "conversation_id":
                      r["conversation_id"], "message_id": r["id"], "intercom_id": None})
    for r in c.execute("SELECT id, from_addr, created_at, text FROM intercom WHERE text LIKE ? "
                       "AND created_at < ?", (f"%{key}%", before)):
        if r["id"] in skip_intercom or n not in _norm(r["text"] or ""):
            continue
        found.append({"who": _who(c, r["from_addr"]), "at": r["created_at"],
                      "conversation_id": None, "message_id": None, "intercom_id": r["id"]})
    return min(found, key=lambda f: f["at"]) if found else None


def _users(c: sqlite3.Connection, key: str, before: str) -> list[str]:
    """What the user wrote before `before` that holds `key` loosely: their messages and
    intercom messages with a word of the quotation's in them."""
    like = f"%{key}%"
    return [r[0] or "" for r in c.execute(
        "SELECT content FROM messages WHERE role='user' AND author='user' AND content LIKE ? "
        "AND created_at < ? UNION ALL SELECT text FROM intercom WHERE from_addr='user' AND "
        "text LIKE ? AND created_at < ?", (like, before, like, before))]


def _the_users(c: sqlite3.Connection, n: str, before: str) -> bool:
    """The user wrote words like `n` before `before`: a cleaned-up typo is still theirs."""
    from rapidfuzz import fuzz
    words = sorted(set(n.split()), key=len, reverse=True)[:3]
    for key in words:
        if any(fuzz.partial_ratio(n, _norm(t)) >= LIKE_USER for t in _users(c, key, before)):
            return True
    return False


def misquotes(c: sqlite3.Connection, text: str, user: str, *, you: bool, they: bool,
              own: str = "she", before: str | None = None,
              skip_messages: set[str] | frozenset[str] = frozenset(),
              skip_intercom: set[int] | frozenset[int] = frozenset()) -> list[Misquote]:
    """The words `text` gives the user that are the agent's own (the module docstring),
    at most MOST. `own`: its own pronouns. `before`: when it wrote `text` (now when left
    out): what anyone wrote after it is no source, and the user's next message is often
    the very correction ("I never said ..."). `skip_*`: the rows that hold `text` itself."""
    before = before or iso(datetime.now(timezone.utc))
    out: list[Misquote] = []
    seen: set[str] = set()
    for words, to_user in claims(text, user, you=you, they=they, own=own):
        n = _norm(words)
        if n in seen:
            continue
        seen.add(n)
        first = _first(c, n, set(skip_messages), set(skip_intercom), before)
        if first is None or first["who"] != "agent" or _the_users(c, n, before):
            continue
        out.append(Misquote(words, to_user, first["at"], first["conversation_id"],
                            first["message_id"], first["intercom_id"]))
        if len(out) == MOST:
            break
    return out


def where(c: sqlite3.Connection, m: Misquote, tz) -> str:
    """Where it wrote the words first, as past_conversations__read finds it."""
    at = when(m.at, tz)
    if m.conversation_id is None:
        return f"your intercom message #{m.intercom_id} ({at})"
    from .conversations import _items
    title = (c.execute("SELECT title FROM conversations WHERE id=?",
                       (m.conversation_id,)).fetchone() or [None])[0] or "Untitled"
    n = next((i["n"] for i in _items(c, m.conversation_id)
              if i["message"]["id"] == m.message_id), None)
    num = f", #{n}" if n else ""
    return f'"{title}" (conversation {m.conversation_id[:8]}{num}, {at})'


def line(c: sqlite3.Connection, m: Misquote, user: str, tz, here: str) -> str:
    """One finding as the agent reads it: `here` says where it gave the words ("in this
    note")."""
    given = f"given to {user}" if m.to_user else "quoted as someone else's"
    return (f'The words "{m.words}", {given} {here}, are your own: you wrote them first in '
            f"{where(c, m, tz)}, and {user} never wrote them.")


def of_reply(c: sqlite3.Connection, mid: str, user: str) -> list[Misquote]:
    """The misquotes in the reply before user message `mid`: its replies since the user
    message before that one."""
    texts: list[tuple[str, str, str]] = []
    pid = (c.execute("SELECT parent_id FROM messages WHERE id=?", (mid,)).fetchone()
           or [None])[0]
    while pid:
        r = c.execute("SELECT id, parent_id, role, content, created_at FROM messages "
                      "WHERE id=?", (pid,)).fetchone()
        if r is None or r["role"] == "user":
            break
        if r["role"] == "assistant" and (r["content"] or "").strip():
            texts.append((r["id"], r["content"], r["created_at"]))
        pid = r["parent_id"]
    ids = {i for i, _, _ in texts}
    out: list[Misquote] = []
    for _, text, at in reversed(texts):
        out += misquotes(c, text, user, you=True, they=False, before=at, skip_messages=ids)
    return out[:MOST]
