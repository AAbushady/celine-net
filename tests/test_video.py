"""The video skill against a fake watch helper: a watch starts and comes back as its first stretch, a stretch reads
in order with where the next begins, the stills way brings its sheets as images, captions come under their kind, a
listen reads with what was measured, one of her tracks is sent to the helper by its sha256, the helper's words come
through, Plex links and searches, the probe, and the app's key for the helper."""
from __future__ import annotations

import json

import httpx
import pytest

from celine import attachments, keys
from celine.boot import load
from celine.settings import services as services_mod
from celine.skills import ToolContext
from celine.skills import video as video_mod
from celine.skills.video import Video, seconds

pytestmark = pytest.mark.anyio


class Helper:
    """A watch helper in memory: jobs that finish after `steps` looks, a log, a sheet, Plex."""

    def __init__(self, mode="omni", steps=2):
        self.mode, self.steps, self.looks = mode, steps, 0
        self.posted: list[dict] = []
        self.seen: list[str] = []
        self.plex = {"linked": False, "user": None, "servers": []}
        self.stopped_at: float | None = None      # where a watch that stopped early ends, None: it is done
        self.files: list[dict] = []                # what was sent to PUT /files/<sha256>
        self.pictures: list[str] = []              # what a listen's first stretch names (listen.py PICTURES)
        self.live_to: float | None = None          # a watch still going: where its log ends now

    def job(self, state="queued"):
        return {"id": "abc123def456", "kind": "listen" if self.mode == "listen" else "watch",
                "source": {"kind": "youtube", "url": "https://www.youtube.com/watch?v=x"},
                "title": "A short film (Blender)", "duration": 600, "start": 0, "length": 0, "state": state,
                "stage": "watching" if state == "watching" else None, "mode": self.mode, "why": None,
                "done_to": 40 if state == "watching" else 600, "stretch": [0, 600], "error": None}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.seen.append(f"{method} {path}?{request.url.query.decode()}")
        assert request.headers["authorization"].startswith("Bearer ")
        if (method, path) == ("POST", "/jobs"):
            body = json.loads(request.content)
            self.posted.append(body)
            if "youtu" not in body["source"] and not body["source"].startswith(("plex:", "file:")):
                return httpx.Response(400, json={"error": "source is a YouTube link or a plex:... id."})
            return httpx.Response(201, json=self.job())
        if method == "PUT" and path.startswith("/files/"):
            data = request.read()
            self.files.append({"sha": path.rsplit("/", 1)[1], "ext": request.url.params.get("ext"),
                               "length": request.headers.get("content-length"), "data": data})
            return httpx.Response(201, json={"sha256": path.rsplit("/", 1)[1], "bytes": len(data)})
        if (method, path) == ("POST", "/jobs/abc123def456/resume"):
            if self.stopped_at is None:
                return httpx.Response(400, json={"error": "That watch is done. A new watch with start watches "
                                                           "more of the video."})
            self.looks = 0
            return httpx.Response(200, json={**self.job("queued"), "done_to": self.stopped_at})
        if (method, path) == ("GET", "/jobs/abc123def456"):
            self.looks += 1
            return httpx.Response(200, json=self.job("done" if self.looks >= self.steps else "watching"))
        if (method, path) == ("GET", "/jobs/abc123def456/log"):
            at = float(request.url.params.get("at", 0))
            out = {"id": "abc123def456", "title": "A short film (Blender)", "mode": self.mode, "state": "done",
                   "from": at, "to": at + 180, "start": 0, "end": 600, "done_to": 600, "next": at + 180}
            if self.live_to is not None:
                out.update(state="watching", done_to=self.live_to)
            if self.mode == "omni":
                out["lines"] = [f"[{int(at) // 60}:00] A bird lands on a branch; soft strings play."] \
                    if self.live_to is None or at < self.live_to else []
            elif self.mode == "listen":
                out.update(end=278, next=None, duration=278, lines=[
                    "[0:06] (the sound gets louder: -48 to -14 dBFS)",
                    "[0:26-0:58] section (MOSS-Music): verse. Words it heard: the night is long and wide"],
                    heard={"ear": "music", "tempo": 92.3, "meter": 4, "voice": 161, "words": False,
                           "about": [{"from": 0, "to": 278, "by": "MOSS-Music", "text": "Synth pop, bright."}]},
                    captions={"kind": "uploader", "lines": ["[0:26] The night is long and wide"]},
                    pictures=self.pictures if at == 0 else [])
            else:
                out.update(lines=[], captions={"kind": "film", "lines": ["[0:23] You're a jerk, Thom."]},
                           sheets=[{"n": 0, "from": 0, "to": 60}, {"n": 1, "from": 60, "to": 120}])
            return httpx.Response(200, json=out)
        if method == "GET" and path.startswith("/jobs/abc123def456/sheets/"):
            return httpx.Response(200, content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"})
        if method == "GET" and path.startswith("/jobs/abc123def456/pictures/"):
            return httpx.Response(200, content=b"\x89PNG picture", headers={"content-type": "image/png"})
        if (method, path) == ("GET", "/jobs"):
            return httpx.Response(200, json={"jobs": [self.job("done")]})
        if (method, path) == ("GET", "/plex"):
            return httpx.Response(200, json=self.plex)
        if (method, path) == ("POST", "/plex/link"):
            return httpx.Response(200, json={"url": "https://app.plex.tv/auth#?clientID=c&code=k", "minutes": 15})
        if (method, path) == ("POST", "/plex/search"):
            return httpx.Response(200, json={"results": [
                {"source": "plex:" + "a" * 40 + ":123", "title": "Tears of Steel", "year": 2012, "type": "movie",
                 "server": "Den", "minutes": 12}]})
        if (method, path) == ("GET", "/health"):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"error": "There is no such route."})


