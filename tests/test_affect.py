"""Feeling probes and steering (affect.py): only the agent's own turns ask for readings, and
only from a provider with Feeling probes on; with Feeling steering on too, they carry the
pushes of its mood and needs, a tint at most and never longing; a reply's readings are kept
per message with the pushes it was read under, a probe the server could not compute as null;
a reply without them keeps nothing; a purged conversation takes its readings with it."""
from __future__ import annotations

import json

import pytest

from chat_fixtures import Script, user_says
from celine import affect, boot
from celine.backends.base import Done, TextDelta
from celine.backends.llm import LLM
from celine.backends.openai import OpenAIBackend
from celine.backends.router import ProviderState
from celine.conversations import store as conversations
from celine.core.bus import EventBus
from celine.core.cancel import Token
from celine.skills import Registry, mind
from celine.turns import store as turns
from celine.turns.runner import run_turn
from settings_fixtures import scratch

pytestmark = pytest.mark.anyio

PROVIDERS = {
    "brain": {"kind": "openai", "base_url": "http://brain:2242/v1", "options": {"affect": True},
              "models": {"big": {"caps": ["chat", "tools"]}}},
    "plain": {"kind": "openai", "base_url": "http://plain:8000/v1",
              "models": {"small": {"caps": ["chat", "tools"]}}},
}
ROLES = {"chat": {"chain": ["brain/big"]}}


class Rec(OpenAIBackend):
    """Keeps the body each call would send instead of sending it."""
    bodies: list[dict] = []

    async def stream(self, messages, tools, body=None, model=None):
        Rec.bodies.append(self.request_body(messages, tools, body, model=model))
        yield Done(reason="stop")


@pytest.fixture
def llm(tmp_path, monkeypatch):
    scratch(tmp_path, monkeypatch, providers=PROVIDERS, roles=ROLES)
    cfg = boot.load()
    cfg.llm = {"queue": {"wait_seconds": 5, "background_wait_seconds": 5}}
    out = LLM(cfg, build=lambda bc: Rec(bc, 5))

    async def probe(pid, **_):
        return ProviderState(reachable=True, loaded=frozenset())
    monkeypatch.setattr(out.router, "probe", probe)
    Rec.bodies = []
    return out


async def body_of(llm, **kw):
    async for _ in llm.bind(role="chat", conversation_id="c1", **kw).stream(
            [{"role": "user", "content": "hi"}], []):
        pass
    return Rec.bodies[-1]


PUSH = [{"name": "tired", "scale": 0.3}]


async def asked(steering: bool, probes: list[str]) -> dict:
    """affect.fields as a turn binds it, with a state that pushes tired."""
    return {"affect": {"probe": list(probes), **({"steer": PUSH} if steering else {})}}


async def test_only_the_agents_own_turn_on_a_provider_with_probes_asks_for_readings(llm):
    assert (await body_of(llm, affect=asked))["affect"] == {"probe": list(affect.PROBES)}
    assert "affect" not in await body_of(llm), "a guard check, a title, the appraisal"
    options = llm.cfg.settings.providers["brain"]["options"]
    options["affect_probes"] = ["lonely", "lonely_clean"]
    assert (await body_of(llm, affect=asked))["affect"] == {
        "probe": ["lonely", "lonely_clean"]}, "the Probes setting names what is read"
    options["affect_steer"] = True
    assert (await body_of(llm, affect=asked))["affect"]["steer"] == PUSH, "steering on"
    options["affect"] = False
    assert "affect" not in await body_of(llm, affect=asked), "steering needs the probes"
    llm.cfg.settings.roles["chat"] = {"chain": ["plain/small"]}
    options["affect"] = True
    assert "affect" not in await body_of(llm, affect=asked), "a server without the patch"


async def test_a_reading_keeps_the_probes_its_request_asked(store):
    cid = await conversations.create(store)
    mid, _, _ = await user_says(store, cid, "hi")
    raw = {"affect": {"probe": {"lonely": {"mean": 0.2, "last": 0.1},
                                "lonely_clean": {"mean": 0.3, "last": 0.2},
                                "longing": {"mean": 0.9, "last": 0.9}}}}
    await affect.keep(store, cid, None, mid, raw, {"probe": ["lonely", "lonely_clean"]})
    row = await store.read(lambda c: c.execute(
        "SELECT readings FROM affect_readings WHERE message_id=?", (mid,)).fetchone())
    assert set(json.loads(row[0])) == {"lonely", "lonely_clean"}, "longing was not asked"


