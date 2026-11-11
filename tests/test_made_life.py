"""What a batch makes lives a set number of days (comfyui/life.py): one copy on disk,
cleared at its day unless kept or shown by something durable, said so everywhere it
appears, made again with a new life, and kept or let go by the agent and the user."""
from __future__ import annotations

from chat_fixtures import upload

import json
import os
from datetime import datetime, timedelta, timezone

import anyio
import httpx
import pytest
from fastapi import FastAPI

from celine import agent, attachments
from celine.api import attachments as attachments_api
from celine.backends.llm import LLM
from celine.backends.router import Router
from celine.boot import load
from celine.clock import zone
from celine.comfyui import api as comfy_api, batches as batches_mod, life, server
from celine.conversations import store as conversations
from celine.core.bus import EventBus
from celine.core.ids import iso, when
from celine.core.services import Services
from celine.errors import install_error_handler
from celine.skills.images import ImageGenerator
from celine.skills.music import MusicGenerator
from celine.skills.tool_rows import row as row_words
from settings_fixtures import client, scratch
from test_comfyui import PROVIDERS, ROLES, Fake, _call

pytestmark = pytest.mark.anyio

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)      # when the files in these tests were made
DAYS = timedelta(days=14)                            # the setting's default
LATER = datetime(2100, 1, 1, tzinfo=timezone.utc)


class Varied(Fake):
    """The fake ComfyUI, each image and track with bytes of its own."""

    def __init__(self):
        super().__init__()
        self.made = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        r = super().__call__(request)
        if request.url.path == "/view":
            self.made += 1
            return httpx.Response(200, content=r.content + self.made.to_bytes(4, "big"),
                                  headers={"content-type": r.headers["content-type"]})
        return r


@pytest.fixture
async def app(tmp_path, monkeypatch, store):
    scratch(tmp_path, monkeypatch, providers=PROVIDERS, roles=ROLES)
    monkeypatch.setattr(server, "POLL", 0.01)
    cfg = load()
    fake = Varied()
    llm = LLM(cfg, router=Router(cfg, transport=httpx.MockTransport(fake)))
    fake.llm = llm
    async with anyio.create_task_group() as tg:
        services = Services(cfg=cfg, store=store, llm=llm, bus=EventBus())
        services.batches = batches_mod.Batches(services, tg)
        yield services
        tg.cancel_scope.cancel()


def _web(app) -> FastAPI:
    web = FastAPI()
    install_error_handler(web)
    web.include_router(comfy_api.router)
    web.include_router(attachments_api.router)
    web.state.services = app
    return web


async def _images(app, ctx, n: int = 1) -> tuple[ImageGenerator, dict]:
    """`n` images made by the agent's call `ctx`, waited for. -> (the skill, the batch)."""
    skill = ImageGenerator(app.cfg)
    skill.app = app
    out = await skill.generate(ctx, items=[{"prompt": f"picture {k}"} for k in range(n)],
                               wait=True)
    assert not out.is_error, out.content
    [b] = await batches_mod.list_batches(app.store, "images",
                                         conversation=ctx.conversation_id)
    return skill, b


async def _made_at(store, at: datetime) -> None:
    """Every file a batch made began its life at `at`."""
    await store.write(lambda c: c.execute(
        "UPDATE attachments SET life_at=? WHERE batch_id IS NOT NULL", (iso(at),)))


def _at(cfg, t: datetime) -> str:
    return when(iso(t), zone(cfg))


async def _life(store, aid: str) -> tuple[str, str]:
    r = await store.read(lambda c: c.execute(
        "SELECT life, life_at FROM attachments WHERE id=?", (aid,)).fetchone())
    return r["life"], r["life_at"]


async def test_a_made_file_is_on_disk_once_its_folder_entry_a_link(app):
    b = await app.batches.create("images", [{"prompt": "a"}])
    b = await app.batches.run(b["id"])
    att = b["items"][0]["attachment"]
    blob = attachments.blob_path(app.cfg, att["sha256"])
    [entry] = (app.cfg.data_dir / "images").rglob("*.png")
    assert os.path.samefile(blob, entry) and os.stat(blob).st_nlink == 2
    folder = await app.store.read(lambda c: c.execute(
        "SELECT folder_path FROM attachments WHERE id=?", (att["id"],)).fetchone()[0])
    assert (app.cfg.data_dir / folder) == entry and entry.parent.parent.name == "images"
    assert b["items"][0]["attachment"].get("life") is None, "the row's, never in its items"
    assert batches_mod.view(await batches_mod.get(app.store, b["id"]))["items"][0][
        "attachment"]["life"]["state"] == "temporary"


