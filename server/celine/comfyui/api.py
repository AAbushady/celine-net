"""Images, music and video over HTTP, the same batches its tools make (batches.py):

- GET /api/generation-presets?kind=images|music|video: every preset of the kind as the UI
  shows it (presets.view: its editor's fields and values, `origin` shipped, edited or
  own), with `ref` ("provider/preset" in its role, null when the role does not list it)
  and `order` (its place in the role, null likewise);
  what changes them is api/presets.py;
- GET /api/batches?kind=&conversation=&limit=: newest first;
- GET /api/batches/{id};
- POST /api/batches {kind, items, count?}: the user's batch, no conversation; it runs
  in the app and returns at once, its slots filled by the events;
- POST /api/batches/{id}/items/{index}/retry: a failed, stopped or cleared item made
  again; POST /api/batches/{id}/stop -> {stopped};
- PATCH /api/batches/{id}/items/{index} {kept}: what the item made kept, or let go
  (life.py).

A batch answers as `Batch`, the shape of batches.view.

The model files the agent asked for (model_files.py), from the comfy helper of a ComfyUI
that starts on demand, its key kept here:
- GET /api/model-files: `ModelFileList`, every file that came by request and every
  request still on its way, and the free space on the models disk;
- DELETE /api/model-files/{path}: one that came by request, off the disk -> `Removed`;
- GET, PUT {key}, DELETE /api/civitai-key: the Civitai key the helper keeps for files that
  need a sign-in -> `CivitaiKey`. A singleton beside them, since DELETE
  /api/model-files/{path} would take /api/model-files/civitai-key. Guarded as PUT
  /api/secrets/{pid} is: the session gate (auth/gate.py), so only a logged-in person sets
  it; the agent's settings skill has no way to it.
"""

from typing import Annotated, Literal

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from ..core.dto import Answer, Display
from ..core.statuses import KeyState, MadeLife, ModelFileState
from ..errors import CelineError, NotFound
from . import batches as batches_mod, life as life_mod, model_files as files_mod
from . import presets as presets_mod
from .server import HelperRefused
from ..core.route_params import TheConfig, TheServices, TheStore

router = APIRouter(prefix="/api")


class Life(Answer):
    """How long what an item made stays (life.py)."""
    state: MadeLife
    display: Display
    at: str = Field(description="when it took this state: made or let go, kept, cleared")


class MadeFile(Answer):
    """What an item made: its attachment."""
    id: str
    sha256: str
    filename: str
    media_type: str
    bytes: int
    width: int | None = None
    height: int | None = None
    duration: float | None = Field(default=None, description="a clip's seconds")
    life: Life | None = None


class Still(Answer):
    """A frame a clip's preset saved beside it: what its look shows."""
    id: str
    sha256: str
    filename: str
    media_type: str


class SourceImage(Answer):
    """An image an edit starts from: an attachment, or the agent's portrait (id portrait)."""
    id: str
    sha256: str
    filename: str
    media_type: str


class Steps(Answer):
    """Where a running item's node is: step `value` of `max`."""
    value: int
    max: int


class BatchItem(Answer):
    """One slot of a batch: the request and what became of it."""
    index: int
    state: Literal["waiting", "running", "done", "failed", "stopped"]
    seed: int
    preset: str | None = None
    provider: str | None = None
    attachment: MadeFile | None = None
    seconds: float | None = None
    queued: float | None = Field(default=None,
                                 description="the seconds it waited in ComfyUI's queue")
    error: str | None = Field(default=None, description="why it failed")
    note: str | None = Field(default=None, description="what a running item does now")
    steps: Steps | None = Field(default=None,
                                description="a running item's steps, while its node counts them")
    warnings: list[str] | None = None
    # images
    prompt: str | None = None
    negative: str | None = None
    width: int | None = None
    height: int | None = None
    mode: str | None = None
    model: str | None = None
    loras: list[str] | None = None
    triggers: list[str] | None = None
    source: SourceImage | None = None
    source2: SourceImage | None = None
    source3: SourceImage | None = None
    source4: SourceImage | None = None
    # music
    style: str | None = None
    lyrics: str | None = None
    title: str | None = None
    instrumental: bool | None = None
    duration: float | None = None
    # video
    length: int | None = Field(default=None, description="a clip's frames")
    fps: float | None = None
    frames: list[Still] | None = None