def test_the_state_pushes_a_tint_and_never_longing():
    start = {"rest": 0.3, "connection": 0.3}
    assert mind.pushes((0.05, 0.0, 0.05), start) == [], "a neutral mood, needs at their start"
    assert mind.pushes(None, start) == [], "no feeling yet"
    low = mind.pushes((-0.6, -0.2, -0.9), {"rest": 0.65, "connection": 0.3})
    assert low == [{"name": "valence", "scale": -0.45}, {"name": "arousal", "scale": -0.15},
                   {"name": "dominance", "scale": -0.5}, {"name": "tired", "scale": 0.375}]
    apart = mind.pushes((0.1, 0.1, 0.1), {"rest": 0.0, "connection": 1.0})
    assert apart == [{"name": "lonely", "scale": 0.75}], "pushes under 0.1 left out"
    assert all(p["name"] != "longing" for p in low + apart)


async def test_the_fields_steer_from_the_mood_now(store):
    from datetime import datetime, timezone
    at = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
    cid = await conversations.create(store)
    assert await affect.fields(store, cid, True, now_=at) == {
        "affect": {"probe": list(affect.PROBES)}}, "no feeling yet: probes only"
    for _ in range(3):
        await mind.feel(store, "lonely", 1.0, "nobody wrote", "agent")
    got = (await affect.fields(store, cid, True, now_=datetime.now(timezone.utc)))["affect"]
    assert {p["name"] for p in got["steer"]} >= {"valence", "dominance"}
    assert all(p["scale"] < 0 for p in got["steer"] if p["name"] in ("valence", "dominance"))
    assert "steer" not in (await affect.fields(store, cid, False))["affect"], "steering off"


def test_a_probe_the_server_could_not_compute_is_null():
    raw = {"affect": {"probe": {"valence": {"mean": 0.4, "last": -0.1}, "tired": None,
                                "lonely": {"mean": float("nan"), "last": None},
                                "arousal": {"mean": True, "last": "x"},
                                "longing": {"mean": 0.9, "last": 0.9}},
                      "tokens": 37}}
    values, tokens = affect.readings(raw)
    assert values["valence"] == {"mean": 0.4, "last": -0.1} and tokens == 37
    assert values["tired"] is values["lonely"] is values["arousal"] is None
    assert values["dominance"] is None, "a probe it did not send"
    assert "longing" not in affect.PROBES and "longing" not in values, "never asked, never kept"
    assert affect.readings({"model": "big"}) is None and affect.readings(None) is None


async def test_a_reply_keeps_its_readings_and_a_purge_takes_them(store):
    cfg = boot.load()
    raw = {"model": "big", "affect": {"probe": {"valence": {"mean": 0.5, "last": 0.2}},
                                      "tokens": 2}}
    b = Script([[TextDelta("hi"), Done(reason="stop", raw=raw)],
                [TextDelta("again"), Done(reason="stop", raw={"model": "big"})]])
    b.affect_sent = {"probe": list(affect.PROBES), "steer": PUSH}   # what the request carried
    cid = await conversations.create(store)
    for text in ("hello", "and now"):
        await user_says(store, cid, text)
        turn = await turns.claim(store, "w")
        await run_turn(turn, store, cfg, b, EventBus(), Token(turn["id"]), Registry())

    def rows(c):
        return [dict(r) for r in c.execute("SELECT * FROM affect_readings")]
    [row] = await store.read(rows)
    assert (row["conversation_id"], row["model"], row["tokens"]) == (cid, "big", 2)
    assert json.loads(row["readings"])["valence"] == {"mean": 0.5, "last": 0.2}
    assert json.loads(row["steer"]) == PUSH, "the pushes it was read under"
    await conversations.delete(store, cid)
    assert await conversations.purge(store, cfg, cid) == [cid]
    assert await store.read(rows) == []
