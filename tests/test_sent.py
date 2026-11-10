"""What was sent to a model, searched (celine/turns/sent.py), and the tool that answers
"was this ever sent to me?" from it (past_conversations__find_sent)."""
from __future__ import annotations

import json

import pytest

from chat_fixtures import user_says

from celine.boot import load
from celine.conversations import store as conversations, tree
from celine.skills import ToolContext, ToolResult
from celine.skills.conversations import PastConversations
from celine.turns import sent, store as turns

pytestmark = pytest.mark.anyio
LINE = "week? or ya bedtime once vera done?"


def test_a_request_is_read_as_pieces_in_every_shape_a_backend_sends():
    openai = {"messages": [
        {"role": "system", "content": "You are Ash.\n\nEarlier in this conversation (a summary): tea"},
        {"role": "user", "content": "[Tue 10 Nov 17:22]\nhello"},
        {"role": "assistant", "content": "hi", "reasoning_content": "greet back", "tool_calls": [
            {"id": "c1", "function": {"name": "notebook__write",
                                      "arguments": json.dumps({"content": "a \"quoted\"\nline"})}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "notebook__write", "content": "Saved."}]}
    assert [(p.kind, p.name) for p in sent.pieces(openai)] == [
        ("instructions", None), ("summary", None), ("message", None),
        ("call", "notebook__write"), ("own", None), ("own", None), ("result", "notebook__write")]
    assert sent.find(openai, sent.norm('A "QUOTED"  line')) == [("call", "notebook__write")], \
        "a call's values are searched as they were written, whatever the case and spacing"
    anthropic = {"system": "You are Ash.", "messages": [
        {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "greet back"}, {"type": "text", "text": "hi"},
            {"type": "tool_use", "id": "u1", "name": "web__search", "input": {"q": "tea"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "u1", "content": [{"type": "text", "text": "Oolong"}]}]}]}
    assert [(p.kind, p.name) for p in sent.pieces(anthropic)] == [
        ("instructions", None), ("message", None), ("own", None), ("own", None),
        ("call", "web__search"), ("result", "web__search")]
    assert sent.find(anthropic, "oolong") == [("result", "web__search")]


def test_curly_quotes_and_spacing_do_not_hide_a_text():
    assert sent.norm("I’m  a\nFAILED run") == "i'm a failed run"


async def _request(store, mid: str, tid: str, step: int, messages: list[dict], at: str) -> None:
    def fn(c):
        c.execute("INSERT INTO requests(message_id, step, turn_id, provider, model, body, "
                  "created_at) VALUES(?,?,?,?,?,?,?)",
                  (mid, step, tid, "p", "m", turns.pack_body(c, {"messages": messages}), at))
    await store.write(fn)


async def _chat(store):
    """A chat in which the app sent one real line and the model then wrote an invented
    one into a note: two requests, the second carrying its own call back to it."""
    cid = await conversations.create(store, "Evening")
    asked, tid, _ = await user_says(store, cid, "I’m a failed run")
    await turns.claim(store, "w")
    await store.write(lambda c: c.execute(
        "INSERT INTO message_context(message_id, text, tail) VALUES(?,?,?)",
        (asked, "[your memory] sections of your notes this message brings up:\n\n"
                "### tea/list.md#oolong (0.51, 8 Nov 2026)\nOolong from the hill shop.\n\n"
                "[your mind]\nmood: clear",
         "Today is Tuesday.\n\n[also working now] the run of the Routine Followed sites.")))
    user = {"role": "user", "content":
            "[Tue 10 Nov 17:22]\nI’m a failed run\n\n[your memory] sections of your notes "
            "this message brings up:\n\n### tea/list.md#oolong (0.51, 8 Nov 2026)\nOolong "
            "from the hill shop.\n\n[your mind]\nmood: clear\n\nToday is Tuesday.\n\n"
            "[also working now] the run of the Routine Followed sites."}
    system = {"role": "system", "content": "You are Ash."}
    mid = await tree.start_assistant_message(store, cid, asked, tid)
    await tree.finish_assistant_message(store, cid, mid, "", reasoning="a note about today")
    call = {"id": f"{tid}:0:0", "name": "notebook__write", "mutating": True, "mode": "blocking",
            "arguments": json.dumps({"path": "diary/evening.md",
                                     "content": f"the chat said *\"{LINE}\"*"})}
    await turns.record_tool_calls(store, cid, mid, [call])
    await turns.finish_tool_call(store, cid, mid, call["id"], call["name"],
                                 ToolResult(content="Saved diary/evening.md."), "ok", None)
    await _request(store, mid, tid, 0, [system, user], "2026-11-10T22:24:25.000+00:00")
    back = {"role": "assistant", "content": "", "tool_calls": [
        {"id": call["id"], "function": {"name": call["name"], "arguments": call["arguments"]}}]}
    result = {"role": "tool", "tool_call_id": call["id"], "name": call["name"],
              "content": "Saved diary/evening.md."}
    leaf = await tree.active_leaf(store, cid)
    second = await tree.start_assistant_message(store, cid, leaf, tid)
    await tree.finish_assistant_message(store, cid, second, "Noted.")
    await _request(store, second, tid, 1, [system, user, back, result],
                   "2026-11-10T22:25:19.000+00:00")
    return cid


async def test_the_first_request_that_held_a_text_and_what_kind_of_piece_held_it(store):
    cid = await _chat(store)
    got = await sent.first_sent(store, cid, "i'm a FAILED run")
    assert got["searched"] == 2 and got["skipped"] == 0
    assert got["first"] == {"at": "2026-11-10T22:24:25.000+00:00", "kinds": [("message", None)]}
    assert got["now"], "the newest request still carries it"
    invented = await sent.first_sent(store, cid, LINE)
    assert invented["first"] == {"at": "2026-11-10T22:25:19.000+00:00",
                                 "kinds": [("call", "notebook__write")]}, \
        "it first went out as the model's own call, in the request after the one that made it"
    assert (await sent.first_sent(store, cid, "can't you even remember"))["first"] is None


async def test_the_stored_part_of_a_message_that_held_a_text(store):
    cid = await _chat(store)
    part = lambda text: store.read(lambda c: sent.message_part(c, cid, text))   # noqa: E731
    typed = await part("failed run")
    assert (typed["part"], typed["author"], typed["label"]) == ("text", "user", None)
    recalled = await part("from the hill shop")
    assert (recalled["part"], recalled["label"], recalled["note"]) == (
        "block", "your memory", "tea/list.md#oolong")
    assert (await part("mood: clear"))["label"] == "your mind"
    tail = await part("Followed sites")
    assert (tail["part"], tail["label"]) == ("tail", "also working now")
    assert (await part("Today is Tuesday"))["label"] is None, "a tail's own line has no label"
    assert await part(LINE) is None, "nobody wrote it to the model"


async def test_where_the_model_first_wrote_a_text_itself(store):
    cid = await _chat(store)
    own = await store.read(lambda c: sent.own_first(c, cid, LINE))
    assert (own["kind"], own["name"]) == ("call", "notebook__write")
    assert (await store.read(lambda c: sent.own_first(c, cid, "a note about today")))["kind"] \
        == "thinking"
    assert await store.read(lambda c: sent.own_first(c, cid, "failed run")) is None


async def test_the_tool_says_who_wrote_it_first(store):
    cfg = load()
    cid = await _chat(store)
    skill = PastConversations(cfg)
    ctx = ToolContext(conversation_id=cid, turn_id="t", tool_call_id="x", store=store,
                      config=cfg, emit=None)  # type: ignore[arg-type]
    own = (await skill.find_sent(ctx, text=LINE)).content
    assert own.startswith(f'You wrote "{LINE}" first yourself: at ')
    assert "in the arguments of your call notebook__write. Nothing the app sent you before " \
           "that holds it." in own
    assert "the app sent it back to you as your own earlier words. It is still in what is " \
           "sent to you now." in own
    assert own.splitlines()[-1].startswith("2 requests of this conversation were searched")
    typed = (await skill.find_sent(ctx, text="I'm a failed run")).content
    assert typed.startswith(f"The app sent you \"I'm a failed run\". It first reached you in "
                            f"the message {cfg.user} wrote at ")
    recalled = (await skill.find_sent(ctx, text="oolong from the hill shop")).content
    assert "in the [your memory] block the app put under the message of " in recalled
    assert recalled.splitlines()[0].endswith("from your note tea/list.md#oolong.")
    never = await skill.find_sent(ctx, text="WOW silly, can't you even remember the evening")
    assert never.content.startswith("Nothing the app sent you in this conversation holds")
    assert never.display == {"conversation": cid, "sent": False, "own": False}
    short = await skill.find_sent(ctx, text="vera")
    assert short.is_error and "at least 8 characters" in short.content
