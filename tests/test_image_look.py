"""image_generator__look: the agent sees the pictures it made, the ones the user attached
to this conversation and its portrait, as many as it names, with no copy of any: the
call's display names them and the context sends them after its result (turns/context.py
calls_wire). A model that cannot see gets a description of each. A cleared or unknown
image says why, a file gone since goes as a missing image's line, an aged look is
stubbed."""
from __future__ import annotations

from chat_fixtures import PNG, upload, user_says
from test_comfyui import _call
from test_made_life import DAYS, T0, _images, _made_at, app  # noqa: F401  (the batch runner on a fake ComfyUI, each image its own bytes)

from types import SimpleNamespace

import pytest

from celine import attachments, vision
from celine.comfyui import life
from celine.conversations import store as conversations
from celine.core.ids import iso
from celine.guard import facts as guard_facts, sources
from celine.skills import ToolResult
from celine.skills.images import ImageGenerator
from celine.skills.tool_rows import row as row_words
from celine.turns import compact, compactions, context
from celine.turns import store as turns
from fake_backend import FakeBackend

pytestmark = pytest.mark.anyio

LOOK = "image_generator__look"


def _sees(yes: bool):
    async def sees(cfg, ctx) -> bool:
        return yes
    return sees


async def _look(app, ctx, mid, **kw) -> ToolResult:
    """The look of call `ctx` in reply `mid`, its result stored as a turn stores it."""
    skill = ImageGenerator(app.cfg)
    skill.app = app
    out = await skill.look(ctx, **kw)
    await turns.finish_tool_call(app.store, ctx.conversation_id, mid, ctx.tool_call_id, LOOK,
                                 out, "error" if out.is_error else "ok")
    return out


async def _rows(store) -> tuple[int, int]:
    """(attachment rows, blob rows)."""
    return await store.read(lambda c: c.execute(
        "SELECT (SELECT count(*) FROM attachments), (SELECT count(*) FROM blobs)").fetchone())


async def _made(app, store, n: int) -> list[dict]:
    """`n` images the agent made in a conversation of their own, in order."""
    ctx, _ = await _call(store, await conversations.create(store), "made")
    _, b = await _images(app, ctx, n=n)
    return [i["attachment"] for i in b["items"]]


@pytest.mark.parametrize("n", [1, 12])
async def test_a_model_that_sees_gets_the_pictures_after_the_result_in_order(
        app, store, monkeypatch, n):
    monkeypatch.setattr(attachments, "turn_sees_images", _sees(True))
    made = await _made(app, store, n)
    ids = [a["id"] for a in made]
    before = await _rows(store)
    cid = await conversations.create(store)
    ctx, mid = await _call(store, cid, "c1", LOOK)
    out = await _look(app, ctx, mid, images=[i[:8] for i in ids])
    assert not out.is_error and out.display["count"] == n
    assert [p["id"] for p in out.display["see"]] == ids
    lines = out.content.splitlines()
    assert len(lines) == n and lines[0].startswith(f"1. image {ids[0][:8]}: ")
    assert lines[-1].startswith(f"{n}. image {ids[-1][:8]}: ")
    assert all(", 832x1216, seed " in ln and ln.endswith("unless kept.") for ln in lines)
    assert await _rows(store) == before, "a look stores no copy of what it shows"

    request = await context.step_wire(store, app.cfg, cid, mid)
    assert [m["role"] for m in request] == ["assistant", "tool", "user"]
    assert request[1]["content"] == out.content + (
        "\n[the image follows]" if n == 1 else "\n[the images follow]")
    shown = request[2]["content"]
    assert shown[0] == {"type": "text", "text": context.pictures_from(LOOK, n)}
    assert [p["type"] for p in shown[1:]] == [attachments.IMAGE] * n
    assert [p["attachment"]["id"] for p in shown[1:]] == ids, "in the order of the lines"
    assert [p["attachment"]["sha256"] for p in shown[1:]] == [a["sha256"] for a in made]


