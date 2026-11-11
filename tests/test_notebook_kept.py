"""What the notebook keeps, through its tools: a note's earlier texts, also after it is
deleted, read as plain lines that cannot pass for the note now; the trash with who
deleted what; a restore of only what it deleted itself, back at its path, in the index
and on the page; the routes and the tools list from the same functions."""
from __future__ import annotations

import itertools
import re
from datetime import datetime, timedelta, timezone

import pytest

from celine import boot
from celine.api import notebook as notebook_api
from celine.conversations import store as conversations
from celine.skills import ToolContext, notebook_index
from celine.skills import notebook as notebook_mod
from celine.skills.notebook import trashed, versions
from settings_fixtures import client, make_app, scratch

pytestmark = pytest.mark.anyio

START = datetime(2026, 9, 24, 19, 52, tzinfo=timezone.utc)   # Thu 24 Sep 21:52 in Berlin
WHEN = "Thu 24 Sep 21:52"
ANCHOR = re.compile(r"^\d+:[0-9a-z]{2}\| ", re.M)              # what notebook__edit takes


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """A fixed start, a second later at each look: each kept text and trash folder is
    named apart."""
    ticks = (START + timedelta(seconds=n) for n in itertools.count())
    monkeypatch.setattr(notebook_mod, "_now", lambda: next(ticks))


@pytest.fixture
def notebook(tmp_path, monkeypatch, store):
    """The page's routes and the agent's tools on one notebook: (app, skill, cfg)."""
    scratch(tmp_path, monkeypatch)
    cfg = boot.load()
    cfg.timezone = "Europe/Berlin"
    app = make_app(cfg, store)
    app.include_router(notebook_api.router)
    return app, app.state.services.registry.skills["notebook"], cfg


def _ctx(cid, store, cfg):
    return ToolContext(cid, "t", "x", store, cfg, None)  # type: ignore[arg-type]


async def test_a_notes_earlier_texts_list_newest_first_and_stay_after_a_delete(notebook, store):
    app, nb, cfg = notebook
    ctx, root = _ctx("c", store, cfg), cfg.agent.notebook.resolve()
    for text in ("# sea\nwaves", "# sea\nwaves\ngulls", "# sea\ngulls"):
        await nb.write(ctx, "lore/sea.md", text, "the sea")
    rows = (await nb.list_versions(ctx, "lore/sea.md")).content.splitlines()
    assert rows[0] == ('The earlier texts of lore/sea.md, newest first. notebook__read_'
                       'version(path="lore/sea.md", version=<n>) reads one:')
    second = "Last changed: 2026-09-24 21:52\n# sea\nwaves\ngulls"
    assert rows[1] == f'version 2, from {WHEN}, {len(second)} characters: "gulls"'
    assert rows[2].startswith("version 1, ") and rows[2].endswith(': "sea"')
    assert rows[3].startswith("Its text now is version 3, from ") and len(rows) == 4

    old = await nb.read_version(ctx, "lore/sea.md", 2)
    head, _, body = old.content.partition("\n\n")
    assert head == (f"[version 2 of lore/sea.md, the note's text from {WHEN}: an earlier "
                    f"text, not the note now]")
    assert body == second
    assert not ANCHOR.search(old.content), "no anchor notebook__edit would take"
    assert old.display == {"path": "lore/sea.md", "version": 2, "class": "unclassed"}
    for v, said in ((3, "Version 3 is the text lore/sea.md has now: notebook__read shows it."),
                    (9, 'lore/sea.md has no version 9: notebook__list_versions(path="lore/'
                        'sea.md") lists its versions.')):
        out = await nb.read_version(ctx, "lore/sea.md", v)
        assert out.is_error and out.content == said

    # deleted, its earlier texts stay; its last text is in the trash
    out = await nb.delete(ctx, "lore/sea.md")
    [entry] = trashed(root)
    tid = entry["id"]
    assert out.content == (f'Moved lore/sea.md to the trash as {tid}: notebook__restore('
                           f'id="{tid}") brings it back.')
    rows = (await nb.list_versions(ctx, "lore/sea.md")).content.splitlines()
    assert [r.split(",")[0] for r in rows[1:3]] == ["version 2", "version 1"]
    assert rows[3] == (f'lore/sea.md itself is in the trash: notebook__read_deleted(id='
                       f'"{tid}") reads the text it had when it was deleted.')
    assert not (await nb.read_version(ctx, "lore/sea.md", 2)).is_error
    assert (await nb.list_versions(ctx, "lore/none.md")).content == \
        "The notebook keeps no earlier text of lore/none.md."

    # the History page lists them from the same function
    async with client(app) as c:
        page = (await c.get("/api/notebook/versions", params={"path": "lore/sea.md"})).json()
    assert page == versions(root, "lore/sea.md")
    assert page["current"] is None and [v["id"] for v in page["versions"]] == [2, 1], \
        "a deleted note has no text now: its newest kept one is no current text"