class Batch(Answer):
    """A batch of images, tracks or clips (batches.view)."""
    id: str
    kind: Literal["images", "music", "video"]
    conversation_id: str | None
    link: str | None = Field(description="where its conversation opens; null for yours")
    tool_call_id: str | None
    state: Literal["running", "done", "stopped"]
    created_at: str
    finished_at: str | None
    items: list[BatchItem]


class BatchList(Answer):
    batches: list[Batch]


class BatchItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kept: bool


class ImageItemIn(BaseModel):
    prompt: str = Field(min_length=1)
    source: str | None = None          # an image attachment's id: an edit
    preset: str | None = None
    negative: str | None = None
    width: int | None = None
    height: int | None = None
    seed: int | None = None
    model: str | None = None
    loras: list[str] | None = None
    mode: str | None = None


class MusicItemIn(BaseModel):
    style: str = Field(min_length=1)
    lyrics: str | None = None
    title: str | None = None
    instrumental: bool = True
    duration: float | None = None
    seed: int | None = None


class ImagesIn(BaseModel):
    kind: Literal["images"]
    items: list[ImageItemIn] = Field(min_length=1)
    count: int = Field(default=1, ge=1)


class MusicIn(BaseModel):
    kind: Literal["music"]
    items: list[MusicItemIn] = Field(min_length=1)
    count: int = Field(default=1, ge=1)


class VideoItemIn(BaseModel):
    prompt: str = Field(min_length=1)
    source: str | None = None          # an image attachment's id: the clip starts from it
    preset: str | None = None
    negative: str | None = None
    seconds: float | None = Field(default=None, gt=0)
    width: int | None = None
    height: int | None = None
    seed: int | None = None


class VideoIn(BaseModel):
    kind: Literal["video"]
    items: list[VideoItemIn] = Field(min_length=1)
    count: int = Field(default=1, ge=1)


BatchIn = Annotated[ImagesIn | MusicIn | VideoIn, Field(discriminator="kind")]
KINDS = ("images", "music", "video")


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise CelineError("bad_request", "kind is images, music or video.", 400)
    return kind


async def _batch(store, bid: str) -> dict:
    b = await batches_mod.get(store, bid)
    if b is None:
        raise NotFound("batch")
    return b


def _runner(services):
    runner = services.batches
    if runner is None or services.llm is None:
        raise CelineError("unavailable", "ComfyUI is not running in this app.", 409)
    return runner


@router.get("/generation-presets")
async def list_presets(cfg: TheConfig, kind: str) -> dict:
    return {"presets": presets_mod.listing(cfg.settings, _kind(kind))}


def _answer(b: dict) -> Batch:
    return Batch.model_validate(batches_mod.view(b))


@router.get("/batches")
async def list_batches(store: TheStore, kind: str, conversation: str | None = None,
                       limit: int = 50) -> BatchList:
    rows = await batches_mod.list_batches(store, _kind(kind),
                                          conversation, max(1, min(limit, 500)))
    return BatchList(batches=[_answer(b) for b in rows])


@router.get("/batches/{bid}")
async def get_batch(store: TheStore, bid: str) -> Batch:
    return _answer(await _batch(store, bid))


@router.post("/batches")
async def start_batch(services: TheServices, body: BatchIn) -> Batch:
    runner = _runner(services)
    items = [i.model_dump(exclude_none=True) for i in body.items]
    try:
        b = await runner.create(body.kind, items, count=body.count)
    except batches_mod.TooMany as e:
        raise CelineError("bad_request", str(e), 400) from None
    if b["state"] == "running":
        runner.start(b["id"])
    return _answer(b)


@router.post("/batches/{bid}/items/{index}/retry")
async def retry_item(store: TheStore, services: TheServices, bid: str, index: int) -> Batch:
    await _batch(store, bid)
    try:
        return _answer(await _runner(services).retry(bid, index))
    except batches_mod.ImageError as e:
        raise CelineError("conflict", str(e), 409) from None


