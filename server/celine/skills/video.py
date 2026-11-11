"""Watching videos through the watch helper on the GPU machine (a small server outside this repo, the "video"
service): video__watch hands it a YouTube link or a Plex item, it fetches the video and watches it with an
audio-visual model, one frame and one second of sound at a time, into a timestamped log. While the GPU is in use
it makes what she watched with before instead: a sheet of stills per minute and the subtitles, which her own model
looks at. video__read gives either a stretch at a time, so she watches in order. Plex is her own account, linked
through Plex's sign-in page in her browser (video__link_plex); its token stays with the helper.

The helper's address is a service (settings/services.py); the app's key for it is <data>/keys/video.key, whose
sha256 (video.hash) the helper checks."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import urlsplit

import anyio
import httpx

from .. import attachments, vision
from ..clock import zone
from ..core.ids import when
from ..settings import services as services_mod
from . import ToolContext, ToolResult, described, guide, tool

SERVICE = "video"
POLL = 15.0                 # seconds between two looks at a running watch
READ_SPAN = 180             # seconds of film one read gives
WAIT_MARGIN = 120           # a wait in a job returns this long before the job's own limit
DETACHED = ("Watching {source} in the background as job {job}; you get the log's first stretch when it is "
            "done. video__list shows how far it is, by its source until the helper has its title.")
WAIT_DETACHED = ("Waiting for watch {id} in the background as job {job}; you get the log's first stretch "
                 "when it is done.")
CONTINUE_DETACHED = ("Watch {id} goes on in the background as job {job}; you get the first stretch it adds "
                     "when it is done.")
CLOCK = re.compile(r"^(?:(\d+):)?(\d{1,3}):(\d{2})$")


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
    """The helper answered with an error, or did not answer: the sentence for her."""


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
                    read: float = 30.0) -> dict:
        at = address(self.cfg)
        try:
            async with services_mod.client(self.cfg, SERVICE, read, transport=self.transport) as client:
                r = await client.request(method, at + path, json=json, params=params)
        except httpx.HTTPError as e:
            raise HelperError(f"The watch helper at {urlsplit(at).netloc} does not answer "
                              f"({type(e).__name__}). It runs on the GPU machine; {self.cfg.user} can start it.")
        if r.status_code == 401:
            raise HelperError("The watch helper does not take the app's key.")
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400:
            raise HelperError(data.get("error") or f"The watch helper answered with error {r.status_code}.")
        return data

    async def _sheet(self, jid: str, n: int):
        at = address(self.cfg)
        async with services_mod.client(self.cfg, SERVICE, 60.0, transport=self.transport) as client:
            async with client.stream("GET", f"{at}/jobs/{jid}/sheets/{n}") as r:
                if r.status_code != 200:
                    raise HelperError(f"Sheet {n} of watch {jid} is not there.")
                async for chunk in r.aiter_bytes():
                    yield chunk

    # ---- words ---------------------------------------------------------------------------------------------------

    @staticmethod
    def _source(j: dict) -> str:
        """What a watch was given, as video__watch took it: its link, or the plex: id from video__search_plex."""
        s = j.get("source") or {}
        if s.get("kind") == "plex":
            return f"plex:{s.get('server')}:{s.get('item')}"
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
        way = {"omni": "seen and heard", "stills": "as stills and subtitles"}.get(j.get("mode") or "", "")
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
        source: Annotated[str, "A YouTube link (youtube.com/watch?v=..., youtu.be/..., /shorts/...) or a plex:... "
                               "id from video__search_plex."],
        start: Annotated[str | None, 'Where to begin, like "12:30" or "1:05:00". The beginning when left out.'] = None,
        minutes: Annotated[float | None, "How many minutes to watch. To the end when left out."] = None,
        background: Annotated[bool, "true: wait for it in the background; the log's first stretch comes to you "
                                    "when it is done. false: start it and come back with video__wait."] = False,
    ) -> ToolResult:
        """Watch a video: the helper fetches it and watches it with sight and sound, or, while the GPU is in use,
        makes stills and subtitles for you to look at. Then video__read gives it a stretch at a time."""
        try:
            begin = seconds(start) or 0
        except ValueError:
            return ToolResult(content='start is a time in the video, like "12:30" or "1:05:00".', is_error=True)
        if minutes is not None and minutes <= 0:
            return ToolResult(content="minutes is how long to watch, more than 0.", is_error=True)
        try:
            j = await self._call("POST", "/jobs", json={
                "source": source.strip(), "start": begin, "length": (minutes or 0) * 60,
                "conversation": ctx.conversation_id})
        except HelperError as e:
            return ToolResult(content=str(e), is_error=True)
        if not background:
            return ToolResult(content=f"Started watch #{j['id']}. video__wait(id=\"{j['id']}\", background=true) "
                                      f"brings it when it is done; video__list shows how far it is.",
                              display={"watch": j["id"]})
        return await self._until_done(ctx, j["id"], in_job=True)

    @tool(mode="detach", read_only=True, open_world=True, background="background", timeout=600,
          detached=WAIT_DETACHED)
    async def wait(
        self,
        ctx: ToolContext,
        id: Annotated[str, "The watch's id, from video__watch or video__list."],  # noqa: A002
        background: Annotated[bool, "true: wait in the background and get its first stretch when it is done."]
        = False,
    ) -> ToolResult:
        """Wait for a watch to finish, then get its first stretch (as video__read)."""
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
        stands: in a job, the job's limit; in the turn, the tool's and the turn's. A watch that is done gives
        its first stretch, from `at` when it was taken up again there."""
        if in_job:
            limit = float(self.cfg.r("job_timeout_seconds")) - WAIT_MARGIN
        else:
            limit = 600 - 10
            if ctx.time_left is not None:
                limit = min(limit, ctx.time_left - 5)
        waited = 0.0
        while True:
            try:
                j = await self._call("GET", f"/jobs/{jid}")
            except HelperError as e:
                return ToolResult(content=str(e), is_error=True)
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
        id: Annotated[str, "The watch's id."],  # noqa: A002
        at: Annotated[str | None, 'Where to read from, like "12:30". The start of what was watched when left '
                                  "out."] = None,
    ) -> ToolResult:
        """A stretch of about three minutes of a watch, in order: what the watching model saw and heard, or the
        subtitles with a sheet of stills per minute. The last line says where the next stretch begins."""
        try:
            where = seconds(at)
        except ValueError:
            return ToolResult(content='at is a time in the video, like "12:30".', is_error=True)
        return await self._stretch(ctx, id.strip().lstrip("#"), where)

    async def _stretch(self, ctx: ToolContext, jid: str, at: float | None) -> ToolResult:
        params = {"span": READ_SPAN}
        if at is not None:
            params["at"] = at
        try:
            s = await self._call("GET", f"/jobs/{jid}/log", params=params)
        except HelperError as e:
            return ToolResult(content=str(e), is_error=True)
        head = f"#{jid} {s['title']}, {clock(s['from'])} to {clock(s['to'])} of {clock(s['start'])}-{clock(s['end'])}"
        if s["state"] in ("fetching", "watching") and s.get("done_to") is not None and s["to"] > s["done_to"]:
            head += f" (still watching: done up to {clock(s['done_to'])})"
        lines = [head]
        images: list[str] = []
        if s["mode"] == "omni":
            lines.append("What the watching model saw and heard:")
            lines += s["lines"] or ["(nothing in this stretch)"]
        elif s["mode"] == "stills":
            lines.append("Subtitles:" if s.get("subtitles") else "No subtitles: the stills alone.")
            lines += s["lines"] if s.get("subtitles") else []
            sees = await attachments.turn_sees_images(self.cfg, ctx) if ctx.store is not None else False
            for sheet in s.get("sheets") or []:
                name = f"{jid}-{sheet['n']:04d}.jpg"
                label = (f"Stills {clock(sheet['from'])} to {clock(sheet['to'])}, one every 5 seconds, each with its "
                         f"time:")
                try:
                    row = await attachments.save_stream(
                        self.cfg, ctx.store, self._sheet(jid, sheet["n"]), name, "image/jpeg",
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
        else:
            lines.append(f"Nothing to read yet: it is {s['state']}.")
        if s.get("next") is not None:
            lines.append(f"next: video__read(id=\"{jid}\", at=\"{clock(s['next'])}\")")
        else:
            lines.append("That is the end of what was watched.")
        return ToolResult(content="\n".join(lines), display={"watch": jid, "from": s["from"], "to": s["to"]},
                          images=images or None)

    @tool()
    async def list(self, ctx: ToolContext) -> ToolResult:  # noqa: A003 - the tool's name
        """Your recent watches, newest first, each with when you asked for it and where it stands."""
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
