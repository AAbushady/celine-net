"""intercom__start_chat: its text is its first message in the new chat, the chat knows
where it came from, and what the user has from the same place already is not started
again."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from chat_fixtures import user_says

from celine import boot
from fake_backend import FakeBackend
from celine.bugreports import store as bugs
from celine.conversations import store as conversations, tree
from celine.intercom import Intercom
from celine.intercom.skill import IntercomSkill, covered
from celine.skills import ToolContext
from celine.turns import context

pytestmark = pytest.mark.anyio


async def _setup(store):
    cfg = boot.load()
    skill = IntercomSkill(cfg)
    skill.app = SimpleNamespace(intercom=Intercom(store))
    bg = await conversations.for_trigger(store, "trigger:mail.new", "mail.new")

    def did(c):
        m = tree.append(c, store, bg, None, "assistant", "", "model")["id"]
        c.execute("INSERT INTO tool_calls(id, message_id, name, arguments, state, started_at) "
                  "VALUES('k1', ?, 'email__read', '{\"id\": 7}', 'ok', 'x')", (m,))
    await store.write(did)
    ctx = ToolContext(conversation_id=bg, turn_id="t", tool_call_id="c", store=store,
                      config=cfg, emit=None)       # type: ignore[arg-type]
    return cfg, skill, bg, ctx


async def test_the_agents_text_is_its_first_message_and_the_chat_knows_where_it_came_from(store):
    cfg, skill, bg, ctx = await _setup(store)
    out = await skill.start_chat(ctx, title="Invoice due", text="It is due tomorrow.")
    new = out.display["chat"]
    msgs = await store.read(lambda c: c.execute(
        "SELECT role, content, intercom_id FROM messages WHERE conversation_id=?",
        (new,)).fetchall())
    assert [tuple(m) for m in msgs] == [("assistant", "It is due tomorrow.", None)]
    assert await store.read(lambda c: c.execute("SELECT count(*) FROM intercom").fetchone()[0]) \
        == 0, "the chat holds it; an Intercom thread beside it showed it twice"
    assert await store.read(lambda c: c.execute(
        "SELECT count(*) FROM global_events WHERE type='waits.changed'").fetchone()[0]) == 1, \
        "Waiting for you lists it at once"
    row = {"id": new, "active_state": None}         # as the chat list's row has it
    assert await store.read(lambda c: conversations.state_of(c, row)) == {
        "tone": "warn", "words": "Waiting for you"}
    built = await context.build(store, cfg, FakeBackend(), new, system="", tools=[])
    first, answer = [m for m in built.messages if m["role"] != "system"][:2]
    assert first["role"] == "user" and "You started this chat from" in first["content"]
    assert "email__read" in first["content"] and answer["content"] == "It is due tomorrow."
    await user_says(store, new, "paid it")
    assert not await store.read(lambda c: conversations.opened_for_you(c, new)), "you wrote"


async def test_a_second_chat_from_the_same_place_starts_and_lists_the_first(store):
    _, skill, bg, ctx = await _setup(store)
    out = await skill.start_chat(ctx, title="Invoice due", text="It is due tomorrow.")
    chat = out.display["chat"]
    again = await skill.start_chat(ctx, title="Invoice again", text="Still due.")
    assert not again.is_error and again.content.startswith('Started the chat "Invoice again"')
    assert f'chat "Invoice due" (/chat/{chat}, ' in again.content
    assert "not answered yet): It is due tomorrow." in again.content
    out = await skill.start_chat(ctx, title="Two gates that stop  costing me missions",
                                 text=" x ")
    assert not out.is_error
    got = await store.read(lambda c: (
        c.execute("SELECT title FROM conversations WHERE id=?", (out.display["chat"],)).fetchone()[0],
        c.execute("SELECT content FROM messages WHERE conversation_id=?",
                  (out.display["chat"],)).fetchone()[0]))
    assert got == ("Two gates that stop  costing me missions", " x "), "as she wrote it"


async def test_an_open_bug_report_of_the_last_day_is_listed_for_the_agent_to_judge(store):
    _, skill, bg, ctx = await _setup(store)
    rid = (await store.write(lambda c: bugs.file(c, store, bg, "Shell wedge", "high",
                                                 "it hangs", "", "")))["id"]
    out = await skill.start_chat(ctx, title="Shell wedge", text="The shell hangs.")
    assert not out.is_error and f'bug #{rid} "Shell wedge" (open, ' in out.content
    assert "it hangs" in out.content
    # older than the day: neither a report nor a thread counts
    assert await store.read(lambda c: covered(c, bg, "9999", skill.tz)) == []
