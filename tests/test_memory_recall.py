"""[your memory] (skills/recall.py): the sections of the notes whose keys come up in the
talk, graded by embedding similarity, only at or over the floor, at most the cap, the
best first, each once per branch until it changes; not for notes it carries in full,
`load: off`, left out here, outside a sub-agent's folder, nor with automatic context
off; an embedding model that does not answer is said, with nothing instead."""
from __future__ import annotations

import json
import math
import threading
from collections import OrderedDict

import pytest

from chat_fixtures import until, user_says
from notes_fixtures import notes_cfg, select_turn, write_notes

from celine.conversations import store as conversations, tree
from celine.kb.embed import SemanticError
from celine.skills import recall

pytestmark = pytest.mark.anyio

NOOR = ("keys: Noor, climbing\n\nNoor is a friend.\n\n## Climbing\nNoor climbs every "
        "weekend.\n\n## Work\nShe bakes bread at the bakery.\n\n## Empty\n")
WORDS = ("climb", "bakery", "friend")


def _vec(t):
    """How often each of WORDS comes up, plus a little of everything."""
    return [t.lower().count(w) for w in WORDS] + [0.01]


class Model:
    """The embedding model: every text it was given, in calls."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, cfg):
        def embed(texts):
            self.calls.append(texts)
            return [_vec(t) for t in texts]
        return ("fake", 0), "", embed


@pytest.fixture(autouse=True)
def model(monkeypatch):
    m = Model()
    monkeypatch.setattr(recall, "_model", m)
    monkeypatch.setattr(recall, "_kept", OrderedDict())
    return m


def _cfg(tmp_path, **context):
    cfg = notes_cfg(tmp_path)
    cfg.context.update(context)
    cfg.r = lambda key: None
    return cfg


async def _say(store, cfg, cid, text):
    mid, tid, _ = await user_says(store, cid, text)
    return await recall.block(cfg, store, cid, tid, {"id": mid, "content": text})


async def _answer(store, cid, text="ok"):
    """The agent's reply to the newest message."""
    mid, tid = await store.read(lambda c: c.execute(
        "SELECT id, turn_id FROM messages WHERE conversation_id=? AND role='user' "
        "ORDER BY rowid DESC LIMIT 1", (cid,)).fetchone())
    reply = await tree.start_assistant_message(store, cid, mid, tid)
    await tree.finish_assistant_message(store, cid, reply, text, None)
    await store.write(lambda c: c.execute("UPDATE turns SET state='done'"))


async def test_sections_come_back_graded_best_first_over_the_floor(store, tmp_path):
    cfg = _cfg(tmp_path)
    await write_notes(store, cfg, {"people/noor.md": NOOR, "lore/plain.md": "Noor climbing."})
    cid = await conversations.create(store)
    text, report = await _say(store, cfg, cid, "is NOOR climbing today?")
    assert text.startswith(recall.HEAD + " ")
    assert text.index("### people/noor.md#Climbing (1.00, ") < text.index("### people/noor.md (0.")
    assert "bakery" not in text and "lore/plain.md" not in text, "below the floor; no keys"
    states = {c["id"]: c["state"] for c in report["candidates"]}
    assert states == {"people/noor.md#Climbing": recall.SHOWN, "people/noor.md": recall.SHOWN,
                      "people/noor.md#Work": recall.BELOW}
    assert report["candidates"][0]["key"] == "noor" and report["floor"] == 0.5
    assert math.isclose(report["candidates"][-1]["score"], 0.01, abs_tol=0.01)
    assert "—" not in text and "–" not in text


async def test_at_most_the_cap_and_each_section_once_until_it_changes(store, tmp_path):
    cfg = _cfg(tmp_path, memory_sections=1)
    await write_notes(store, cfg, {"people/noor.md": NOOR})
    cid = await conversations.create(store)
    text, report = await _say(store, cfg, cid, "noor climbing?")
    assert "#Climbing" in text and "Noor is a friend" not in text
    assert [c["state"] for c in report["candidates"]] == [recall.SHOWN, recall.NO_ROOM,
                                                          recall.BELOW]
    await store.write(lambda c: c.execute(
        "INSERT INTO message_context(message_id, text, report) VALUES("
        "(SELECT id FROM messages WHERE role='user'), ?, ?)",
        (text, json.dumps({"notebook": report}))))
    await _answer(store, cid, "she is, ask her")
    text, report = await _say(store, cfg, cid, "climbing with noor again")
    assert "#Climbing" not in text and "Noor is a friend" in text, "the next one has room"
    assert report["candidates"][0]["state"] == recall.BEFORE, "shown before on this branch"
    assert report["candidates"][1]["state"] == recall.SHOWN
    await write_notes(store, cfg, {"people/noor.md": NOOR.replace("every weekend",
                                                                  "on Sundays")})
    text, _ = await _say(store, cfg, cid, "noor climbing")
    assert "on Sundays" in text, "it changed: it comes back"


async def test_the_agents_last_reply_counts_and_the_token_cap_holds(store, tmp_path):
    cfg = _cfg(tmp_path, memory_tokens=5)
    await write_notes(store, cfg, {"people/noor.md": NOOR})
    cid = await conversations.create(store)
    await select_turn(store, cfg, None, cid, 1, "hi")
    await store.write(lambda c: c.execute("UPDATE messages SET content='Noor went "
                                          "climbing' WHERE role='assistant'"))
    text, report = await _say(store, cfg, cid, "and then?")
    assert text == "" and {c["state"] for c in report["candidates"]} == {
        recall.NO_ROOM, recall.BELOW}


