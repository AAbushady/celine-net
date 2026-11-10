"""The video skill against a fake watch helper: a watch starts and comes back as its first stretch, a stretch reads
in order with where the next begins, the stills way brings its sheets as images, the helper's words come through,
Plex links and searches, the probe, and the app's key for the helper."""
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

    def job(self, state="queued"):
        return {"id": "abc123def456", "source": {"kind": "youtube", "url": "https://www.youtube.com/watch?v=x"},
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
            if "youtu" not in body["source"] and not body["source"].startswith("plex:"):
                return httpx.Response(400, json={"error": "source is a YouTube link or a plex:... id."})
            return httpx.Response(201, json=self.job())
        if (method, path) == ("GET", "/jobs/abc123def456"):
            self.looks += 1
            return httpx.Response(200, json=self.job("done" if self.looks >= self.steps else "watching"))
        if (method, path) == ("GET", "/jobs/abc123def456/log"):
            at = float(request.url.params.get("at", 0))
            out = {"id": "abc123def456", "title": "A short film (Blender)", "mode": self.mode, "state": "done",
                   "from": at, "to": at + 180, "start": 0, "end": 600, "done_to": 600, "next": at + 180}
            if self.mode == "omni":
                out["lines"] = [f"[{int(at) // 60}:00] A bird lands on a branch; soft strings play."]
            else:
                out.update(lines=["[0:23] You're a jerk, Thom."], subtitles=True,
                           sheets=[{"n": 0, "from": 0, "to": 60}, {"n": 1, "from": 60, "to": 120}])
            return httpx.Response(200, json=out)
        if method == "GET" and path.startswith("/jobs/abc123def456/sheets/"):
            return httpx.Response(200, content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"})
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
    assert h.posted[-1] == {"source": "https://youtu.be/aqz-KE-bpKQ", "start": 90, "length": 120,
                            "conversation": "c1"}
    out = await v.watch(_ctx(store, cfg), "https://youtu.be/aqz-KE-bpKQ", background=True)
    lines = out.content.splitlines()
    assert lines[0] == "#abc123def456 A short film (Blender), 0:00 to 3:00 of 0:00-10:00"
    assert lines[1] == "What the watching model saw and heard:"
    assert lines[-1] == 'next: video__read(id="abc123def456", at="3:00")'
    assert h.looks == 2, "it looked until the watch was done"
    out = await v.read(_ctx(store, cfg), "#abc123def456", at="3:00")
    assert "[3:00] A bird lands" in out.content and out.content.endswith('at="6:00")')
    assert "at=180" in h.seen[-1]


def test_a_queued_watch_reads_by_its_source_and_a_background_one_names_it(cfg):
    """Two Plex watches in the queue both read "a Plex item", and a watch sent to the background named only its
    job: the wrong ones were stopped twice."""
    from celine.skills import Registry
    plex = {"id": "86144e7180b8", "source": {"kind": "plex", "server": "a" * 40, "item": "254348"},
            "title": None, "state": "queued"}
    assert Video._line(plex) == f"#86144e7180b8 plex:{'a' * 40}:254348: queued", "the id video__watch took"
    assert Video._line({**plex, "title": "In the Mouth of Madness (1994)"}).startswith(
        "#86144e7180b8 In the Mouth of Madness (1994): ")
    reg = Registry()
    reg.add(Video(cfg))
    said = reg.tools["video__watch"].detached.format_map(
        {"source": "plex:x:1", "background": True, "job": "406381a2"})
    assert said.startswith("Watching plex:x:1 in the background as job 406381a2;")
    said = reg.tools["video__wait"].detached.format_map(
        {"id": "86144e7180b8", "background": True, "job": "dbcbe63e"})
    assert said.startswith("Waiting for watch 86144e7180b8 in the background as job dbcbe63e;")


async def test_the_stills_way_brings_subtitles_and_a_sheet_a_minute_as_images(store, cfg, monkeypatch):
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
    assert "Subtitles:\n[0:23] You're a jerk, Thom." in out.content
    assert "Stills 0:00 to 1:00, one every 5 seconds, each with its time:" in out.content


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
    assert out.is_error and "does not answer" in out.content
    out = await Video(cfg, transport=httpx.MockTransport(lambda r: httpx.Response(401))).list(_ctx(store, cfg))
    assert out.is_error and out.content == "The watch helper does not take the app's key."


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