async def test_it_restores_what_it_deleted_and_the_users_stays_in_the_trash(notebook, store):
    app, nb, cfg = notebook
    ctx, root, user = _ctx("c", store, cfg), cfg.agent.notebook.resolve(), cfg.user
    await nb.write(ctx, "people/noor.md", "Noor climbs.", "noor")
    await nb.write(ctx, "people/sam.md", "Sam sails.", "sam")
    await nb.write(ctx, "plans/trip.md", "Lyon in May.", "the trip")
    await nb.write(ctx, "lore/a.md", "a", "a")
    await nb.write(ctx, "lore/b.md", "b", "b")
    async with client(app) as c:
        await nb.delete(ctx, "people/noor.md")
        assert (await c.delete("/api/notebook/entries/people/sam.md")).status_code == 200
        await nb.delete(ctx, "plans/trip.md")
        await nb.mark(store, "plans/trip.md", True)         # the user's path now
        assert (await c.delete("/api/notebook/entries/lore")).status_code == 200

        # the page and the tool list the trash from the same function
        listed = (await c.get("/api/notebook/trash")).json()["trash"]
        assert listed == trashed(root)
        assert [(t["path"], t["kind"], t["by"]) for t in listed] == [
            ("lore", "dir", "user"), ("plans/trip.md", "file", "agent"),
            ("people/sam.md", "file", "user"), ("people/noor.md", "file", "agent")]
        lore, trip, sam, noor = (t["id"] for t in listed)
        assert (await nb.list_deleted(ctx, None)).content.splitlines() == [
            f"{lore}: lore (a folder), deleted by {user} {WHEN}",
            f"{trip}: plans/trip.md, deleted by you {WHEN}",
            f"{sam}: people/sam.md, deleted by {user} {WHEN}",
            f"{noor}: people/noor.md, deleted by you {WHEN}"]
        assert (await nb.list_deleted(ctx, "people")).display == {"count": 2}

        # read, and left in the trash
        out = await nb.read_deleted(ctx, noor)
        assert out.content == (f"[people/noor.md as it was when you deleted it, {WHEN}: a "
                               f"text in the trash, not a note in your notebook now]\n\n"
                               f"Last changed: 2026-09-24 21:52\nNoor climbs.")
        assert not ANCHOR.search(out.content) and noor in [t["id"] for t in trashed(root)]
        # what the user deleted is listed above, and its text is the user's
        for out, said in (
                (await nb.read_deleted(ctx, sam),
                 f"{user} deleted people/sam.md, so its text is {user}'s and stays in the "
                 f"trash."),
                (await nb.read_deleted(ctx, lore),
                 f"{user} deleted lore, so the notes in it are {user}'s and stay in the trash.")):
            assert out.is_error and out.content == said

        # the user's stay
        for tid, said in ((sam, f"{user} deleted people/sam.md"),
                          (lore, f"{user} deleted lore"),
                          (trip, f"plans/trip.md is {user}'s")):
            out = await nb.restore(ctx, tid)
            assert out.is_error and out.content == \
                f"{said}, so it stays in the trash for {user} to bring back."
        for call in (nb.restore, nb.read_deleted):
            out = await call(ctx, "2020-01-01_000000_000000")
            assert out.is_error and out.content == ("The trash has no 2020-01-01_000000_000000"
                                                    ": notebook__list_deleted shows what it "
                                                    "holds.")

        # a note at its path now: nothing changes
        await nb.write(ctx, "people/noor.md", "Noor again.", "noor anew")
        out = await nb.restore(ctx, noor)
        assert out.is_error and out.content == (
            f'A note is at people/noor.md now, so nothing changed: notebook__read_deleted('
            f'id="{noor}") shows the deleted text, and notebook__write saves it under '
            f'another path.')
        assert (root / "people/noor.md").read_text().endswith("Noor again.")
        assert noor in [t["id"] for t in trashed(root)]

        # moved away, it comes back: the file, the index, the page's tree and trash
        await nb.move(ctx, "people/noor.md", "people/noor-2.md")
        out = await nb.restore(ctx, noor)
        assert not out.is_error and out.content == "Restored people/noor.md from the trash."
        assert out.display == {"path": "people/noor.md"}
        assert (root / "people/noor.md").read_text().endswith("Noor climbs.")
        assert not (root / ".trash" / noor).exists()
        assert "people/noor.md" in [r["path"] for r in await notebook_index.list(store)]
        assert "people/noor.md" in (await nb.search(ctx, "climbs")).content
        tree = (await c.get("/api/notebook/tree")).json()["tree"]
        people = next(n for n in tree if n["path"] == "people")
        assert "people/noor.md" in [n["path"] for n in people["children"]]
        assert noor not in [t["id"] for t in (await c.get("/api/notebook/trash")).json()["trash"]]
        # the user brings back their own from the page
        assert (await c.post(f"/api/notebook/trash/{sam}/restore")).json() == \
            {"ok": True, "path": "people/sam.md"}