async def test_it_is_cleared_at_its_day_and_every_place_says_so(app, store, monkeypatch):
    cfg = app.cfg
    cid = await conversations.create(store)
    ctx, _ = await _call(store, cid, "c1")
    skill, b = await _images(app, ctx)
    att = b["items"][0]["attachment"]
    blob = attachments.blob_path(cfg, att["sha256"])
    small = blob.with_name(f"{att['sha256']}.w320.webp")
    small.write_bytes(b"small")
    [entry] = (cfg.data_dir / "images").rglob("*.png")
    await _made_at(store, T0)
    read = (await skill.read(ctx, batch=b["id"][:8])).content
    assert f"Cleared on {_at(cfg, T0 + DAYS)} unless kept." in read

    assert await life.clear(cfg, store, at=T0 + DAYS - timedelta(seconds=1)) == 0
    assert blob.exists() and entry.exists()

    async def events() -> int:
        return await store.read(lambda c: c.execute(
            "SELECT count(*) FROM events WHERE conversation_id=? AND type='images.batch'",
            (cid,)).fetchone()[0])
    before = await events()
    assert await life.clear(cfg, store, at=T0 + DAYS) == 1
    assert not blob.exists() and not small.exists() and not entry.exists()
    assert await _life(store, att["id"]) == ("cleared", iso(T0 + DAYS))
    assert await events() == before + 1, "its batch is published again"

    # the batch, its call's display, the agent's read and list_images say so
    shown = {"state": "cleared", "display": {"words": "Cleared", "tone": "muted"},
             "at": iso(T0 + DAYS)}
    got = batches_mod.view(await batches_mod.get(store, b["id"]))["items"][0]
    assert got["state"] == "done" and got["attachment"]["life"] == shown
    assert got["prompt"] == "picture 0" and got["seed"] == b["items"][0]["seed"]
    display = json.loads(await store.read(lambda c: c.execute(
        "SELECT display FROM tool_calls WHERE id='c1'").fetchone()[0]))
    assert display["items"][0]["attachment"]["life"] == shown
    read = (await skill.read(ctx, batch=b["id"][:8])).content
    assert read.endswith(f"Cleared on {_at(cfg, T0 + DAYS)}.")
    listed = (await skill.list_images(ctx)).content
    assert f", cleared on {_at(cfg, T0 + DAYS)}" in listed and "prompt: picture 0" in listed

    # the file and its tiles answer with the sentence, as a thing that is gone
    async with client(_web(app)) as c:
        for url in (f"/api/attachments/{att['id']}/file",
                    f"/api/attachments/{att['id']}/file?w=320"):
            r = await c.get(url)
            assert r.status_code == 404
            assert r.json()["error"]["message"] == \
                f"The image was cleared on {_at(cfg, T0 + DAYS)}."
        listed = (await c.get("/api/batches?kind=images")).json()["batches"]
        assert listed[0]["items"][0]["attachment"]["life"] == shown

    # a look that finds nothing to clear writes nothing
    writes = []
    real = store.write
    monkeypatch.setattr(store, "write", lambda fn: writes.append(fn) or real(fn))
    assert await life.clear(cfg, store, at=LATER) == 0 and writes == []


async def test_kept_ones_and_what_something_durable_shows_stay(app, store):
    cfg = app.cfg
    cid = await conversations.create(store)
    ctx, _ = await _call(store, cid, "c1")
    skill, b = await _images(app, ctx, n=4)
    kept, face, sent, plain = [i["attachment"] for i in b["items"]]
    out = await skill.keep(ctx, images=[kept["id"][:8]])
    assert out.content == f"Image {kept['id'][:8]} is kept." and not out.is_error
    await agent.set_portrait(store, cfg, attachments.blob_path(cfg, face["sha256"]).read_bytes())
    draft = await attachments.draft_of(store, sent["id"])
    await store.write(lambda c: c.execute(
        "INSERT INTO intercom(created_at, from_addr, to_addr, kind, text, data) "
        "VALUES(?, 'agent:x', 'user', 'message', 'for you', ?)",
        (iso(T0), json.dumps({"attachments": [draft]}))))
    await _made_at(store, T0)

    assert await life.clear(cfg, store, at=LATER) == 1
    lives = [(await _life(store, a["id"]))[0] for a in (kept, face, sent, plain)]
    assert lives == ["kept", "temporary", "temporary", "cleared"]
    for a in (kept, face, sent):
        assert attachments.blob_path(cfg, a["sha256"]).exists()
    assert not attachments.blob_path(cfg, plain["sha256"]).exists()


