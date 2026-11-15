"""The quote check (skills/quotes.py): words the agent gives the user that it wrote
itself are told to it, in a note's save, an intercom message's answer and the block on
the user's next message; the user's own words, what a tool showed it, a paraphrase and
its own pronouns are left alone. Bug #80: a daydream read its own "send the fourth
whenever" as the user's."""
from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from celine.boot import load
from celine.conversations import store as conversations, tree
from celine.core.services import Services
from celine.intercom import Intercom
from celine.intercom.skill import IntercomSkill
from celine.skills import ToolContext, quotes
from celine.skills.conversations import PastConversations
from celine.skills.notebook import Notebook

pytestmark = pytest.mark.anyio

LINE = "send the fourth whenever"
UTC = ZoneInfo("UTC")
WHERE = 'you wrote them first in "Last day conversation" (conversation {}, #3, Thu 1 Oct 21:27)'


@pytest.fixture
def cfg():
    c = load()
    c.user = "Alex"
    c.timezone = "UTC"
    return c


def _at(c, mid: str, at: str) -> None:
    c.execute("UPDATE messages SET created_at=? WHERE id=?", (f"2026-10-01T{at}:00.000+00:00", mid))


async def _music(store) -> str:
    """The chat where it wrote the line itself on Thu 1 Oct at 21:27 UTC, after the
    user's songs (with his typo)."""
    cid = await conversations.create(store, "Last day conversation")

    def fn(c):
        m = tree.append(c, store, cid, None, "user",
                        "All of then a but to be honest, B’s motoin hits different though",
                        "user")
        r = tree.append(c, store, cid, m["id"], "assistant",
                        "B's motion hits different, noted.", "model")
        s = tree.append(c, store, cid, r["id"], "assistant", f"\n\n{LINE}.", "model")
        for row, at in ((m, "21:00"), (r, "21:01"), (s, "21:27")):
            _at(c, row["id"], at)
    await store.write(fn)
    return cid


async def _found(store, text, **kw):
    return await store.read(lambda c: quotes.misquotes(c, text, "Alex", **kw))


async def _line(store, m, here):
    return await store.read(lambda c: quotes.line(c, m, "Alex", UTC, here))


async def test_its_own_line_given_to_the_user_is_found_with_where_it_wrote_it(store, cfg):
    music = await _music(store)
    for text in (f'you said "{LINE}", so I made one', f"you said {LINE}, so I made one.",
                 f'Alex, 1 Oct 21:27: "{LINE}."', f'"{LINE}," you said'):
        [m] = await _found(store, text, you=True, they=False)
        assert (m.words, m.conversation_id, m.to_user) == (LINE, music, True), text
    assert await _line(store, m, "in this note") == (
        f'The words "{LINE}", given to Alex in this note, are your own: '
        f"{WHERE.format(music[:8])}, and Alex never wrote them.")
    [he] = await _found(store, f'At 21:27 he said "{LINE}."', you=False, they=True)
    assert not he.to_user
    assert (await _line(store, he, "here")).startswith(
        f'The words "{LINE}", quoted as someone else\'s here, are your own')


