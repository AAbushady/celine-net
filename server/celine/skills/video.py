"""Watching and listening through the watch helper on the GPU machine (a small server outside this repo, the
"video" service). video__watch hands it a YouTube link, a Plex item or one of her clips; it fetches the video and
watches it with an audio-visual model, one frame and one second of sound at a time, into a timestamped log. While
the GPU is in use it makes what she watched with before instead: a sheet of stills per minute, which her own model
looks at. video__listen hands it a track, a video's sound or a film's: it measures the beat and where a voice is,
writes down the words and has listening models say what is heard. The source's own captions (the uploader's,
YouTube's automatic ones, a film's subtitles) come with every stretch, named as what they are. video__read gives a
stretch at a time, so she watches and listens in order. One of her own tracks or clips (or a sound or video file on
a message) is sent to the helper first, by its sha256. Plex is her own account, linked through Plex's sign-in page
in her browser (video__link_plex); its token stays with the helper.

The helper's address is a service (settings/services.py); the app's key for it is <data>/keys/video.key, whose
sha256 (video.hash) the helper checks."""
from __future__ import annotations

import mimetypes
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Annotated, Any
from urllib.parse import urlsplit

import anyio
import httpx

from .. import attachments, vision
from ..clock import zone
from ..comfyui import life
from ..core.ids import when
from ..settings import services as services_mod
from . import ToolContext, ToolResult, described, guide, tool

SERVICE = "video"
POLL = 15.0                 # seconds between two looks at a running watch
UNANSWERED = 8              # looks in a row the helper may leave unanswered before a wait ends
READ_SPAN = 180             # seconds of film one read gives
WAIT_MARGIN = 120           # a wait in a job returns this long before the job's own limit
LISTEN_MINUTES = 20         # the longest stretch one listen hears (the helper's LISTEN_MAX)
UPLOAD_READ = 120.0         # seconds the helper may take to answer a file it was sent
FILE_MAX = 1 << 30          # the largest file the helper takes (its FILE_MAX)
DETACHED = ("Watching {source} in the background as job {job}; you get the log's first stretch when it is "
            "done. video__list shows how far it is, by its source until the helper has its title.")
LISTEN_DETACHED = ("Listening to {source} in the background as job {job}; you get the first stretch when it is "
                   "done. video__list shows how far it is.")
WAIT_DETACHED = ("Waiting for #{id} in the background as job {job}; you get the log's first stretch when it is "
                 "done.")
CONTINUE_DETACHED = ("Watch {id} goes on in the background as job {job}; you get the first stretch it adds "
                     "when it is done.")
CLOCK = re.compile(r"^(?:(\d+):)?(\d{1,3}):(\d{2})$")
SHORT_ID = re.compile(r"[0-9a-f]{4,32}")
# what a stretch calls the source's own captions, by their kind (the helper's CAPTIONS)
CAPTIONS = {"uploader": "The uploader's captions", "automatic": "YouTube's automatic captions",
            "film": "The film's subtitles"}


def address(cfg) -> str:
    return services_mod.url(cfg, SERVICE) or services_mod.default(SERVICE)


def clock(s: float | None) -> str:
    s = int(round(s or 0))
    h, m, sec = s // 3600, (s // 60) % 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def seconds(text: str | None) -> float | None:
    """"12:30" or "1:05:00" as seconds; None for nothing. ValueError for anything else."""
    if text is None or not str(text).strip():
        return None
    m = CLOCK.match(str(text).strip())
    if not m:
        raise ValueError(text)
    return int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))


class HelperError(Exception):
    """The helper answered with an error, or did not answer (`silent`): the sentence for her."""

    def __init__(self, text: str, silent: bool = False):
        super().__init__(text)
        self.silent = silent


def unanswered(at: str, method: str, path: str, e: httpx.HTTPError, user: str) -> str:
    """The sentence for a call the helper did not answer: the call and httpx's error. Who can
    start the helper only when nothing took the connection: a timeout says nothing of whether
    it serves, and one ended a wait on a watch that went on and finished (bug 92)."""
    said = f"The watch helper at {urlsplit(at).netloc} did not answer {method} {path} ({type(e).__name__})."
    if isinstance(e, httpx.ConnectError):
        said += f" Nothing took the connection: it runs on the GPU machine, and {user} can start it."
    return said