@pytest.fixture
def cfg():
    return load()


def _ctx(store, cfg, cid="c1") -> ToolContext:
    async def emit(*_a, **_k):
        return None
    return ToolContext(conversation_id=cid, turn_id="t1", tool_call_id="call1", store=store, config=cfg,
                       emit=emit, max_output_tokens=8000)


def _video(cfg, helper) -> Video:
    v = Video(cfg, transport=httpx.MockTransport(helper))
    v.poll = 0
    return v


async def test_a_watch_starts_and_its_first_stretch_comes_back_in_order(store, cfg):
    h = Helper()
    v = _video(cfg, h)
    out = await v.watch(_ctx(store, cfg), "https://youtu.be/aqz-KE-bpKQ", start="1:30", minutes=2)
    assert not out.is_error and out.content.startswith("Started watch #abc123def456.")
    assert h.posted[-1] == {"kind": "watch", "source": "https://youtu.be/aqz-KE-bpKQ", "start": 90,
                            "length": 120, "conversation": "c1"}
    out = await v.watch(_ctx(store, cfg), "https://youtu.be/aqz-KE-bpKQ", background=True)
    lines = out.content.splitlines()
    assert lines[0] == "#abc123def456 A short film (Blender), 0:00 to 3:00 of 0:00-10:00"
    assert lines[1] == "What the watching model saw and heard:"
    assert lines[-1] == 'next: video__read(id="abc123def456", at="3:00")'
    assert h.looks == 2, "it looked until the watch was done"
    out = await v.read(_ctx(store, cfg), "#abc123def456", at="3:00")
    assert "[3:00] A bird lands" in out.content and out.content.endswith('at="6:00")')
    assert "at=180" in h.seen[-1]


def test_a_stretch_the_watch_has_not_reached_says_so():
    """Bug 81: read while the watch went on, a stretch came back empty and was full an hour
    later, so the agent kept a rule that one empty read is never final."""
    s = {"from": 2980, "done_to": 2700, "state": "watching"}
    assert Video._nothing(s) == "(not watched yet: the watch is done up to 45:00)"
    assert Video._nothing({**s, "state": "failed"}) == "(not watched: the watch stopped at 45:00)"
    assert Video._nothing({**s, "state": "done"}) == "(nothing in this stretch)"
    assert Video._nothing({**s, "from": 2600}) == "(nothing in this stretch)"