async def test_a_model_that_does_not_see_gets_a_description_of_each(app, store, monkeypatch):
    monkeypatch.setattr(attachments, "turn_sees_images", _sees(False))
    asked = []

    async def described(llm, data, cid, tid, question=None):
        asked.append((data, question))
        return f"Description by eyes: picture {len(asked)}."
    monkeypatch.setattr(vision, "describe", described)
    made = await _made(app, store, 2)
    cid = await conversations.create(store)
    ctx, mid = await _call(store, cid, "c1", LOOK)
    out = await _look(app, ctx, mid, images=[a["id"][:8] for a in made],
                      question="Which one has a hat?")
    assert out.display == {"count": 2}, "nothing to send it"
    lines = out.content.splitlines()
    assert lines[0].startswith(f"1. image {made[0]['id'][:8]}: ")
    assert lines[1] == "Description by eyes: picture 1."
    assert lines[3] == "Description by eyes: picture 2."
    assert asked == [(attachments.blob_path(app.cfg, a["sha256"]).read_bytes(),
                      "Which one has a hat?") for a in made]
    request = await context.step_wire(store, app.cfg, cid, mid)
    assert [m["role"] for m in request] == ["assistant", "tool"]
    assert request[1]["content"] == out.content


async def test_a_batch_is_every_image_it_made_and_a_repeat_collapses(app, store, monkeypatch):
    monkeypatch.setattr(attachments, "turn_sees_images", _sees(True))
    ctx, _ = await _call(store, await conversations.create(store), "made")
    _, b = await _images(app, ctx, n=3)
    ids = [i["attachment"]["id"] for i in b["items"]]
    cid = await conversations.create(store)
    look, mid = await _call(store, cid, "c1", LOOK)
    out = await _look(app, look, mid, batch=b["id"][:8], images=[ids[1][:8], ids[1]])
    assert out.content.splitlines()[0] == f"Batch {b['id'][:8]} is done: 3 of 3 made."
    assert [p["id"] for p in out.display["see"]] == ids, "the batch's, in order, once each"
    assert out.display["count"] == 3

    out = await ImageGenerator(app.cfg).look(look, batch="feedbeef")
    assert out.is_error and out.content == "There is no batch feedbeef."
    for nothing in ({}, {"images": None, "batch": None}):       # a null is its default
        out = await ImageGenerator(app.cfg).look(look, **nothing)
        assert out.is_error and \
            out.content == "Give images, by their short ids, or a batch id."


async def test_images_it_made_anywhere_and_yours_here_but_never_yours_elsewhere(
        app, store, monkeypatch):
    monkeypatch.setattr(attachments, "turn_sees_images", _sees(True))
    [made] = await _made(app, store, 1)
    here, other = await conversations.create(store), await conversations.create(store)
    mine = await upload(app.cfg, store, PNG, "mine.png", "image/png")
    await user_says(store, here, "this one", attachment_ids=[mine["id"]])
    away = await upload(app.cfg, store, PNG + b"elsewhere", "away.png", "image/png")
    await user_says(store, other, "not for you", attachment_ids=[away["id"]])
    before = await _rows(store)
    ctx, mid = await _call(store, here, "c1", LOOK)
    out = await _look(app, ctx, mid, images=[made["id"][:8], mine["id"][:8],
                                             away["id"][:8]])
    lines = out.content.splitlines()
    assert lines[0].startswith(f"1. image {made['id'][:8]}: ")
    assert lines[1] == f"2. image {mine['id'][:8]}: {app.cfg.user} attached mine.png, 1x1."
    assert lines[2] == (f"There is no image {away['id'][:8]} that you may use: only an image "
                        f"you made, or one {app.cfg.user} attached to this conversation.")
    assert [p["id"] for p in out.display["see"]] == [made["id"], mine["id"]]
    assert not out.is_error and await _rows(store) == before