class Video:
    name = "video"
    guide = guide("video")
    description = described()

    def __init__(self, cfg, transport: httpx.AsyncBaseTransport | None = None):
        self.cfg = cfg
        self.transport = transport              # tests: a fake helper
        self.poll = POLL

    # ---- the helper ----------------------------------------------------------------------------------------------

    async def _call(self, method: str, path: str, *, json: dict | None = None, params: dict | None = None,
                    read: float = 30.0, content: Any = None, headers: dict | None = None) -> dict:
        at = address(self.cfg)
        try:
            async with services_mod.client(self.cfg, SERVICE, read, transport=self.transport) as client:
                r = await client.request(method, at + path, json=json, params=params, content=content,
                                         headers=headers)
        except httpx.HTTPError as e:
            raise HelperError(unanswered(at, method, path, e, self.cfg.user), silent=True) from e
        if r.status_code == 401:
            raise HelperError("The watch helper does not take the app's key.")
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400:
            raise HelperError(data.get("error") or f"The watch helper answered with error {r.status_code}.")
        return data

    async def _image(self, jid: str, path: str):
        """A picture of a job (sheets/<n>, pictures/<name>), as chunks."""
        at = address(self.cfg)
        async with services_mod.client(self.cfg, SERVICE, 60.0, transport=self.transport) as client:
            async with client.stream("GET", f"{at}/jobs/{jid}/{path}") as r:
                if r.status_code != 200:
                    raise HelperError(f"{path} of #{jid} is not there.")
                async for chunk in r.aiter_bytes():
                    yield chunk

    async def _show(self, ctx: ToolContext, jid: str, pictures: list[tuple[str, str, str]], lines: list[str],
                    images: list[str]) -> None:
        """Pictures of a job, each (its path at the helper, its file name, its label), kept with this call: as
        images of the result when the turn's model sees them, else each described in its line."""
        sees = await attachments.turn_sees_images(self.cfg, ctx) if ctx.store is not None else False
        for path, name, label in pictures:
            try:
                row = await attachments.save_stream(
                    self.cfg, ctx.store, self._image(jid, path), name,
                    "image/png" if name.endswith(".png") else "image/jpeg",
                    tool_call_id=ctx.tool_call_id, in_message=False,
                    limit=int(self.cfg.r("max_attachment_bytes")))
            except (HelperError, httpx.HTTPError) as e:
                lines.append(f"{label} not available ({e}).")
                continue
            if sees:
                images.append(row["id"])
                lines.append(label)
            else:
                data = attachments.blob_path(self.cfg, row["sha256"]).read_bytes()
                llm = getattr(getattr(self, "app", None), "llm", None)
                said = await vision.describe(llm, data, ctx.conversation_id, ctx.turn_id, None)
                lines.append(f"{label} {said}")

    async def _her_file(self, ctx: ToolContext, ref: str, kinds: tuple[str, ...]) -> dict | str:
        """One of her files (life.own: a short id, or a track's title) whose kind is one of `kinds` (audio,
        video). -> its attachment row, or the sentence why not."""
        row = await life.own(self.cfg, ctx.store, ref, ctx.conversation_id)
        if isinstance(row, str):
            return row
        if not attachments.blob_path(self.cfg, row["sha256"]).is_file():
            return f"{row['filename']} is no longer on disk."
        if row["kind"] not in kinds:
            return f"{row['filename']} is not {' or '.join('a sound' if k == 'audio' else 'a video' for k in kinds)}."
        if row["bytes"] > FILE_MAX:
            return (f"{row['filename']} is {attachments.size(row['bytes'])}; the helper takes files up to "
                    f"{attachments.size(FILE_MAX)}.")
        return row

    async def _send(self, row: dict) -> str:
        """Her file to the helper (PUT /files/<sha256>), a MiB at a time. -> the source a job takes,
        file:<sha256>."""
        path = attachments.blob_path(self.cfg, row["sha256"])
        ext = (PurePosixPath(row["filename"]).suffix.lstrip(".").lower()
               or (mimetypes.guess_extension(row["media_type"]) or ".bin").lstrip("."))[:5]

        async def chunks():
            async with await anyio.open_file(path, "rb") as f:
                while chunk := await f.read(1 << 20):
                    yield chunk
        await self._call("PUT", f"/files/{row['sha256']}", params={"ext": ext}, content=chunks(),
                         headers={"Content-Length": str(path.stat().st_size)}, read=UPLOAD_READ)
        return f"file:{row['sha256']}"

    async def _start(self, ctx: ToolContext, kind: str, source: str, start: str | None,
                     minutes: float | None, background: bool, kinds: tuple[str, ...]) -> ToolResult:
        """A watch or a listen (`kind`) of `source`: a link or a plex: id as it is, a short id (`_her_file`, one of
        `kinds`) sent to the helper first. In the background it comes back as its first stretch."""
        try:
            begin = seconds(start) or 0
        except ValueError:
            return ToolResult(content='start is a time, like "12:30" or "1:05:00".', is_error=True)
        if minutes is not None and minutes <= 0:
            return ToolResult(content="minutes is how long, more than 0.", is_error=True)
        if kind == "listen" and minutes is not None and minutes > LISTEN_MINUTES:
            return ToolResult(content=f"A listen hears at most {LISTEN_MINUTES} minutes; listen again with start "
                                      f"for the rest.", is_error=True)
        body: dict = {"kind": kind, "source": source.strip(), "start": begin, "length": (minutes or 0) * 60,
                      "conversation": ctx.conversation_id}
        try:
            src = body["source"]
            # a short id, or a track's title (no "/", which every link has); a link or a plex: id goes as it is
            if SHORT_ID.fullmatch(src.lower()) or ("/" not in src and not src.lower().startswith("plex:")):
                row = await self._her_file(ctx, src, kinds)
                if isinstance(row, str):
                    return ToolResult(content=row, is_error=True)
                body.update(source=await self._send(row), title=row["filename"])
            j = await self._call("POST", "/jobs", json=body)
        except HelperError as e:
            return ToolResult(content=str(e), is_error=True)
        if not background:
            return ToolResult(content=f"Started {'listen' if kind == 'listen' else 'watch'} #{j['id']}. "
                                      f"video__wait(id=\"{j['id']}\", background=true) brings it when it is done; "
                                      f"video__list shows how far it is.",
                              display={"watch": j["id"]})
        return await self._until_done(ctx, j["id"], in_job=True)

    # ---- words ---------------------------------------------------------------------------------------------------

    @staticmethod
    def _source(j: dict) -> str:
        """What a watch was given, as video__watch took it: its link, or the plex: id from video__search_plex."""
        s = j.get("source") or {}
        if s.get("kind") == "plex":
            return f"plex:{s.get('server')}:{s.get('item')}"
        if s.get("kind") == "file":
            return s.get("title") or "a file"
        return s.get("url") or "?"

    @classmethod
    def _line(cls, j: dict) -> str:
        """One watch in a line: id, title (its source until the helper knows the title), where it stands."""
        title = j.get("title") or cls._source(j)
        span = ""
        if j.get("stretch"):
            a, b = j["stretch"]
            span = f", {clock(a)} to {clock(b)}"
        state = j["state"]
        if state in ("fetching", "watching"):
            done = f", up to {clock(j['done_to'])}" if j.get("done_to") else ""
            state = f"{j.get('stage') or state}{done}"
        elif state == "failed":
            state = f"failed: {j.get('error')}"
        way = {"omni": "seen and heard", "stills": "as stills", "listen": "listened to"}.get(j.get("mode") or "", "")
        why = f" ({j['why']})" if j.get("mode") == "stills" and j.get("why") else ""
        return f"#{j['id']} {title}{span}: {state}" + (f", {way}{why}" if way and j["state"] == "done" else "")

    @classmethod
    def _dated(cls, j: dict, tz) -> str:
        """A watch's line with when it was asked for in front ("Sun 8 Nov 01:06 #..."), so a failure from days
        ago reads as old in the list."""
        asked = j.get("created")
        line = cls._line(j)
        if not asked:
            return line
        return f"{when(datetime.fromtimestamp(asked, timezone.utc).isoformat(), tz)} {line}"

    # ---- tools ---------------------------------------------------------------------------------------------------

    @tool(mode="detach", read_only=False, destructive=False, open_world=True, background="background",
          timeout=600, detached=DETACHED)
    async def watch(
        self,
        ctx: ToolContext,
        source: Annotated[str, "A YouTube link (youtube.com/watch?v=..., youtu.be/..., /shorts/...), a plex:... "
                               "id from video__search_plex, or the short id of one of your clips (like f5ef648c) "
                               "or of a video file on a message."],
        start: Annotated[str | None, 'Where to begin, like "12:30" or "1:05:00". The beginning when left out.'] = None,
        minutes: Annotated[float | None, "How many minutes to watch. To the end when left out."] = None,
        background: Annotated[bool, "true: wait for it in the background; the log's first stretch comes to you "
                                    "when it is done. false: start it and come back with video__wait."] = False,
    ) -> ToolResult:
        """Watch a video: the helper fetches it and watches it with sight and sound, or, while the GPU is in use,
        makes stills for you to look at. Its captions come along. Then video__read gives it a stretch at a
        time."""
        return await self._start(ctx, "watch", source, start, minutes, background, ("video",))

    @tool(mode="detach", read_only=False, destructive=False, open_world=True, background="background",
          timeout=600, detached=LISTEN_DETACHED)
    async def listen(
        self,
        ctx: ToolContext,
        source: Annotated[str, "The short id of one of your tracks or clips (like f5ef648c) or of a sound or video "
                               "file on a message, the title of one of your tracks (music_generator__list_tracks "
                               "lists them), a YouTube link, or a plex:... id from video__search_plex."],
        start: Annotated[str | None, 'Where to begin, like "1:30". The beginning when left out.'] = None,
        minutes: Annotated[float | None, f"How many minutes to listen to, at most {LISTEN_MINUTES}. To the end "
                                         f"when left out (at most {LISTEN_MINUTES} minutes)."] = None,
        background: Annotated[bool, "true: wait for it in the background; the first stretch comes to you when it "
                                    "is done. false: start it and come back with video__wait."] = False,
    ) -> ToolResult:
        """Listen to a track, a video's sound or a film's: the helper measures the beat and where a voice is,
        writes down the words, and has a listening model say what the music is and what is heard, in order. Its
        captions come along. It needs the GPU and waits for it. Then video__read gives it a stretch at a
        time."""
        return await self._start(ctx, "listen", source, start, minutes, background, ("audio", "video"))

    @tool(mode="detach", read_only=True, open_world=True, background="background", timeout=600,
          detached=WAIT_DETACHED)
    async def wait(
        self,
        ctx: ToolContext,
        id: Annotated[str, "The id of a watch or a listen, from video__watch, video__listen or video__list."],  # noqa: A002
        background: Annotated[bool, "true: wait in the background and get its first stretch when it is done."]
        = False,
    ) -> ToolResult:
        """Wait for a watch or a listen to finish, then get its first stretch (as video__read)."""
        return await self._until_done(ctx, id.strip().lstrip("#"), in_job=background)

    @tool(mode="detach", read_only=False, destructive=False, open_world=True, background="background",
          timeout=600, detached=CONTINUE_DETACHED)
    async def continue_watch(
        self,
        ctx: ToolContext,
        id: Annotated[str, "The id of a watch that failed or was stopped, from video__list."],  # noqa: A002
        background: Annotated[bool, "true: wait for it in the background; the first stretch it adds comes to "
                                    "you when it is done. false: start it and come back with video__wait."]
        = False,
    ) -> ToolResult:
        """Take up a watch that stopped early (it failed, you stopped it, or the helper restarted under it): it
        goes on from where its log ends, into the same log, under the same id. What it had stays readable the
        whole time. A watch that is done takes video__watch with start for more of the video."""
        jid = id.strip().lstrip("#")
        try:
            j = await self._call("POST", f"/jobs/{jid}/resume")
        except HelperError as e:
            return ToolResult(content=str(e), is_error=True)
        edge = j.get("done_to")
        if not background:
            where = f" from {clock(edge)}" if edge else ""
            return ToolResult(content=f"Watch #{jid} goes on{where}. video__wait(id=\"{jid}\", background=true) "
                                      f"brings it when it is done; video__list shows how far it is.",
                              display={"watch": jid})
        return await self._until_done(ctx, jid, in_job=True, at=edge)

    async def _until_done(self, ctx: ToolContext, jid: str, in_job: bool,
                          at: float | None = None) -> ToolResult:
        """Look every POLL seconds until the watch ends, or until the wait's own limit nears, then say where it
        stands: in a job, the job's limit; in the turn, the tool's and the turn's. A look the helper did not
        answer is followed by the next one, up to UNANSWERED in a row: the watch goes on there whatever the app
        hears. A watch that is done gives its first stretch, from `at` when it was taken up again there."""
        if in_job:
            limit = float(self.cfg.r("job_timeout_seconds")) - WAIT_MARGIN
        else:
            limit = 600 - 10
            if ctx.time_left is not None:
                limit = min(limit, ctx.time_left - 5)
        waited, missed = 0.0, 0
        while True:
            try:
                j = await self._call("GET", f"/jobs/{jid}")
            except HelperError as e:
                if not e.silent:
                    return ToolResult(content=str(e), is_error=True)
                missed += 1
                if missed >= UNANSWERED or waited + self.poll > limit:
                    looks = "1 look" if missed == 1 else f"{missed} looks in a row"
                    return ToolResult(content=f"{e} {looks} at #{jid} went unanswered; the watch may still be "
                                              f"going, and video__list shows where it stands.", is_error=True)
            else:
                missed = 0
                if j["state"] in ("done", "failed", "cancelled"):
                    break
                if waited + self.poll > limit:
                    return ToolResult(content=f"Still going: {self._line(j)}. video__wait(id=\"{jid}\", "
                                              f"background=true) waits again.")
                if ctx.emit:
                    await ctx.emit(j.get("stage") or j["state"])
            await anyio.sleep(self.poll)
            waited += self.poll
        if j["state"] != "done":
            return ToolResult(content=self._line(j), is_error=j["state"] == "failed")
        return await self._stretch(ctx, jid, at)

    @tool()
    async def read(
        self,
        ctx: ToolContext,
        id: Annotated[str, "The id of a watch or a listen."],  # noqa: A002
        at: Annotated[str | None, 'Where to read from, like "12:30". The start of what was watched or heard when '
                                  "left out."] = None,
    ) -> ToolResult:
        """A stretch of about three minutes of a watch or a listen, in order: what the watching model saw and
        heard, a sheet of stills per minute, or what was heard; with the source's own captions. The last line
        says where the next stretch begins."""
        try:
            where = seconds(at)
        except ValueError:
            return ToolResult(content='at is a time in the video, like "12:30".', is_error=True)
        return await self._stretch(ctx, id.strip().lstrip("#"), where)

    @staticmethod
    def _nothing(s: dict) -> str:
        """The line of a stretch with nothing in it. One the watch has not reached says so: read while the watch
        went on, it came back empty and was full an hour later (bug 81)."""
        done = s.get("done_to")
        if done is None or s["from"] < done or s["state"] == "done":
            return "(nothing in this stretch)"
        if s["state"] in ("failed", "cancelled"):
            return f"(not watched: the watch stopped at {clock(done)})"
        return f"(not watched yet: the watch is done up to {clock(done)})"

    async def _stretch(self, ctx: ToolContext, jid: str, at: float | None) -> ToolResult:
        params = {"span": READ_SPAN}
        if at is not None:
            params["at"] = at
        try:
            s = await self._call("GET", f"/jobs/{jid}/log", params=params)
        except HelperError as e:
            return ToolResult(content=str(e), is_error=True)
        head = f"#{jid} {s['title']}, {clock(s['from'])} to {clock(s['to'])} of {clock(s['start'])}-{clock(s['end'])}"
        if s.get("duration") and s["end"] < s["duration"] - 5:
            head += f" (it runs {clock(s['duration'])})"
        going = s["state"] in ("fetching", "watching")
        if going and s.get("done_to") is not None and s["to"] > s["done_to"]:
            head += f" (still going: done up to {clock(s['done_to'])})"
        lines = [head]
        images: list[str] = []
        cap = s.get("captions")
        said = [f"{CAPTIONS.get(cap['kind'], 'Captions')}:", *(cap["lines"] or ["(none in this stretch)"])] \
            if cap else []
        if not cap and s.get("captions_missing"):
            said = [f"No captions: YouTube did not give them ({s['captions_missing']})."]
        if s["mode"] == "omni":
            lines.append("What the watching model saw and heard:")
            lines += s["lines"] or [self._nothing(s)]
            lines += said
        elif s["mode"] == "listen":
            lines += self._heard(s, bool(cap))
            lines += said
            span = f"{clock(s['start'])} to {clock(s['end'])}"
            labels = {"spectrum.png": f"The sound of {span} drawn as a spectrogram: time across, its seconds "
                                      f"counted from {clock(s['start'])}; frequency up, on a log scale from 20 Hz; "
                                      f"loudness as colour, in dBFS:",
                      "waveform.png": f"The waveform of {span}, loudness over time:"}
            await self._show(ctx, jid, [(f"pictures/{p}", f"{jid}-{p}", labels[p])
                                        for p in s.get("pictures") or [] if p in labels], lines, images)
        elif s["mode"] == "stills":
            lines += said or ["No captions: the stills alone."]
            await self._show(ctx, jid, [
                (f"sheets/{sheet['n']}", f"{jid}-{sheet['n']:04d}.jpg",
                 f"Stills {clock(sheet['from'])} to {clock(sheet['to'])}, one every 5 seconds, each with its time:")
                for sheet in s.get("sheets") or []], lines, images)
        else:
            lines.append(f"Nothing to read yet: it is {s['state']}.")
        nxt = s.get("next")
        if nxt is not None and going:
            # at the live edge the next stretch begins where the watch is, so following it skips nothing (bug 88)
            nxt = min(nxt, s["from"] if s.get("done_to") is None else s["done_to"])
        if nxt is not None:
            lines.append(f"next: video__read(id=\"{jid}\", at=\"{clock(nxt)}\")")
        else:
            lines.append("That is the end of what was heard." if s["mode"] == "listen"
                         else "That is the end of what was watched.")
        return ToolResult(content="\n".join(lines), display={"watch": jid, "from": s["from"], "to": s["to"]},
                          images=images or None)

    @staticmethod
    def _heard(s: dict, captions: bool) -> list[str]:
        """A listen's stretch: on the first, what was measured (the beat, how much has a voice); what the
        listening model says of the music that starts in it; then what was heard, in order."""
        h = s.get("heard") or {}
        out = []
        if s["from"] <= s["start"]:
            if h.get("ear") == "music":
                beat = f", about {h['tempo']:.0f} beats a minute" if h.get("tempo") else ""
                bar = f", {h['meter']} beats to a bar" if h.get("meter") else ""
                out.append(f"Heard as music{beat}{bar}.")
            else:
                out.append("Heard as sounds: no steady beat.")
            voice = f"A voice in {clock(h['voice'])} of it." if h.get("voice") else "No voice in it."
            if h.get("voice") and not h.get("words") and captions:
                voice += " Its words are the captions below, so they were not written down again."
            out.append(voice)
        for a in h.get("about") or []:
            out.append(f"What {a['by']} says of {clock(a['from'])} to {clock(a['to'])}: {a['text']}")
        out.append("What was heard:")
        out += s["lines"] or ["(nothing in this stretch)"]
        return out

    @tool()
    async def list(self, ctx: ToolContext) -> ToolResult:  # noqa: A003 - the tool's name
        """Your recent watches and listens, newest first, each with when you asked for it and where it stands."""
        try:
            got = await self._call("GET", "/jobs")
        except HelperError as e:
            return ToolResult(content=str(e), is_error=True)
        jobs = got.get("jobs") or []
        tz = zone(ctx.config)
        return ToolResult(content="\n".join(self._dated(j, tz) for j in jobs) or "No watches yet.")

    @tool(read_only=False, destructive=True)
    async def stop(
        self,
        ctx: ToolContext,
        id: Annotated[str, "The watch's id."],  # noqa: A002
    ) -> ToolResult:
        """Stop a watch that is waiting or running. What it watched so far stays readable."""
        try:
            j = await self._call("DELETE", f"/jobs/{id.strip().lstrip('#')}")
        except HelperError as e:
            return ToolResult(content=str(e), is_error=True)
        return ToolResult(content=self._line(j))

    @tool(read_only=False, destructive=False, open_world=True)
    async def link_plex(self, ctx: ToolContext) -> ToolResult:
        """Link your own Plex account: a Plex sign-in link to open in your browser while signed in to Plex as
        yourself, then press Allow. Plex's token goes to the watch helper and stays there."""
        try:
            state = await self._call("GET", "/plex")
            if state.get("linked"):
                return ToolResult(content=f"Plex is linked as {state.get('user')}; servers: "
                                          f"{', '.join(state.get('servers') or []) or 'none shared yet'}.")
            got = await self._call("POST", "/plex/link")
        except HelperError as e:
            return ToolResult(content=str(e), is_error=True)
        return ToolResult(content=f"Open this in your browser, signed in to Plex as yourself, and press Allow "
                                  f"(it works for {got.get('minutes', 15)} minutes): {got['url']}\n"
                                  f"Then video__link_plex again shows the account and its servers.")

    @tool(open_world=True)
    async def search_plex(
        self,
        ctx: ToolContext,
        query: Annotated[str, "A film's or an episode's title, or part of it."],
    ) -> ToolResult:
        """Find a film or an episode on the Plex servers shared with you; each result's plex:... id goes to
        video__watch."""
        try:
            got = await self._call("POST", "/plex/search", json={"query": query}, read=60.0)
        except HelperError as e:
            return ToolResult(content=str(e), is_error=True)
        rows = got.get("results") or []
        if not rows:
            return ToolResult(content=f"Nothing on your Plex servers matches {query!r}.")
        return ToolResult(content="\n".join(
            f"{r['source']}  {r['title']}" + (f" ({r['year']})" if r.get("year") and r["type"] == "movie" else "")
            + f", {r['minutes']} min, on {r['server']}" for r in rows))