async def test_a_stretch_cut_at_the_live_edge_goes_on_where_the_watch_is(store, cfg):
    """Bug 88: following next: from a stretch the running watch had not finished skipped the
    rest of it; next is where the watch is now, also from a stretch it has not reached."""
    h = Helper()
    h.live_to = 220
    v = _video(cfg, h)
    cut = (await v.read(_ctx(store, cfg), "abc123def456", at="3:00")).content.splitlines()
    assert cut[0].endswith("(still going: done up to 3:40)")
    assert cut[-1] == 'next: video__read(id="abc123def456", at="3:40")'
    ahead = (await v.read(_ctx(store, cfg), "abc123def456", at="5:00")).content.splitlines()
    assert ahead[2] == "(not watched yet: the watch is done up to 3:40)"
    assert ahead[-1] == 'next: video__read(id="abc123def456", at="3:40")'
    h.live_to = None
    done = await v.read(_ctx(store, cfg), "abc123def456", at="3:00")
    assert done.content.endswith('at="6:00")'), "a finished watch reads on a whole stretch"


async def test_a_watch_that_stopped_early_goes_on_under_its_id_from_where_its_log_ends(store, cfg):
    h = Helper()
    h.stopped_at = 360.0
    v = _video(cfg, h)
    out = await v.continue_watch(_ctx(store, cfg), "#abc123def456")
    assert not out.is_error and out.content.startswith("Watch #abc123def456 goes on from 6:00. ")
    assert out.display == {"watch": "abc123def456"} and "POST /jobs/abc123def456/resume?" in h.seen
    # in the background: the first stretch it adds, read from where it stopped
    out = await v.continue_watch(_ctx(store, cfg), "abc123def456", background=True)
    assert out.content.splitlines()[0] == "#abc123def456 A short film (Blender), 6:00 to 9:00 of 0:00-10:00"
    assert "at=360" in h.seen[-1] and h.looks == 2, "it looked until the watch was done"
    # a watch that is done says so in the helper's words; one it does not know too
    h.stopped_at = None
    out = await v.continue_watch(_ctx(store, cfg), "abc123def456")
    assert out.is_error and out.content.startswith("That watch is done.")
    out = await v.continue_watch(_ctx(store, cfg), "nope")
    assert out.is_error and out.content == "There is no such route."
    from celine.skills import Registry
    reg = Registry()
    reg.add(Video(cfg))
    said = reg.tools["video__continue_watch"].detached.format_map(
        {"id": "abc123def456", "background": True, "job": "406381a2"})
    assert said.startswith("Watch abc123def456 goes on in the background as job 406381a2;")


def test_a_queued_watch_reads_by_its_source_and_a_background_one_names_it(cfg):
    """Two Plex watches in the queue both read "a Plex item", and a watch sent to the background named only its
    job: the wrong ones were stopped twice."""
    from celine.skills import Registry
    plex = {"id": "86144e7180b8", "source": {"kind": "plex", "server": "a" * 40, "item": "254348"},
            "title": None, "state": "queued"}
    assert Video._line(plex) == f"#86144e7180b8 plex:{'a' * 40}:254348: queued", "the id video__watch took"
    assert Video._line({**plex, "title": "A Quiet Harbour (1994)"}).startswith(
        "#86144e7180b8 A Quiet Harbour (1994): ")
    reg = Registry()
    reg.add(Video(cfg))
    said = reg.tools["video__watch"].detached.format_map(
        {"source": "plex:x:1", "background": True, "job": "406381a2"})
    assert said.startswith("Watching plex:x:1 in the background as job 406381a2;")
    said = reg.tools["video__wait"].detached.format_map(
        {"id": "86144e7180b8", "background": True, "job": "dbcbe63e"})
    assert said.startswith("Waiting for #86144e7180b8 in the background as job dbcbe63e;")