async def test_the_users_words_what_a_tool_showed_and_a_paraphrase_are_left_alone(store, cfg):
    music = await _music(store)
    # it wrote "B's motion hits different" first, but he wrote it too, with a typo
    assert await _found(store, 'you said "B\'s motion hits different"', you=True,
                        they=False) == []
    film = await conversations.create(store, "Film")

    def fn(c):
        m = tree.append(c, store, film, None, "user", "watch it with me", "user")
        r = tree.append(c, store, film, m["id"], "assistant", "", "model")
        t = tree.append(c, store, film, r["id"], "tool",
                        "12:40 Come on, you old bucket of bolts!", "model")
        s = tree.append(c, store, film, t["id"], "assistant",
                        "Come on, you old bucket of bolts, ha.", "model")
        for row, at in ((m, "20:00"), (r, "20:01"), (t, "20:02"), (s, "20:03")):
            _at(c, row["id"], at)
    await store.write(fn)
    assert await _found(store, 'he says "Come on, you old bucket of bolts"', you=False,
                        they=True) == [], "the subtitles showed it first"
    assert await _found(store, 'you said "send me a fourth one later"', you=True,
                        they=False) == [], "words nobody wrote: a paraphrase"
    assert await _found(store, f'you said "{LINE}"', you=False, they=True) == [], \
        "a message to someone else: its you is not the user"
    assert await _found(store, f'I said "{LINE}"', you=True, they=True) == []
    # its own pronouns are itself (an auditor's "her line"), a path or "to Alex at" no speaker
    for text in (f'Her line "{LINE}" is accurate', f'she said "{LINE}"',
                 f'`opinions/Alex.md`: "{LINE}"', f'Overclaim to Alex at #22: "{LINE}"'):
        assert await _found(store, text, you=False, they=True, own="she") == [], text
    [his] = await _found(store, f'His line "{LINE}" stays', you=False, they=True, own="she")
    assert his.conversation_id == music
    assert await _found(store, f'His line "{LINE}" stays', you=False, they=True,
                        own="he") == []


async def test_a_note_and_an_intercom_message_say_it_in_their_answer(store, cfg):
    music = await _music(store)
    here = await conversations.create(store, "Daydream", kind="mission")
    ctx = ToolContext(here, "t", "x", store, cfg, None)
    nb = Notebook(cfg)
    await nb.seed(store)
    out = (await nb.write(ctx, "people/alex-music.md",
                          f'At 21:27 he said "{LINE}," so I made one.', "music")).content
    assert (f' Quote check: The words "{LINE}", quoted as someone else\'s in this note, are '
            f"your own: {WHERE.format(music[:8])}, and Alex never wrote them.") in out
    clean = (await nb.write(ctx, "people/alex-songs.md", "He sent three songs.", "songs"))
    assert "Quote check" not in clean.content

    skill = IntercomSkill(cfg)
    skill.app = Services(intercom=Intercom(store))
    sent = (await skill.send(ctx, to="user",
                             text=f"you said {LINE}, so I made one instead.")).content
    assert sent.startswith("Sent #")
    assert (f' Quote check: The words "{LINE}", given to Alex in this message, are your '
            f"own: {WHERE.format(music[:8])}") in sent
    again = (await skill.send(ctx, to="user", text="The fourth song is ready.")).content
    assert "Quote check" not in again


async def test_its_reply_is_told_to_it_on_the_users_next_message(store, cfg):
    music = await _music(store)
    chat = await conversations.create(store, "Songs")

    def fn(c):
        a = tree.append(c, store, chat, None, "user", "did you like them?", "user")
        b = tree.append(c, store, chat, a["id"], "assistant",
                        f'you said "{LINE}", so here is a fourth.', "model")
        # his correction holds the same words; it came after, so it is no source
        typed = tree.append(c, store, chat, b["id"], "user",
                            f"I never said {LINE}, that was you", "user")
        d = tree.append(c, store, chat, typed["id"], "assistant",
                        f'you said "{LINE}" though', "model")
        came = tree.append(c, store, chat, d["id"], "user", "[a job ended]", "intercom")
        for row, at in ((a, "22:40"), (b, "22:48"), (typed, "22:49"), (d, "22:50"),
                        (came, "22:51")):
            _at(c, row["id"], at)
        return typed, came
    typed, came = await store.write(fn)
    skill = PastConversations(cfg)
    ctx = ToolContext(chat, "t", "x", store, cfg, None)
    text, report = await skill.message_context(ctx, typed)
    assert text == (f'{quotes.HEAD}\nThe words "{LINE}", given to Alex in your last reply, '
                    f"are your own: {WHERE.format(music[:8])}, and Alex never wrote them.")
    assert report == {"quotes": 1}
    assert await skill.message_context(ctx, came) is None, \
        "a message the intercom delivered: its reply's you may be anyone"