async def test_the_users_image_is_named_by_its_line_its_file_name_or_its_box_path(
        app, store, monkeypatch):
    """The user's attachment showed only its box path, and no tool took that or its file
    name, so it could never be an edit's source (#55)."""
    monkeypatch.setattr(attachments, "turn_sees_images", _sees(True))
    here = await conversations.create(store)
    att = await upload(app.cfg, store, PNG, "8t6onhjeacpf1.jpeg", "image/png")
    await user_says(store, here, "for the second image", attachment_ids=[att["id"]])
    box = "~/uploads/e082b5bc9be8/8t6onhjeacpf1.jpeg"
    await attachments.set_box_path(store, att["id"], box)
    short = att["id"][:8]
    # its line says its short id, beside where it is in the box
    mid_user = await store.read(lambda c: c.execute(
        "SELECT message_id FROM attachments WHERE id=?", (att["id"],)).fetchone()[0])
    rows = await store.read(lambda c: attachments.of_message(c, mid_user))
    [said] = attachments.render(app.cfg, [{"role": "user", "content": "for the second image",
                                           "attachments": rows}])
    text = said["content"][0]["text"]
    assert f"[image 8t6onhjeacpf1.jpeg, short id {short}, in your box at " in text
    ctx, mid = await _call(store, here, "c1", LOOK)
    for ref in ("8t6onhjeacpf1.jpeg", box, "/home/celine/uploads/e082b5bc9be8/8t6onhjeacpf1.jpeg"):
        out = await _look(app, ctx, mid, images=[ref])
        assert not out.is_error and [p["id"] for p in out.display["see"]] == [att["id"]], ref
    listed = (await ImageGenerator(app.cfg).list_images(ctx, scope="all")).content
    assert f"{short}  " in listed and "attached 8t6onhjeacpf1.jpeg" in listed


async def test_a_cleared_image_an_unknown_id_and_a_good_one_in_one_look(app, store, monkeypatch):
    monkeypatch.setattr(attachments, "turn_sees_images", _sees(True))
    gone, kept = await _made(app, store, 2)
    await ImageGenerator(app.cfg).keep(
        SimpleNamespace(store=store, conversation_id="x"), images=[kept["id"][:8]])
    await _made_at(store, T0)
    assert await life.clear(app.cfg, store, at=T0 + DAYS) == 1
    cid = await conversations.create(store)
    ctx, mid = await _call(store, cid, "c1", LOOK)
    out = await _look(app, ctx, mid, images=[gone["id"][:8], "abcdef12", "nothing!",
                                             kept["id"][:8]])
    lines = out.content.splitlines()
    assert lines[:3] == [
        attachments.cleared_line(app.cfg, "images", gone["id"], iso(T0 + DAYS), ""),
        "There is no image abcdef12 that you may use: only an image you made, or one "
        f"{app.cfg.user} attached to this conversation.",
        "nothing! is not an image's short id: use the 8 characters from its line, its "
        "result or list_images, like f5ef648c, or for one "
        f"{app.cfg.user} attached to this conversation its file name."]
    assert len(lines) == 4 and lines[3].startswith(f"1. image {kept['id'][:8]}: ")
    assert lines[3].endswith(" Kept.")
    assert not out.is_error and [p["id"] for p in out.display["see"]] == [kept["id"]]

    nothing = await ImageGenerator(app.cfg).look(ctx, images=[gone["id"][:8], "abcdef12"])
    assert nothing.is_error and nothing.display is None, "nothing could be shown"