async def test_the_stills_way_brings_captions_and_a_sheet_a_minute_as_images(store, cfg, monkeypatch):
    saved: list[str] = []

    async def sees(_cfg, _ctx):
        return True

    async def keep(_cfg, _store, chunks, name, media, **kw):
        data = b"".join([c async for c in chunks])
        assert data.startswith(b"\xff\xd8") and media == "image/jpeg" and kw["tool_call_id"] == "call1"
        saved.append(name)
        return {"id": f"att{len(saved)}", "sha256": "s"}

    monkeypatch.setattr(attachments, "turn_sees_images", sees)
    monkeypatch.setattr(attachments, "save_stream", keep)
    v = _video(cfg, Helper(mode="stills"))
    out = await v.read(_ctx(store, cfg), "abc123def456")
    assert out.images == ["att1", "att2"] and saved == ["abc123def456-0000.jpg", "abc123def456-0001.jpg"]
    assert "The film's subtitles:\n[0:23] You're a jerk, Thom.\nStills 0:00 to 1:00" in out.content
    assert "Stills 0:00 to 1:00, one every 5 seconds, each with its time:" in out.content


async def test_a_listen_reads_with_what_was_measured_and_the_uploaders_captions(store, cfg):
    h = Helper(mode="listen")
    v = _video(cfg, h)
    out = await v.listen(_ctx(store, cfg), "https://youtu.be/ilqeDbu4E8Y", background=True)
    assert h.posted[-1]["kind"] == "listen"
    assert out.content.splitlines() == [
        "#abc123def456 A short film (Blender), 0:00 to 3:00 of 0:00-4:38",
        "Heard as music, about 92 beats a minute, 4 beats to a bar.",
        "A voice in 2:41 of it. Its words are the captions below, so they were not written down again.",
        "What MOSS-Music says of 0:00 to 4:38: Synth pop, bright.",
        "What was heard:",
        "[0:06] (the sound gets louder: -48 to -14 dBFS)",
        "[0:26-0:58] section (MOSS-Music): verse. Words it heard: the night is long and wide",
        "The uploader's captions:",
        "[0:26] The night is long and wide",
        "That is the end of what was heard."]
    out = await v.listen(_ctx(store, cfg), "https://youtu.be/x", minutes=25)
    assert out.is_error and out.content.startswith("A listen hears at most 20 minutes;")


async def test_a_listens_first_stretch_brings_the_sound_drawn_as_pictures_she_sees(store, cfg, monkeypatch):
    saved: list[tuple[str, str]] = []

    async def sees(_cfg, _ctx):
        return True

    async def keep(_cfg, _store, chunks, name, media, **kw):
        data = b"".join([c async for c in chunks])
        assert data.startswith(b"\x89PNG") and kw["in_message"] is False
        saved.append((name, media))
        return {"id": f"att{len(saved)}", "sha256": "s"}

    monkeypatch.setattr(attachments, "turn_sees_images", sees)
    monkeypatch.setattr(attachments, "save_stream", keep)
    h = Helper(mode="listen")
    h.pictures = ["spectrum.png", "waveform.png"]
    out = await _video(cfg, h).read(_ctx(store, cfg), "abc123def456")
    assert out.images == ["att1", "att2"]
    assert saved == [("abc123def456-spectrum.png", "image/png"), ("abc123def456-waveform.png", "image/png")]
    assert ("The sound of 0:00 to 4:38 drawn as a spectrogram: time across, its seconds counted from 0:00; "
            "frequency up, on a log scale from 20 Hz; loudness as colour, in dBFS:") in out.content
    assert "The waveform of 0:00 to 4:38, loudness over time:" in out.content
    assert "GET /jobs/abc123def456/pictures/spectrum.png?" in h.seen