async def test_the_same_bytes_held_by_another_attachment_stay_on_disk(app, store):
    cfg = app.cfg
    cid = await conversations.create(store)
    ctx, _ = await _call(store, cid, "c1")
    _, b = await _images(app, ctx)
    att = b["items"][0]["attachment"]
    blob = attachments.blob_path(cfg, att["sha256"])
    same = await upload(cfg, store, blob.read_bytes(), "again.png", "image/png")
    [entry] = (cfg.data_dir / "images").rglob("*.png")
    await _made_at(store, T0)

    assert await life.clear(cfg, store, at=LATER) == 1
    assert (await _life(store, att["id"]))[0] == "cleared"
    assert blob.exists(), "your upload holds the same bytes"
    assert not entry.exists(), "its own entry in the dated folder goes"
    assert (await attachments.get(store, same["id"]))["life"] is None


async def test_a_cleared_one_is_made_again_with_a_new_life_and_an_edit_from_it_refused(
        app, store):
    cfg = app.cfg
    cid = await conversations.create(store)
    ctx, _ = await _call(store, cid, "c1")
    skill, b = await _images(app, ctx)
    old = b["items"][0]["attachment"]
    await _made_at(store, T0)
    assert await life.clear(cfg, store, at=T0 + DAYS) == 1

    other, _ = await _call(store, await conversations.create(store), "c2")
    out = await skill.generate(other, items=[{"prompt": "at night", "source": old["id"][:8]}])
    said = attachments.cleared_line(cfg, "images", old["id"], iso(T0 + DAYS), "")
    assert out.is_error and said in out.content
    assert said == (f"Image {old['id'][:8]} was cleared on {_at(cfg, T0 + DAYS)}: make it "
                    f"again with image_generator__generate from the prompt, preset, size "
                    f"and seed list_images shows.")
    refused = await skill.keep(ctx, images=[old["id"][:8]])
    assert refused.is_error and refused.content == attachments.cleared_line(
        cfg, "images", old["id"], iso(T0 + DAYS), " and cannot be kept")

    async with client(_web(app)) as c:
        r = await c.patch(f"/api/batches/{b['id']}/items/0", json={"kept": True})
        assert r.status_code == 409 and r.json()["error"]["message"] == (
            f"The image was cleared on {_at(cfg, T0 + DAYS)} and cannot be kept: Retry "
            f"makes it again.")
        r = await c.post(f"/api/batches/{b['id']}/items/0/retry")
        assert r.status_code == 200 and r.json()["items"][0]["state"] == "waiting"
        with anyio.fail_after(3):
            await app.batches.wait(b["id"])
        item = (await c.get(f"/api/batches/{b['id']}")).json()["items"][0]
    new = item["attachment"]
    assert item["state"] == "done" and new["id"] != old["id"]
    assert (item["prompt"], item["seed"], item["preset"]) == (
        "picture 0", b["items"][0]["seed"], b["items"][0]["preset"]), "as it was asked"
    assert new["life"]["state"] == "temporary" and new["life"]["at"] > iso(T0 + DAYS)
    assert await attachments.get(store, old["id"]) is None, "the cleared row goes"
    assert attachments.blob_path(cfg, new["sha256"]).exists()
    assert len(list((cfg.data_dir / "images").rglob("*.png"))) == 1


