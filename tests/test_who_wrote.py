"""Who wrote what the agent reads: the legend's rule, the count in a turn's tail, and the
summary's input, where the user's name stands only over what the user typed."""
from __future__ import annotations

import pytest

from chat_fixtures import user_says
from compaction_fixtures import seed_exchange

from celine.boot import load
from celine.conversations import store as conversations, tree
from celine.intercom import send
from celine.turns import compact, legend
from celine.turns.context import _Item
from celine.turns.loads import Carried
from celine.turns.prompt import count_line, tail_text, wrote
from fake_backend import FakeBackend

pytestmark = pytest.mark.anyio


async def _reply(store, cid, mid, tid, text="hi") -> None:
    reply = await tree.start_assistant_message(store, cid, mid, tid)
    await tree.finish_assistant_message(store, cid, reply, text, None)
    await store.write(lambda c: c.execute("UPDATE turns SET state='done'"))


async def test_the_tail_counts_what_each_wrote_in_its_own_unit(store):
    cfg = load()
    user = cfg.substitute("{{username}}")
    cid = await conversations.create(store)
    mid, tid, _ = await user_says(store, cid, "hello")
    assert await store.read(lambda c: wrote(c, cid)) is None, "before it wrote anything"
    assert "written by you" not in await tail_text(store, cfg, None, FakeBackend(), cid, Carried())
    await _reply(store, cid, mid, tid)
    await user_says(store, cid, "and again")
    line = f"In this chat so far: 2 messages typed by {user}, 1 reply written by you."
    assert cfg.substitute(await store.read(lambda c: wrote(c, cid))) == line
    tail = (await tail_text(store, cfg, None, FakeBackend(), cid, Carried())).split("\n\n")
    assert line in tail and tail.index(line) > 1, "after the date and the time"


async def test_a_reply_of_many_steps_is_one_reply_and_its_calls(store):
    """A turn's steps are message rows of their own that only make calls: the count says
    one reply and the calls, so it adds up against what the chat shows."""
    cid = await conversations.create(store)
    await seed_exchange(store, cid, "look it up", [("arch__find", "a"), ("arch__find", "b"),
                                                  ("notebook__read", "c")])
    assert await store.read(lambda c: wrote(c, cid)) == (
        "In this chat so far: 1 message typed by {{username}}, 1 reply written by you, "
        "3 tool calls made by you.")
    out = await send(store, "system:app", f"agent:{cid}", "The box was reset.", kind="notice")
    assert out["message_id"], "it landed in the idle chat"
    assert await store.read(lambda c: wrote(c, cid)) == (
        "In this chat so far: 1 message typed by {{username}}, 1 reply written by you, "
        "3 tool calls made by you, 1 Intercom message.")


def test_the_count_says_only_what_there_is():
    assert count_line(3, 0, 0, 1) is None, "nothing of its own yet"
    assert count_line(0, 1, 0, 0) == "In this chat so far: 1 reply written by you."
    assert count_line(0, 0, 4, 2) == \
        "In this chat so far: 4 tool calls made by you, 2 Intercom messages."
    assert count_line(3, 2, 25, 0) == ("In this chat so far: 3 messages typed by {{username}}, "
                                       "2 replies written by you, 25 tool calls made by you.")


def test_the_summarys_input_names_the_user_only_over_what_the_user_typed():
    typed = _Item(0, {"id": "m1", "role": "user", "author": "user", "content": "I am tired.",
                      "context": "[your memory] sections of your notes\n### diary/x.md\n"
                                 "Joe said he failed.",
                      "tail": "Today is Tuesday."}, None, None, "Joe")
    report = _Item(1, {"id": "m2", "role": "user", "author": "intercom", "content": "done",
                       "intercom": {"kind": "report", "from": "agent:x", "from_name": "scout",
                                    "id": 4, "data": {}}}, None, None, "Joe")
    mine = _Item(2, {"id": "m3", "role": "assistant", "author": "model",
                     "content": "Rest then."}, None, None, "Joe")
    blocks = compact.transcript([it.as_dict() for it in (typed, report, mine)], "Joe", "Celine")
    assert blocks == [
        "Joe: I am tired.",
        f"{compact.ADDED}\n[your memory] sections of your notes\n### diary/x.md\n"
        "Joe said he failed.\n\nToday is Tuesday.",
        "[Report from scout (#4)]\ndone",
        "Celine: Rest then.",
    ]
    # a message handed over without its author reads as typed, as before
    assert compact.transcript([{"role": "user", "content": "hey"}], "Joe", "Celine") == ["Joe: hey"]


def test_the_summary_prompt_and_the_legend_say_whose_words_are_whose():
    cfg = load()
    name, user = cfg.substitute("{{name}}"), cfg.substitute("{{username}}")
    from celine import prompts
    told = prompts.fill(cfg, compact.__file__, "compact.md", words=100)
    assert f'"{user}:"' in told and f'"{compact.ADDED}"' in told
    assert f"quote {user} only from {user}'s own blocks" in told and name in told
    text = legend.legend(cfg, guarded=False)
    assert f"{user} typed the text right under the time line" in text
    assert "your notes are your own writing" in text
    for banned in ("—", "–", " · "):
        assert banned not in told and banned not in text
