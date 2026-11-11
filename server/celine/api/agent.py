"""The agent (celine/agent.py): GET and PUT /api/agent, its portrait. A write reloads `cfg.agent` and
sends `agent.changed` on the global feed."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import FileResponse

from .. import agent
from ..errors import NotFound
from ..settings import files
from ..settings.files import Invalid, version
from ..settings import write
from .deps import etag, json_body
from ..core.route_params import TheConfig, TheStore
from ..core.event_types import AGENT_CHANGED

router = APIRouter(prefix="/api")
log = logging.getLogger("celine.agent")


async def _changed(store, cfg) -> None:
    await agent.refresh(store, cfg)
    store.emit_global({"type": AGENT_CHANGED})


@router.get("/agent")
async def get_agent(cfg: TheConfig, response: Response) -> dict:
    """The agent's plain fields, persona, talk (null: the shipped one, which is `talk_shipped`),
    voice and `voice_text` (as its History keeps it), its portrait, plus `version`."""
    out = agent.package(cfg)
    return {**out, "version": etag(response, version(out))}


@router.put("/agent")
async def put_agent(request: Request, store: TheStore, cfg: TheConfig, response: Response,
                  if_match: str | None = Header(default=None)) -> dict:
    """Any of: name, pronouns, color, persona, talk (null: back to the shipped one),
    voice (settings/voice.py). Checked whole before anything is written."""
    body = await json_body(request)
    async with write.lock():
        files.check_version("agent", agent.package(cfg), if_match)
        await agent.write(store, cfg, body)
        await _changed(store, cfg)
    return await get_agent(cfg, response)


@router.put("/agent/portrait")
async def put_portrait(request: Request, store: TheStore, cfg: TheConfig) -> dict:
    """The agent's portrait: multipart with one file `image` (png, jpg or webp, at most the
    attachment limit), or JSON {attachment} (an attachment's id: a picture it made, one
    the user sent). -> {portrait: {url}}."""
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        form = await request.form()
        image = form.get("image")
        if image is None or isinstance(image, str):
            raise Invalid("image", "'image' is missing.")
        limit = int(cfg.r("max_attachment_bytes"))
        data = await image.read(limit + 1)
        if len(data) > limit:
            raise Invalid("image", f"The image is larger than {limit:,} bytes.")
        field = "image"
    else:
        from ..attachments import blob_path, get
        body = await json_body(request, need=("attachment",), allow=("attachment",))
        row = await get(store, str(body["attachment"]))
        if row is None or row["kind"] != "image":
            raise NotFound("image")
        try:
            data = blob_path(cfg, row["sha256"]).read_bytes()
        except OSError:
            raise NotFound("image") from None
        field = "attachment"
    try:
        await agent.set_portrait(store, cfg, data)
    except ValueError as e:
        raise Invalid(field, str(e)) from None
    await _changed(store, cfg)
    return {"portrait": agent.portrait_view(cfg.agent)}


@router.delete("/agent/portrait")
async def delete_portrait(store: TheStore, cfg: TheConfig) -> dict:
    """{deleted}: false when there was none."""
    if not (cfg.agent and cfg.agent.portrait):
        return {"deleted": False}
    await agent.set_portrait(store, cfg, None)
    await _changed(store, cfg)
    return {"deleted": True}


@router.get("/agent/portrait", response_model=None)
async def get_portrait(cfg: TheConfig) -> FileResponse | Response:
    """The agent's portrait; 204 when there is none (the web shows its initial)."""
    got = agent.portrait_file(cfg)
    if got is None:
        return Response(status_code=204, headers={"Cache-Control": "no-cache"})
    return FileResponse(got[0], media_type=got[1], headers={"Cache-Control": "no-cache"})


@router.get("/agent/mood")
async def mood(store: TheStore) -> dict:
    """The agent's mood next to its name (skills/mind.py mind__set_status): {text, set_at}, null until
    it set one; every change is the global event `mood`."""
    from ..skills.mind import get_mood
    return {"mood": await get_mood(store)}