async def test_never_carried_off_left_out_outside_the_folder_or_with_auto_off(
        store, tmp_path):
    cfg = _cfg(tmp_path)
    await write_notes(store, cfg, {
        "people/noor.md": NOOR, "people/joe.md": "keys: noor\n\nJoe climbs.",
        "lore/tower.md": "keys: noor\nload: off\n\nThe climb.",
        "agents/scout/noor.md": "keys: noor\n\nThe scout climbs.",
        "skills/web.md": "keys: noor\n\nSearch noor on the web."})
    cid = await conversations.create(store)
    _, report = await _say(store, cfg, cid, "noor climbing")
    assert {c["id"].split("#")[0] for c in report["candidates"]} == {
        "people/noor.md", "agents/scout/noor.md"}, \
        "joe's note is always loaded; load: off; a skill's note comes with its load"
    await store.write(lambda c: c.execute("UPDATE conversations SET notes_off=? WHERE id=?",
                                          (json.dumps(["agents/scout/noor.md",
                                                       "people/noor.md"]), cid)))
    assert await _say(store, cfg, cid, "noor climbing") is None, "left out"
    sub = await conversations.create(store, agent_of=cid, agent_name="scout",
                                     notebook_folder="agents/scout")
    _, report = await _say(store, cfg, sub, "noor climbing")
    assert [c["id"] for c in report["candidates"]] == ["agents/scout/noor.md"]
    off = await conversations.create(store)
    await store.write(lambda c: c.execute(
        "UPDATE conversations SET auto_context=0 WHERE id=?", (off,)))
    assert await _say(store, cfg, off, "noor climbing") is None


async def test_an_embedding_model_that_does_not_answer_is_said(store, tmp_path,
                                                                monkeypatch):
    def down(cfg):
        raise SemanticError("No embedding model is set.")
    monkeypatch.setattr(recall, "_model", down)
    cfg = _cfg(tmp_path)
    await write_notes(store, cfg, {"people/noor.md": NOOR})
    cid = await conversations.create(store)
    text, report = await _say(store, cfg, cid, "noor")
    assert text == f"{recall.HEAD} not available now: No embedding model is set."
    assert report["error"] and len(report["candidates"]) == 3


async def test_a_section_is_embedded_once_until_it_changes(store, tmp_path, model):
    """Every message embedded every section again: twenty took longer than the 10 s a
    message waits on a CPU embedder, so most messages had no memory."""
    cfg = _cfg(tmp_path)
    await write_notes(store, cfg, {"people/noor.md": NOOR})
    cid = await conversations.create(store)
    await _say(store, cfg, cid, "noor climbing")
    assert len(model.calls) == 1 and len(model.calls[0]) == 4, "the talk and three sections"
    await _answer(store, cid)
    text, _ = await _say(store, cfg, cid, "climbing with noor again")
    assert model.calls[1] == ["climbing with noor again\nok"], "the talk alone"
    await _answer(store, cid)
    await write_notes(store, cfg, {"people/noor.md": NOOR.replace("every weekend",
                                                                  "on Sundays")})
    await _say(store, cfg, cid, "noor climbing")
    assert len(model.calls[2]) == 2 and "on Sundays" in model.calls[2][1], \
        "the talk and the section that changed"


async def test_a_message_that_stops_waiting_leaves_the_vectors_to_the_next(
        store, tmp_path, monkeypatch):
    go = threading.Event()

    def slow(cfg):
        def embed(texts):
            go.wait()
            return [_vec(t) for t in texts]
        return ("fake", 0), "", embed
    monkeypatch.setattr(recall, "_model", slow)
    monkeypatch.setattr(recall, "EMBED_SECONDS", 0.05)
    cfg = _cfg(tmp_path)
    await write_notes(store, cfg, {"people/noor.md": NOOR})
    cid = await conversations.create(store)
    text, report = await _say(store, cfg, cid, "noor climbing")
    assert text == f"{recall.HEAD} not available now: {recall.LATE}"
    assert recall._late, "the call goes on"
    go.set()
    await until(lambda: not recall._late)
    assert len(recall._kept) == 3
    await _answer(store, cid)
    text, _ = await _say(store, cfg, cid, "noor climbing")
    assert "### people/noor.md#Climbing" in text


async def test_one_long_note_leaves_room_for_every_other(store, tmp_path):
    """Sections are capped per note before the cut, so a note with many sections does
    not crowd out the other notes its keys brought up."""
    cfg = _cfg(tmp_path)
    long = "keys: noor\n\n" + "".join(f"## Day {i}\nA friend.\n\n" for i in range(60))
    await write_notes(store, cfg, {"a/diary.md": long, "people/noor.md": NOOR})
    cid = await conversations.create(store)
    text, report = await _say(store, cfg, cid, "noor climbing")
    ids = [c["id"] for c in report["candidates"]]
    assert sum(i.startswith("a/diary.md") for i in ids) == recall.NOTE_SECTIONS
    assert "people/noor.md#Climbing" in ids and "### people/noor.md#Climbing" in text
    assert len(ids) <= recall.CANDIDATES