async def test_the_texts_from_before_the_user_deleted_a_note_are_the_users(notebook, store):
    """A user's delete of a note, or of a folder that held it, draws a line: its tools
    leave out the texts kept before it, also once a new note is at the path; the
    History page keeps every one."""
    app, nb, cfg = notebook
    ctx, user = _ctx("c", store, cfg), cfg.user
    for text in ("# sea\nwaves", "# sea\ngulls"):
        await nb.write(ctx, "lore/sea.md", text, "the sea")
    for text in ("# reef\nfish", "# reef\ncoral"):
        await nb.write(ctx, "lore/reef.md", text, "the reef")
    line = f"{user} deleted lore/sea.md on {WHEN}, so its texts from before then are {user}'s."
    async with client(app) as c:
        assert (await c.delete("/api/notebook/entries/lore/sea.md")).status_code == 200
        assert (await nb.list_versions(ctx, "lore/sea.md")).content == line, \
            "every text it had is before the line, and its last one is in the trash"
        out = await nb.read_version(ctx, "lore/sea.md", 1)
        assert out.is_error and out.content.startswith("lore/sea.md has no version 1: ")

        # a new note at the path: only its own texts, under ids that never change
        for text in ("storm", "storm\nrain"):
            await nb.write(ctx, "lore/sea.md", text, "the sea again")
        rows = (await nb.list_versions(ctx, "lore/sea.md")).content.splitlines()
        first = "Last changed: 2026-09-24 21:52\nstorm"
        assert rows[1:] == [f'version 2, from {WHEN}, {len(first)} characters: "storm"', line,
                            f"Its text now is version 3, from {WHEN}: notebook__read shows it."]
        assert (await nb.read_version(ctx, "lore/sea.md", 2)).content.endswith(f"\n\n{first}")
        assert (await nb.read_version(ctx, "lore/sea.md", 1)).is_error
        page = (await c.get("/api/notebook/versions", params={"path": "lore/sea.md"})).json()
        assert [v["id"] for v in page["versions"]] == [2, 1] and page["current"]["id"] == 3, \
            "the user's History keeps every text"
        old = (await c.get("/api/notebook/versions/1", params={"path": "lore/sea.md"})).json()
        assert old["text"].endswith("# sea\nwaves")

        # the user deletes the folder: it covers each note that was in it
        assert (await c.delete("/api/notebook/entries/lore")).status_code == 200
        for rel in ("lore/sea.md", "lore/reef.md"):
            assert (await nb.list_versions(ctx, rel)).content == (
                f"{user} deleted {rel} on {WHEN}, so its texts from before then are {user}'s.")
            assert (await nb.read_version(ctx, rel, 1)).is_error
        assert [v["id"] for v in (await c.get("/api/notebook/versions", params={
            "path": "lore/reef.md"})).json()["versions"]] == [1]