async def test_the_agent_and_the_user_keep_and_let_go_with_the_kept_folder(app, store):
    cfg = app.cfg
    cid = await conversations.create(store)
    ctx, _ = await _call(store, cid, "c1")
    skill, b = await _images(app, ctx, n=2)
    one, two = [i["attachment"] for i in b["items"]]
    short = one["id"][:8]
    folder = cfg.data_dir / "images" / "kept"

    out = await skill.keep(ctx, images=[short, two["id"][:8], "nothing!", "abcdef12"])
    assert out.content.splitlines() == [
        f"Image {short} is kept.", f"Image {two['id'][:8]} is kept.",
        "nothing! is not an image's short id: use the 8 characters from its line, like "
        "f5ef648c.", "There is no image abcdef12 that a batch made."]
    assert os.path.samefile(folder / f"{short}_{one['filename']}",
                            attachments.blob_path(cfg, one["sha256"]))
    assert (await skill.keep(ctx, images=[short])).content == f"Image {short} was kept already."
    read = (await skill.read(ctx, batch=b["id"][:8])).content
    assert read.splitlines()[1].endswith("Kept.")
    assert ", kept" in (await skill.list_images(ctx)).content
    assert row_words("image_generator__keep", {"images": [short, "x"]}, {}, "", "ok") == \
        "Kept 2 images"
    assert row_words("image_generator__keep", {"images": [short], "let_go": True}, {}, "",
                     "ok") == "Let go of 1 image"

    let = T0 + timedelta(days=100)
    out = await life.keep(cfg, store, [one["id"]], False, at=let)
    assert out[0]["changed"] and await _life(store, one["id"]) == ("temporary", iso(let))
    assert not (folder / f"{short}_{one['filename']}").exists()
    out = await skill.keep(ctx, images=[two["id"][:8]], let_go=True)
    assert out.content.startswith(f"Image {two['id'][:8]} is let go. It is cleared on ")
    assert list(folder.iterdir()) == []
    assert await life.clear(cfg, store, at=let + DAYS - timedelta(seconds=1)) == 0, \
        "a let go one's days begin again"

    async with client(_web(app)) as c:
        r = await c.patch(f"/api/batches/{b['id']}/items/1", json={"kept": True})
        assert r.status_code == 200
        assert r.json()["items"][1]["attachment"]["life"]["state"] == "kept"
        assert r.json()["items"][1]["attachment"]["life"]["display"] == \
            {"words": "Kept", "tone": "success"}
        assert (folder / f"{two['id'][:8]}_{two['filename']}").exists()
        r = await c.patch(f"/api/batches/{b['id']}/items/1", json={"kept": False})
        assert r.json()["items"][1]["attachment"]["life"]["state"] == "temporary"
        assert not (folder / f"{two['id'][:8]}_{two['filename']}").exists()
        r = await c.patch(f"/api/batches/{b['id']}/items/5", json={"kept": True})
        assert r.status_code == 409


async def test_a_track_is_kept_and_its_line_says_so(app, store):
    cid = await conversations.create(store, kind="chat")
    ctx, _ = await _call(store, cid, "m1", "music_generator__generate")
    skill = MusicGenerator(app.cfg)
    skill.app = app
    out = await skill.generate(ctx, items=[{"style": "ambient", "title": "Glass"}], wait=True)
    assert not out.is_error, out.content
    [b] = await batches_mod.list_batches(store, "music", conversation=cid)
    track = b["items"][0]["attachment"]
    assert "unless kept." in (await skill.read(ctx, batch=b["id"][:8])).content
    out = await skill.keep(ctx, tracks=[track["id"][:8]])
    assert out.content == f"Track {track['id'][:8]} is kept."
    assert (await skill.read(ctx, batch=b["id"][:8])).content.endswith("Kept.")
    assert (app.cfg.data_dir / "music" / "kept" / f"{track['id'][:8]}_{track['filename']}"
            ).exists()


async def test_the_setting_at_0_clears_nothing(app, store):
    cfg = app.cfg
    cfg.settings.general["generated_keep_days"] = 0
    cid = await conversations.create(store)
    ctx, _ = await _call(store, cid, "c1")
    skill, b = await _images(app, ctx)
    await _made_at(store, T0)
    assert await life.clear(cfg, store, at=LATER) == 0
    assert (await _life(store, b["items"][0]["attachment"]["id"]))[0] == "temporary"
    assert "Cleared" not in (await skill.read(ctx, batch=b["id"][:8])).content
    out = await skill.keep(ctx, images=[b["items"][0]["attachment"]["id"][:8]], let_go=True)
    assert out.content == f"Image {b['items'][0]['attachment']['id'][:8]} is temporary."