async def test_her_track_goes_to_the_helper_by_its_sha256_and_a_stranger_id_is_refused(store, cfg):
    from celine import attachments as atts
    await store.write(lambda c: c.execute(
        "INSERT INTO media_batches(id, kind, state, items, created_at) VALUES('b1', 'music', 'done', '[]', 'x')"))
    row = await atts.save_generated(cfg, store, b"ID3 a track", "tower-hum.mp3", "audio/mpeg", None,
                                    in_message=False, batch_id="b1")
    h = Helper(mode="listen")
    v = _video(cfg, h)
    out = await v.listen(_ctx(store, cfg), row["id"][:8].upper())
    assert not out.is_error and out.content.startswith("Started listen #abc123def456.")
    assert h.files == [{"sha": row["sha256"], "ext": "mp3", "length": "11", "data": b"ID3 a track"}]
    assert h.posted[-1]["source"] == f"file:{row['sha256']}" and h.posted[-1]["title"] == "tower-hum.mp3"
    out = await v.watch(_ctx(store, cfg), row["id"][:8])
    assert out.is_error and out.content == "tower-hum.mp3 is not a video."
    out = await v.listen(_ctx(store, cfg), "0123abcd")
    assert out.is_error and out.content.startswith("There is no file 0123abcd of yours:")
    assert len(h.files) == 1


async def test_her_tracks_list_with_their_ids_and_a_listen_finds_one_by_its_title(store, cfg):
    from celine import attachments as atts
    from celine.skills.music import MusicGenerator
    await store.write(lambda c: c.execute(
        "INSERT INTO media_batches(id, kind, state, items, created_at) VALUES('b1', 'music', 'done', '[]', 'x')"))
    one = await atts.save_generated(cfg, store, b"ID3 one", "tower_hum-58178951067900.mp3", "audio/mpeg", None,
                                    in_message=False, batch_id="b1")
    two = await atts.save_generated(cfg, store, b"ID3 two", "tower_hum_industrial-112874722676781.mp3",
                                    "audio/mpeg", None, in_message=False, batch_id="b1")
    items = [{"index": 0, "title": "Tower Hum", "style": "dream pop, soft synths", "instrumental": False,
              "seconds": 103.3, "preset": "yue2", "attachment": {"id": one["id"]}},
             {"index": 1, "title": "Tower Hum (industrial)", "style": "90s industrial metal", "instrumental": False,
              "seconds": 48.6, "preset": "yue2", "attachment": {"id": two["id"]}}]
    await store.write(lambda c: c.execute("UPDATE media_batches SET items = ? WHERE id = 'b1'", (json.dumps(items),)))
    out = await MusicGenerator(cfg).list_tracks(_ctx(store, cfg))
    blocks = out.content.split("\n\n")
    assert blocks[0].startswith(two["id"][:8]) and blocks[1].startswith(one["id"][:8]), "newest first"
    assert '"Tower Hum (industrial)", song, 49 s, yue2' in blocks[0] and "\n  style: 90s industrial metal" in blocks[0]
    assert (await MusicGenerator(cfg).list_tracks(_ctx(store, cfg), scope="kept")).content == "You have kept no tracks."
    h = Helper(mode="listen")
    v = _video(cfg, h)
    for name, want in (("tower hum", one), ("TOWER HUM (industrial)", two)):
        out = await v.listen(_ctx(store, cfg), name)
        assert not out.is_error and h.posted[-1]["source"] == f"file:{want['sha256']}", "an exact title wins"
    out = await v.listen(_ctx(store, cfg), "tower")
    assert out.is_error and out.content.startswith("Several tracks are called 'tower': ")
    assert one["id"][:8] in out.content and two["id"][:8] in out.content
    out = await v.listen(_ctx(store, cfg), "Lighthouse")
    assert out.is_error and out.content.startswith("There is no track called 'Lighthouse':")
    sent = len(h.files)
    out = await v.watch(_ctx(store, cfg), "youtu.be/x")
    assert not out.is_error and len(h.files) == sent, "a link without https:// is no title"


