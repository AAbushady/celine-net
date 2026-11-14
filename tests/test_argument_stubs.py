"""An aged call's long string arguments go shortened on the wire (compact.short_arguments):
its first line and a mark that names the call and how to read it back; the JSON stays
valid and the stored call never changes."""
from __future__ import annotations

import json

import pytest

from chat_fixtures import user_says
from fake_backend import FakeBackend

from celine.boot import load
from celine.conversations import store as conversations, tree
from celine.skills import ToolResult
from celine.turns import compactions, context
from celine.turns import store as turns

pytestmark = pytest.mark.anyio

SCRIPT = "cat > /tmp/build.sh <<'EOF'\n" + "".join(
    f"echo step {i}: compile the module number {i} with its flags\n" for i in range(200)) + "EOF"


async def _heredoc_call(store, cid) -> tuple[str, str]:
    await user_says(store, cid, "write the build script")
    turn = await turns.claim(store, "w")
    tid = turn["id"]
    mid = await tree.start_assistant_message(store, cid, turn["root_message_id"], tid)
    await tree.finish_assistant_message(store, cid, mid, "")
    call = {"id": f"{tid}:0:0", "name": "shell__run",
            "arguments": json.dumps({"command": SCRIPT, "tab": "main"}),
            "mutating": True, "mode": "blocking"}
    await turns.record_tool_calls(store, cid, mid, [call])
    await turns.finish_tool_call(store, cid, mid, call["id"], "shell__run",
                                 ToolResult(content="$ done"), "ok", None)
    a = await tree.start_assistant_message(store, cid, await tree.active_leaf(store, cid), tid)
    await tree.finish_assistant_message(store, cid, a, "Written.")
    await turns.transition(store, tid, cid, "done")
    return call["id"], mid


READBACK = [{"type": "function", "function": {"name": "past_conversations__read_tool_call",
                                              "parameters": {"type": "object"}}}]


def _sent(built, call_id) -> str:
    return next(t["function"]["arguments"] for m in built.messages
                for t in m.get("tool_calls") or () if t["id"] == call_id)


async def test_an_aged_heredoc_call_goes_short_and_valid(store):
    cfg = load()
    cid = await conversations.create(store)
    call_id, _ = await _heredoc_call(store, cid)
    newest_id, mid = await _heredoc_call(store, cid)
    fresh = await context.build(store, cfg, FakeBackend(), cid, system="S", tools=[],
                                mode="dry")
    assert json.loads(_sent(fresh, call_id))["command"] == SCRIPT, "verbatim until aged"

    await compactions.set_aged_through(store, cid, mid, {"results": 2})
    bare = await context.build(store, cfg, FakeBackend(), cid, system="S", tools=[],
                               mode="dry")
    assert json.loads(_sent(bare, call_id))["command"] == SCRIPT, \
        "no read-back tool offered: the argument goes whole"
    aged = await context.build(store, cfg, FakeBackend(), cid, system="S", tools=READBACK,
                               mode="dry")
    args = json.loads(_sent(aged, call_id))
    assert args["tab"] == "main", "a short value stays"
    assert args["command"].startswith("cat > /tmp/build.sh <<'EOF' … [202 lines, about ")
    assert args["command"].endswith(
        f"tokens, shortened; the call is kept: {call_id}; read it back with "
        f"past_conversations__read_tool_call(id=\"{call_id}\")]")
    assert len(_sent(aged, call_id)) < 400
    again = await context.build(store, cfg, FakeBackend(), cid, system="S", tools=READBACK,
                                mode="dry")
    assert _sent(again, call_id) == _sent(aged, call_id), "byte-identical every build"
    stored = await store.read(lambda c: c.execute(
        "SELECT arguments FROM tool_calls WHERE id=?", (call_id,)).fetchone()[0])
    assert json.loads(stored)["command"] == SCRIPT, "the row never changes"
    assert json.loads(_sent(aged, newest_id))["command"] == SCRIPT, \
        "the newest aged call of a tool goes whole, so one whole call stays to copy (bug 76)"


async def test_the_whole_call_of_a_tool_moves_only_with_the_mark(store):
    cfg = load()
    cid = await conversations.create(store)
    first, _ = await _heredoc_call(store, cid)
    second, mid = await _heredoc_call(store, cid)
    await compactions.set_aged_through(store, cid, mid, {"results": 2})

    def build():
        return context.build(store, cfg, FakeBackend(), cid, system="S", tools=READBACK,
                             mode="dry")
    before = await build()
    third, later = await _heredoc_call(store, cid)
    after = await build()
    for call in (first, second):
        assert _sent(after, call) == _sent(before, call), "a new call changes no earlier bytes"
    assert json.loads(_sent(after, second))["command"] == SCRIPT
    assert json.loads(_sent(after, third))["command"] == SCRIPT, "not aged"

    await compactions.set_aged_through(store, cid, later, {"results": 1})
    moved = await build()
    assert "shortened; the call is kept" in _sent(moved, second), "older than the newest now"
    assert json.loads(_sent(moved, third))["command"] == SCRIPT, "the newest aged call"


def test_the_mark_names_the_argument_and_call_it_shortened():
    from celine.turns import compact
    short = json.loads(compact.short_arguments(json.dumps({"command": SCRIPT, "tab": "a"}),
                                               "t1:0:0"))
    assert compact.marked(short) == ("command", "t1:0:0")
    assert compact.marked({"files": [{"content": short["command"]}]}) == ("files", "t1:0:0")
    quoted = f"the app wrote {short['command']} into my file, then I fixed it"
    assert compact.marked({"content": quoted}) is None, "a text that quotes it runs"
    assert compact.marked({"command": SCRIPT}) is None


def test_the_mark_copied_with_its_counts_reworded_is_still_the_mark():
    from celine.turns import compact
    # bug 72: items sent as the text of an aged call, its mark copied as "chars"
    items = ('[{"preset": "krea2-turbo", "width": 1376, "height": 1152, "loras": '
             '["krea_2/a:0.8", "krea_2/Bondage_A… [285 chars, about 776 tokens, shortened; '
             'the call is kept: t3:8:0; read it back with '
             'past_conversations__read_tool_call(id="t3:8:0")]')
    assert compact.marked({"items": items, "count": "1"}) == ("items", "t3:8:0")
    assert compact.marked({"items": [{"prompt": f"a cat {items}"}]}) == ("items", "t3:8:0")