async def test_a_picture_cleared_after_the_look_goes_as_a_missing_image(app, store,
                                                                         monkeypatch):
    """The look named it; by the next build its file is gone. The request says so where
    the picture was, as for a message's missing image, and nothing raises."""
    monkeypatch.setattr(attachments, "turn_sees_images", _sees(True))
    gone, stays = await _made(app, store, 2)
    await ImageGenerator(app.cfg).keep(
        SimpleNamespace(store=store, conversation_id="x"), images=[stays["id"][:8]])
    cid = await conversations.create(store)
    ctx, mid = await _call(store, cid, "c1", LOOK)
    await _look(app, ctx, mid, images=[gone["id"][:8], stays["id"][:8]])
    await _made_at(store, T0)
    assert await life.clear(app.cfg, store, at=T0 + DAYS) == 1
    assert not attachments.blob_path(app.cfg, gone["sha256"]).exists()

    request = await context.step_wire(store, app.cfg, cid, mid)
    assert attachments.gone(gone["filename"]) in request[1]["content"]
    assert request[1]["content"].endswith("\n[the image follows]")
    assert [p["attachment"]["id"] for p in request[2]["content"][1:]] == [stays["id"]]
    built = await context.build(store, app.cfg, FakeBackend(), cid, system="S", tools=[],
                                mode="dry")
    assert attachments.gone(gone["filename"]) in next(
        m["content"] for m in built.messages if m["role"] == "tool")

    # gone between the build and the request: the image's part is that line, for a model
    # that sees and for one that is given a description
    part = attachments.image_part(gone, attachments.blob_path(app.cfg, gone["sha256"]))
    images = attachments.Images(app.cfg, store, None, "x")
    msg = {"role": "user", "content": [{"type": "text", "text": "[the image from x]"}, part]}
    eyes = await images.wire([msg], SimpleNamespace(supports_images=True), "Eyes")
    assert eyes[0]["content"][1] == {"type": "text", "text": attachments.gone(gone["filename"])}
    blind = await images.wire([msg], SimpleNamespace(supports_images=False), "Text")
    assert blind[0]["content"] == f"[the image from x]\n\n{attachments.gone(gone['filename'])}"


async def test_an_aged_look_goes_as_its_stub_without_its_pictures(app, store, monkeypatch):
    monkeypatch.setattr(attachments, "turn_sees_images", _sees(True))
    made = await _made(app, store, 2)
    cid = await conversations.create(store)
    ctx, mid = await _call(store, cid, "c1", LOOK)
    await _look(app, ctx, mid, images=[a["id"][:8] for a in made])
    await compactions.set_aged_through(store, cid, mid, {"results": 1})
    built = await context.build(store, app.cfg, FakeBackend(), cid, system="S", tools=[],
                                mode="dry")
    tool = next(m for m in built.messages if m["role"] == "tool")
    assert tool["content"].startswith(f"{compact.STUB_HEAD} {LOOK}(")
    assert not [p for m in built.messages if isinstance(m["content"], list)
                for p in m["content"] if p.get("type") == attachments.IMAGE]


async def test_its_portrait_by_the_ref_an_edit_takes(app, store, monkeypatch):
    monkeypatch.setattr(attachments, "turn_sees_images", _sees(True))
    [face] = await _made(app, store, 1)
    app.cfg.agent.portrait = face["sha256"]
    cid = await conversations.create(store)
    ctx, mid = await _call(store, cid, "c1", LOOK)
    out = await _look(app, ctx, mid, images=["portrait"])
    assert out.content == "1. your portrait."
    assert out.display["see"] == [{"id": "portrait", "filename": "portrait.png",
                                   "sha256": face["sha256"], "media_type": "image/png"}]