async def test_the_users_restore_gives_the_earlier_texts_back(notebook, store):
    """The user's Restore lifts the line from each note it brings back, a note or every
    note of a folder; the user's next delete draws it again; its own restore keeps it."""
    app, nb, cfg = notebook
    ctx, root, user = _ctx("c", store, cfg), cfg.agent.notebook.resolve(), cfg.user
    for text in ("# sea\nwaves", "# sea\ngulls"):
        await nb.write(ctx, "lore/sea.md", text, "the sea")
    for text in ("# reef\nfish", "# reef\ncoral"):
        await nb.write(ctx, "lore/reef.md", text, "the reef")
    async with client(app) as c:
        await c.delete("/api/notebook/entries/lore/sea.md")
        assert (await nb.read_version(ctx, "lore/sea.md", 1)).is_error
        [sea] = (await c.get("/api/notebook/trash")).json()["trash"]
        assert (await c.post(f"/api/notebook/trash/{sea['id']}/restore")).status_code == 200
        assert (await nb.list_versions(ctx, "lore/sea.md")).content.splitlines()[1:] == [
            f'version 1, from {WHEN}, 42 characters: "sea"',
            f"Its text now is version 2, from {WHEN}: notebook__read shows it."]
        assert (await nb.read_version(ctx, "lore/sea.md", 1)).content.endswith("# sea\nwaves")

        await c.delete("/api/notebook/entries/lore")
        assert (await nb.read_version(ctx, "lore/reef.md", 1)).is_error
        [lore] = (await c.get("/api/notebook/trash")).json()["trash"]
        assert (await c.post(f"/api/notebook/trash/{lore['id']}/restore")).status_code == 200
        for rel, first in (("lore/sea.md", "# sea\nwaves"), ("lore/reef.md", "# reef\nfish")):
            assert (await nb.read_version(ctx, rel, 1)).content.endswith(first), rel

        # deleted by the user again: the line is back, and its own restore keeps it
        line = (f"{user} deleted lore/reef.md on {WHEN}, so its texts from before then are "
                f"{user}'s.")
        await c.delete("/api/notebook/entries/lore/reef.md")
        assert (await nb.list_versions(ctx, "lore/reef.md")).content == line
        assert (await nb.read_version(ctx, "lore/reef.md", 1)).is_error
        await nb.write(ctx, "lore/reef.md", "kelp", "the reef again")
        await nb.delete(ctx, "lore/reef.md")
        assert not (await nb.restore(ctx, trashed(root)[0]["id"])).is_error
        assert (await nb.list_versions(ctx, "lore/reef.md")).content.splitlines() == [
            line, f"Its text now is version 2, from {WHEN}: notebook__read shows it."]
        assert (await nb.read_version(ctx, "lore/reef.md", 1)).is_error


async def test_a_restore_is_named_by_the_path_it_went_back_to():
    """The call names a trash id; its row, its Events line and its link name the path its
    display says."""
    from celine.events import _note
    from celine.skills.tool_rows import words
    args, shown = '{"id": "2026-11-10_140210_123456"}', '{"path": "people/noor.md"}'
    got = _note({"name": "notebook__restore", "state": "ok", "arguments": args,
                 "display": shown}, None)
    assert (got["title"], got["open"]) == ("Restored people/noor.md",
                                           "/notebook?note=people/noor.md")
    row = words("notebook__restore", args, "ok", {"path": "people/noor.md"})
    assert row["settled"] == "Restored the note people/noor.md from Trash"
    assert words("notebook__restore", args, "running")["running"] == \
        "Restoring a note from Trash"


async def test_a_sub_agent_sees_and_restores_only_its_folder(notebook, store):
    app, nb, cfg = notebook
    whole = _ctx("c", store, cfg)
    await nb.write(whole, "plans/secret.md", "the big plan", "the plan")
    await nb.delete(whole, "plans/secret.md")
    parent = await conversations.create(store)
    sub = await conversations.create(store, agent_of=parent, agent_name="scout",
                                     notebook_folder="agents/scout")
    ctx = _ctx(sub, store, cfg)
    await nb.write(ctx, "agents/scout/found.md", "found it", "finds")
    await nb.delete(ctx, "agents/scout/found.md")
    [mine, secret] = (t["id"] for t in trashed(cfg.agent.notebook.resolve()))
    assert (await nb.list_deleted(ctx, None)).content == \
        f"{mine}: agents/scout/found.md, deleted by you {WHEN}"
    for out in (await nb.restore(ctx, secret), await nb.read_deleted(ctx, secret),
                await nb.list_versions(ctx, "plans/secret.md"),
                await nb.list_deleted(ctx, "plans")):
        assert out.is_error and out.content.endswith("is outside your folder agents/scout."), \
            out.content
    assert (await nb.restore(ctx, mine)).content == \
        "Restored agents/scout/found.md from the trash."
