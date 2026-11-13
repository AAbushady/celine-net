"""Feeling probes (affect.py): only the agent's own turns ask for readings, and only from a
provider with Feeling probes on; a reply's readings are kept per message, a probe the server
could not compute as null; a reply without them keeps nothing; a purged conversation takes its
readings with it."""
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
from celine.skills import Registry
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


async def test_only_the_agents_own_turn_on_a_provider_with_probes_asks_for_readings(llm):
    assert (await body_of(llm, affect=True))["affect"] == {"probe": list(affect.PROBES)}
    assert "affect" not in await body_of(llm), "a guard check, a title, the appraisal"
    llm.cfg.settings.roles["chat"] = {"chain": ["plain/small"]}
    assert "affect" not in await body_of(llm, affect=True), "a server without the patch"


def test_a_probe_the_server_could_not_compute_is_null():
    raw = {"affect": {"probe": {"valence": {"mean": 0.4, "last": -0.1}, "tired": None,
                                "lonely": {"mean": float("nan"), "last": None},
                                "arousal": {"mean": True, "last": "x"}},
                      "tokens": 37}}
    values, tokens = affect.readings(raw)
    assert values["valence"] == {"mean": 0.4, "last": -0.1} and tokens == 37
    assert values["tired"] is values["lonely"] is values["arousal"] is None
    assert values["longing"] is None, "a probe it did not send"
    assert affect.readings({"model": "big"}) is None and affect.readings(None) is None


async def test_a_reply_keeps_its_readings_and_a_purge_takes_them(store):
    cfg = boot.load()
    raw = {"model": "big", "affect": {"probe": {"valence": {"mean": 0.5, "last": 0.2}},
                                      "tokens": 2}}
    b = Script([[TextDelta("hi"), Done(reason="stop", raw=raw)],
                [TextDelta("again"), Done(reason="stop", raw={"model": "big"})]])
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
    await conversations.delete(store, cid)
    assert await conversations.purge(store, cfg, cid) == [cid]
    assert await store.read(rows) == []