async def test_a_portrait_it_sets_is_its_portrait_at_once_for_every_reader(app, store,
                                                                           monkeypatch):
    """appearance__set_portrait reads the portrait back: the page's portrait route and a
    look's `portrait` see it on the next call, with no restart (#49: the row was written
    and cfg.agent kept the old one, so the page showed none and a look said there was
    none, while the call said it was set)."""
    from celine.skills.appearance import Appearance
    from celine import agent
    monkeypatch.setattr(attachments, "turn_sees_images", _sees(True))
    [face] = await _made(app, store, 1)
    app.cfg.agent.portrait = None
    store.emit_global = lambda ev: None
    cid = await conversations.create(store)
    ctx, mid = await _call(store, cid, "c1", "appearance__set_portrait")
    out = await Appearance(app.cfg).set_portrait(ctx, image=face["id"][:8])
    assert not out.is_error
    assert out.content == f"Your portrait is now image {face['id'][:8]}: the app read it back."
    assert out.display["portrait"]["url"].endswith(face["sha256"][:12])
    assert agent.portrait_file(app.cfg)[0] == attachments.blob_path(app.cfg, face["sha256"])

    look, mid = await _call(store, cid, "c2", LOOK)
    out = await _look(app, look, mid, images=["portrait", f"portrait:{app.cfg.name}"])
    assert out.content == "1. your portrait." and len(out.display["see"]) == 1
    assert out.display["see"][0]["sha256"] == face["sha256"]

    out = await ImageGenerator(app.cfg).look(look, images=["portrait:Alex"])
    assert out.is_error and out.content == \
        f"There is no portrait of Alex: the one portrait is yours, portrait:{app.cfg.name}."
    await agent.set_portrait(store, app.cfg, None)
    out = await ImageGenerator(app.cfg).look(look, images=["portrait"])
    assert out.content == f"{app.cfg.name} has no portrait. appearance__set_portrait sets it."


async def test_it_lists_what_it_kept_or_made_in_any_conversation_its_portrait_marked(
        app, store):
    """A self-review in a conversation with no images could not list the render it kept
    elsewhere, nor see that it was its portrait (#50)."""
    from celine import agent
    kept, other = await _made(app, store, 2)
    await life.keep(app.cfg, store, [kept["id"]], True, at=T0)
    store.emit_global = lambda ev: None
    await agent.set_portrait(store, app.cfg, attachments.blob_path(
        app.cfg, kept["sha256"]).read_bytes())
    cid = await conversations.create(store)
    ctx, _ = await _call(store, cid, "c1", "image_generator__list_images")
    skill = ImageGenerator(app.cfg)

    out = await skill.list_images(ctx)
    assert out.content == "There are no images in this conversation."
    out = await skill.list_images(ctx, scope="kept")
    [head, prompt] = out.content.splitlines()
    assert head.startswith(f"{kept['id'][:8]}  ")
    assert head.endswith(", kept, your portrait, in another conversation")
    assert prompt == "  prompt: picture 0"
    out = await skill.list_images(ctx, scope="all")
    assert sorted(b.split()[0] for b in out.content.split("\n\n")) == \
        sorted([other["id"][:8], kept["id"][:8]])

    await life.keep(app.cfg, store, [kept["id"]], False, at=T0)
    out = await skill.list_images(ctx, scope="kept")
    assert out.content == "You have kept no images."
    assert row_words("image_generator__list_images", {"scope": "kept"}, {}, "", "ok") == \
        "Listed the kept images"
    assert row_words("image_generator__list_images", {}, {}, "", "ok") == \
        "Listed the images in this chat"


def test_its_row_says_how_many_it_looked_at_and_the_guard_reads_it_as_its_own():
    assert row_words(LOOK, {"images": ["a", "b"]}, {"count": 4}, "", "ok") == \
        "Looked at 4 images"
    assert row_words(LOOK, {"images": ["a", "b"]}, {}, "", "running") == "Looking at 2 images"
    assert row_words(LOOK, {"batch": "3f2a9c1e"}, {}, "", "error") == \
        "Could not look at the images of batch 3f2a9c1e"
    # its own pictures, the user's attachments and its portrait: no outside text
    assert sources.never(LOOK) and not sources.from_outside(LOOK)
    assert sources.label(LOOK, {"images": ["a"]}, ToolResult(content="1. image a")) is None
    assert guard_facts.result_class(LOOK, {}, None) is None