async def probe(cfg, services) -> dict[str, str]:
    """Settings, Features: the watch helper at the saved address (else the default) answers /health with the
    app's key."""
    from ..errors import bad_key, agent_bad_key, agent_not_set_up, agent_unreachable, not_set_up, unreachable_words
    saved = services_mod.url(cfg, SERVICE)
    at = saved or services_mod.default(SERVICE)
    found = {} if saved else {"url": at}
    try:
        async with services_mod.client(cfg, SERVICE, 2.5) as client:
            r = await client.get(at + "/health")
    except httpx.HTTPError as e:
        if not saved:
            return {"state": "unset", "detail": not_set_up("the watch helper"),
                    "why": agent_not_set_up("the watch helper")}
        return {"state": "missing", "detail": unreachable_words("the watch helper", at, e),
                "why": agent_unreachable("the watch helper", at)}
    if r.status_code in (401, 403):
        return {"state": "missing", "detail": bad_key("the watch helper", at),
                "why": agent_bad_key("the watch helper", at), "key": True, **found}
    if r.status_code >= 400:
        return {"state": "missing", "detail": f"The watch helper at {urlsplit(at).netloc} answered with error "
                                              f"{r.status_code}.", **found}
    return {"state": "ready", "detail": f"The watch helper at {urlsplit(at).netloc} answers.", **found}