async def test_the_helpers_words_come_through_and_a_bad_time_is_refused(store, cfg):
    v = _video(cfg, Helper())
    out = await v.watch(_ctx(store, cfg), "https://evil.example/x")
    assert out.is_error and out.content == "source is a YouTube link or a plex:... id."
    out = await v.watch(_ctx(store, cfg), "https://youtu.be/x", start="soon")
    assert out.is_error and "like \"12:30\"" in out.content
    assert (await v.list(_ctx(store, cfg))).content.startswith("#abc123def456 A short film (Blender), 0:00 to 10:00: done")
    from zoneinfo import ZoneInfo
    old = {**Helper().job("failed"), "error": "YouTube stopped the download.", "created": 1794118015.84}
    assert Video._dated(old, ZoneInfo("UTC")) == \
        "Sun 8 Nov 06:06 #abc123def456 A short film (Blender), 0:00 to 10:00: failed: YouTube stopped the download.", \
        "a watch in the list says when it was asked for"

    def down(request):
        raise httpx.ConnectError("refused", request=request)
    out = await Video(cfg, transport=httpx.MockTransport(down)).list(_ctx(store, cfg))
    assert out.is_error and out.content.endswith(
        "did not answer GET /jobs (ConnectError). Nothing took the connection: it runs on the GPU machine, "
        f"and {cfg.user} can start it."), out.content
    out = await Video(cfg, transport=httpx.MockTransport(lambda r: httpx.Response(401))).list(_ctx(store, cfg))
    assert out.is_error and out.content == "The watch helper does not take the app's key."


async def test_a_wait_goes_on_past_looks_the_helper_did_not_answer(store, cfg):
    """Bug 92: one look that timed out ended a wait on a watch that went on and finished, with a sentence saying
    the helper was down and who could start it."""
    h = Helper()
    left = {"timeouts": 3}

    def slow(request):
        if request.url.path == "/jobs/abc123def456" and left["timeouts"]:
            left["timeouts"] -= 1
            raise httpx.ConnectTimeout("timed out", request=request)
        return h(request)
    v = Video(cfg, transport=httpx.MockTransport(slow))
    v.poll = 0
    out = await v.wait(_ctx(store, cfg), "abc123def456", background=True)
    assert not out.is_error and out.content.startswith("#abc123def456 A short film (Blender), 0:00 to 3:00")
    assert h.looks == 2, "it looked on until the watch was done"
    left["timeouts"] = 99
    out = await v.wait(_ctx(store, cfg), "abc123def456", background=True)
    assert out.is_error and out.content.endswith(
        "did not answer GET /jobs/abc123def456 (ConnectTimeout). 8 looks in a row at #abc123def456 went "
        "unanswered; the watch may still be going, and video__list shows where it stands."), out.content
    assert "can start it" not in out.content


async def test_plex_links_once_and_searches_her_servers(store, cfg):
    h = Helper()
    v = _video(cfg, h)
    out = await v.link_plex(_ctx(store, cfg))
    assert "https://app.plex.tv/auth#?clientID=c&code=k" in out.content and "press Allow" in out.content
    h.plex = {"linked": True, "user": "celine", "servers": ["Den", "Attic"]}
    out = await v.link_plex(_ctx(store, cfg))
    assert out.content == "Plex is linked as celine; servers: Den, Attic."
    out = await v.search_plex(_ctx(store, cfg), "tears")
    assert out.content == "plex:" + "a" * 40 + ":123  Tears of Steel (2012), 12 min, on Den"


async def test_the_probe_reads_the_helper_and_the_app_makes_its_key(cfg, monkeypatch, tmp_path):
    calls = []

    class Client(httpx.AsyncClient):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(lambda r: (calls.append(r.url.path), httpx.Response(200))[1])
            super().__init__(*a, **kw)
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    got = await video_mod.probe(cfg, None)
    assert got["state"] == "ready" and calls == ["/health"]
    assert services_mod.DEFAULTS["video"] == "http://host.docker.internal:8790"
    written = keys.ensure(tmp_path / "var")
    assert "video.key" in written or (tmp_path / "var" / "keys" / "video.key").exists()
    key = keys.read(tmp_path / "var", "video")
    assert keys.key_hash(tmp_path / "var", "video") == keys.sha256(key)


def test_times_read_as_minutes_and_hours():
    assert seconds("12:30") == 750 and seconds("1:05:00") == 3900 and seconds(None) is None
    with pytest.raises(ValueError):
        seconds("12 minutes")
