"""Images and music over HTTP, the same batches its tools make (batches.py):

- GET /api/generation-presets?kind=images|music: every preset of the kind as the UI
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
"""

from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from ..core.dto import Answer, Display
from ..core.statuses import MadeLife
from ..errors import CelineError, NotFound
from . import batches as batches_mod, life as life_mod, presets as presets_mod
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
    life: Life | None = None


class SourceImage(Answer):
    """An image an edit starts from: an attachment, or the agent's portrait (id portrait)."""
    id: str
    sha256: str
    filename: str
    media_type: str


class BatchItem(Answer):
    """One slot of a batch: the request and what became of it."""
    index: int
    state: Literal["waiting", "running", "done", "failed", "stopped"]
    seed: int
    preset: str | None = None
    provider: str | None = None
    attachment: MadeFile | None = None
    seconds: float | None = None
    error: str | None = Field(default=None, description="why it failed")
    note: str | None = Field(default=None, description="what a running item does now")
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


class Batch(Answer):
    """A batch of images or tracks (batches.view)."""
    id: str
    kind: Literal["images", "music"]
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


BatchIn = Annotated[ImagesIn | MusicIn, Field(discriminator="kind")]
KINDS = ("images", "music")


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise CelineError("bad_request", "kind is images or music.", 400)
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
        raise CelineError("conflict", f"Only a {unit} that was made can be kept.", 409)
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