@router.patch("/batches/{bid}/items/{index}")
async def keep_item(store: TheStore, cfg: TheConfig, bid: str, index: int,
                    body: BatchItemPatch) -> Batch:
    """What the item made, kept (`kept`) or let go: as the agent's keep tool does."""
    b = await _batch(store, bid)
    item = next((i for i in b["items"] if i["index"] == index), None)
    made = (item or {}).get("attachment")
    unit = life_mod.UNIT[b["kind"]]
    if item is None or item["state"] != "done" or not made:
        raise CelineError("conflict", f"Only {'an' if unit == 'image' else 'a'} {unit} "
                                      f"that was made can be kept.", 409)
    rows = await life_mod.keep(cfg, store, [made["id"]], body.kept)
    if body.kept and rows and rows[0]["life"] == MadeLife.CLEARED:
        from ..clock import zone
        from ..core.ids import when
        raise CelineError("conflict", f"The {unit} was cleared on "
                                      f"{when(rows[0]['life_at'], zone(cfg))} and cannot be "
                                      f"kept: Retry makes it again.", 409)
    return _answer(await _batch(store, bid))


@router.post("/batches/{bid}/stop")
async def stop_batch(store: TheStore, services: TheServices, bid: str) -> dict:
    await _batch(store, bid)
    return {"stopped": await _runner(services).stop(bid)}


# ---- the model files the agent asked for -----------------------------------------------

class ModelFile(Answer):
    """A model file that came by request, or a request still on its way (model_files.py)."""
    path: str = Field(description="where it is under the models folder")
    id: str = Field(description="what a batch's loras or model takes")
    kind: Literal["lora", "model"] | None
    size: int | None = Field(description="bytes")
    at: str | None = Field(description="when it came, or when it was asked for")
    preset: str | None
    preset_label: str | None
    site: str | None = Field(description="where it came from: huggingface.co, civitai.red")
    title: str | None = Field(description="the hub's title, or the Hugging Face repo")
    address: str | None = Field(description="its page")
    request: str | None
    state: ModelFileState
    display: Display
    done: int | None = Field(description="bytes downloaded, while it downloads")
    removable: bool


class ModelFileList(Answer):
    files: list[ModelFile]
    free: int | None = Field(description="bytes free on the models disk")


class Removed(Answer):
    removed: bool


def _helper_error(e: Exception) -> CelineError:
    if isinstance(e, HelperRefused):
        code = {404: "not_found", 409: "conflict"}.get(e.status, "bad_request")
        return CelineError(code, str(e), e.status if e.status in (404, 409) else 400)
    if isinstance(e, httpx.HTTPError):
        return CelineError("unavailable", f"The comfy helper did not answer "
                                          f"({type(e).__name__}).", 502)
    return CelineError("conflict", str(e), 409)


@router.get("/model-files")
async def list_model_files(services: TheServices) -> ModelFileList:
    try:
        return ModelFileList.model_validate(await files_mod.listing(services))
    except (files_mod.ImageError, httpx.HTTPError) as e:
        raise _helper_error(e) from None


@router.delete("/model-files/{path:path}")
async def remove_model_file(services: TheServices, path: str) -> Removed:
    try:
        await files_mod.remove(services, path)
    except (files_mod.ImageError, httpx.HTTPError) as e:
        raise _helper_error(e) from None
    return Removed(removed=True)


class CivitaiKey(Answer):
    """Whether the comfy helper keeps a Civitai key; the key never comes back."""
    state: KeyState
    display: Display


class CivitaiKeyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str


async def _civitai_key(services, method: str, key: str | None = None) -> CivitaiKey:
    try:
        return CivitaiKey.model_validate(await files_mod.civitai_key(services, method, key))
    except (files_mod.ImageError, httpx.HTTPError) as e:
        raise _helper_error(e) from None


@router.get("/civitai-key")
async def get_civitai_key(services: TheServices) -> CivitaiKey:
    return await _civitai_key(services, "GET")


@router.put("/civitai-key")
async def put_civitai_key(services: TheServices, body: CivitaiKeyIn) -> CivitaiKey:
    return await _civitai_key(services, "PUT", body.key)


@router.delete("/civitai-key")
async def delete_civitai_key(services: TheServices) -> CivitaiKey:
    return await _civitai_key(services, "DELETE")
