"""Attachments over HTTP: upload a draft, drop a draft, serve a stored file."""
from __future__ import annotations

from pathlib import Path

import anyio
from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import FileResponse

from .. import attachments, box_files
from ..clock import zone
from ..core.ids import when
from ..core.statuses import MadeLife
from ..errors import CelineError, NotFound
from ..core.route_params import TheConfig, TheStore

router = APIRouter(prefix="/api")


@router.post("/attachments")
async def upload_attachment(store: TheStore, cfg: TheConfig, file: UploadFile = File(...)) -> dict:
    row = await attachments.save(cfg, store, file)
    box_files.copy_later(cfg, attachments.blob_path(cfg, row["sha256"]), row["filename"], store,
                         row["id"])
    return row


@router.delete("/attachments/{aid}")
async def delete_attachment(store: TheStore, aid: str) -> dict:
    return {"removed": await attachments.delete_draft(store, aid)}


# the browser may render these, so nothing uploaded can run on this origin: only raster
# images and audio are shown inline, the rest is a download, and a sandbox covers both
SAFE_FILE_HEADERS = {"X-Content-Type-Options": "nosniff",
                     "Content-Security-Policy": "default-src 'none'; sandbox"}


def shown_inline(media_type: str) -> bool:
    return (media_type.startswith("image/") and media_type != "image/svg+xml") or media_type.startswith("audio/")


@router.get("/attachments/{aid}/file")
async def attachment_file(store: TheStore, cfg: TheConfig, aid: str, download: bool = False,
                          w: int | None = Query(None, ge=1)):
    """The file under its own name and type; `download` saves even an image. `w`: a
    picture as wide as the first of attachments.WIDTHS that is at least `w` (WebP),
    the file itself when it is not wider. A file a batch made that was cleared
    (comfyui/life.py) answers 404 with when."""
    row = await attachments.get(store, aid)
    if row and row["life"] == MadeLife.CLEARED:
        what = "image" if row["kind"] == "image" else "track"
        raise CelineError("not_found", f"The {what} was cleared on "
                                       f"{when(row['life_at'], zone(cfg))}.", 404)
    path = attachments.blob_path(cfg, row["sha256"]) if row else None
    if not row or not path.exists():
        raise NotFound("attachment")
    media, name = row["media_type"], row["filename"]
    if w is not None and row["kind"] == "image" and shown_inline(media) and not download:
        small = await anyio.to_thread.run_sync(attachments.resized, cfg, row["sha256"],
                                               attachments.snap(w))
        if small != path:
            path, media, name = small, "image/webp", f"{Path(name).stem}.webp"
    inline = shown_inline(media) and not download
    return FileResponse(path, media_type=media, filename=name,
                        content_disposition_type="inline" if inline else "attachment",
                        headers={**SAFE_FILE_HEADERS,
                                 "Cache-Control": "private, max-age=604800, immutable"})
