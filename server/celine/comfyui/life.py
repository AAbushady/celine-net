"""How long what a batch makes stays.

An image or a track a batch made (an attachment with its batch_id) is temporary: it is
cleared `generated_keep_days` days (General settings, Storage; 0: no limit) after its life
began, when it was made or let go. A kept one stays until it is let go. A cleared one keeps
its row and its batch item, marked cleared with the time (attachments.life, life_at), and
its batch says so wherever it appears (batches.view); its bytes go: its entry in the dated
folder, and its blob with the blob's smaller copies unless another attachment that is not
cleared holds the same bytes. Made again (batches.Batches.retry), it is a new attachment
with a new life.

Never cleared, whatever its age: a kept one, and one whose bytes something durable shows:
the agent's portrait, or a file an Intercom message carries (attachments.ON_INTERCOM, a
copy of its row that holds the same blob). Pages are sandboxed and read no attachment,
and a share serves the box's own files, so neither holds one.

The folders: everything made is also an entry in images/<date>/ or music/<date>/ of the
data folder (its time zone), a plain folder to browse, and a kept one in images/kept/ or
music/kept/ until it is let go. Each entry is a hard link to the blob, so the bytes are on
disk once; a plain copy where the two are on different filesystems or the filesystem takes
no links.

`clear` runs at each start beside attachments.gc and every CLEAR_EVERY seconds after
(`keep_clearing`), on the app's clock; a look that finds nothing to clear writes nothing.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anyio

from ..attachments import ON_INTERCOM, blob_path, drop_blob
from ..core.ids import iso, new_id, when
from ..core.statuses import MadeLife

log = logging.getLogger("celine.comfyui")

DAYS = "generated_keep_days"     # the General setting
CLEAR_EVERY = 3600.0             # seconds between two looks of the app's loop
SHORT = 8                        # a made file's short id: the first characters of its id
UNIT = {"images": "image", "music": "track"}

# what is due at a cutoff: temporary since before it, its bytes shown by nothing durable
_DUE = ("SELECT a.id, a.sha256, a.batch_id, a.folder_path FROM attachments a "
        "WHERE a.life = 'temporary' AND a.life_at <= ? "
        "AND a.sha256 NOT IN (SELECT portrait FROM agent WHERE portrait IS NOT NULL) "
        f"AND a.sha256 NOT IN (SELECT o.sha256 FROM attachments o WHERE o.id IN ({ON_INTERCOM}))")


def days(cfg) -> int:
    return int(cfg.r(DAYS))


def link(src: Path, dest: Path) -> None:
    """`dest` as a hard link to `src` (replacing what was there): the bytes on disk once. A
    plain copy where `src` and `dest` are on different filesystems (EXDEV) or the filesystem
    takes no links."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{new_id()[:8]}.part")
    try:
        os.link(src, tmp)
    except OSError:
        shutil.copyfile(src, tmp)
    os.replace(tmp, dest)


def dated(cfg, sha: str, kind: str, name: str) -> str:
    """Its entry in images/<date>/ or music/<date>/ (the app's zone), linked to its blob.
    -> the entry's path in the data folder."""
    from ..clock import zone
    t = datetime.now(zone(cfg))
    path = cfg.data_dir / kind / f"{t:%Y-%m-%d}" / f"{t:%H-%M-%S}_{name}"
    link(blob_path(cfg, sha), path)
    return path.relative_to(cfg.data_dir).as_posix()


def kept_entry(cfg, kind: str, aid: str, filename: str) -> Path:
    """A kept one's entry in images/kept/ or music/kept/: its short id and its file name."""
    return cfg.data_dir / kind / "kept" / f"{aid[:SHORT]}_{filename}"


def view(state: str, at: str) -> dict:
    """A life as a batch item shows it: {state, display, at}."""
    s = MadeLife(state)
    return {"state": s.value, "display": s.display, "at": at}


def lives(c: sqlite3.Connection, batch_id: str) -> dict[str, dict]:
    """The lives of what batch `batch_id` made, by attachment id."""
    return {r["id"]: view(r["life"], r["life_at"]) for r in c.execute(
        "SELECT id, life, life_at FROM attachments WHERE batch_id=? AND life IS NOT NULL",
        (batch_id,))}


def clears_at(cfg, life: dict | None) -> datetime | None:
    """When a temporary one is cleared; None for any other, or with no limit."""
    n = days(cfg)
    if not life or life["state"] != MadeLife.TEMPORARY or n <= 0:
        return None
    return datetime.fromisoformat(life["at"]) + timedelta(days=n)


def words(cfg, life: dict | None) -> str:
    """Its life as the agent reads it, a sentence: "Kept.", "Cleared on Wed 25 Nov 10:00
    unless kept.", "Cleared on Wed 25 Nov 10:00."; "" when it has none or stays."""
    from ..clock import zone
    if not life:
        return ""
    if life["state"] == MadeLife.KEPT:
        return "Kept."
    if life["state"] == MadeLife.CLEARED:
        return f"Cleared on {when(life['at'], zone(cfg))}."
    due = clears_at(cfg, life)
    return f"Cleared on {when(iso(due), zone(cfg))} unless kept." if due else ""


def cleared_line(cfg, kind: str, aid: str, at: str, what: str) -> str:
    """The sentence of a refusal for one that was cleared: `what` it cannot be, and how
    the agent gets it again."""
    from ..clock import zone
    tool = "image_generator" if kind == "images" else "music_generator"
    again = "the prompt, preset, size and seed list_images shows" if kind == "images" \
        else "the same style, lyrics and seed"
    return (f"{UNIT[kind].capitalize()} {aid[:SHORT]} was cleared on "
            f"{when(at, zone(cfg))}{what}: make it again with {tool}__generate from "
            f"{again}.")


# ---- clear -----------------------------------------------------------------------------

async def clear(cfg, store, at: datetime | None = None) -> int:
    """Clears what is due as of `at` (now): the module docstring. -> how many."""
    n = days(cfg)
    if n <= 0:
        return 0
    t = at or datetime.now(timezone.utc)
    cutoff = iso(t - timedelta(days=n))
    if await store.read(lambda c: c.execute(_DUE + " LIMIT 1", (cutoff,)).fetchone()) is None:
        return 0

    def fn(c: sqlite3.Connection) -> int:
        due = [dict(r) for r in c.execute(_DUE, (cutoff,))]
        c.executemany("UPDATE attachments SET life = 'cleared', life_at = ? WHERE id = ?",
                      [(iso(t), r["id"]) for r in due])
        for r in due:
            if r["folder_path"]:
                (cfg.data_dir / r["folder_path"]).unlink(missing_ok=True)
        for sha in {r["sha256"] for r in due}:
            if c.execute("SELECT 1 FROM attachments WHERE sha256 = ? AND "
                         "coalesce(life, '') <> 'cleared' LIMIT 1", (sha,)).fetchone() is None:
                drop_blob(cfg, sha)
        _publish(store, c, {r["batch_id"] for r in due})
        return len(due)

    cleared = await store.write(fn)
    log.info("generated files cleared", extra={"event": "comfyui.cleared", "count": cleared})
    return cleared


async def keep_clearing(cfg, store, every: float = CLEAR_EVERY) -> None:
    """The app's loop: `clear` every `every` seconds (the startup clears once before)."""
    while True:
        await anyio.sleep(every)
        try:
            await clear(cfg, store)
        except Exception:
            log.exception("clear failed", extra={"event": "comfyui.clear_failed"})


def _publish(store, c: sqlite3.Connection, batch_ids) -> None:
    from . import batches
    for bid in sorted(b for b in batch_ids if b):
        batches.publish_stored(store, c, bid)


# ---- keep ------------------------------------------------------------------------------

async def find(store, kind: str, ref: str) -> dict | str:
    """What a batch of `kind` made, by its short id (or a longer start of its id). -> {id},
    or the sentence why not."""
    unit = UNIT[kind]
    ref = (ref or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{4,32}", ref):
        a = "an" if unit == "image" else "a"
        return (f"{ref or 'An empty id'} is not {a} {unit}'s short id: use the {SHORT} "
                f"characters from its line, like f5ef648c.")

    def fn(c: sqlite3.Connection) -> list[dict]:
        return [dict(r) for r in c.execute(
            "SELECT a.id FROM attachments a JOIN media_batches mb ON mb.id = a.batch_id "
            "WHERE mb.kind = ? AND substr(a.id, 1, ?) = ? LIMIT 2", (kind, len(ref), ref))]
    rows = await store.read(fn)
    if not rows:
        return f"There is no {unit} {ref} that a batch made."
    if len(rows) > 1:
        return f"Several {unit}s start with {ref}: use all {SHORT} characters of its short id."
    return rows[0]


async def keep(cfg, store, aids: list[str], on: bool, at: datetime | None = None) -> list[dict]:
    """Keeps (`on`) or lets go of what a batch made, the attachments `aids`, in one write: a
    kept one gets its entry in the kept folder, a let go one loses it and its days begin
    again. A cleared one stays cleared. -> one row each, as it is after: {id, kind,
    filename, life, life_at, changed}."""
    stamp = iso(at or datetime.now(timezone.utc))
    want = MadeLife.KEPT if on else MadeLife.TEMPORARY

    def fn(c: sqlite3.Connection) -> list[dict]:
        out, touched = [], set()
        for aid in aids:
            r = c.execute(
                "SELECT a.id, a.filename, a.sha256, a.life, a.life_at, a.batch_id, mb.kind "
                "FROM attachments a JOIN media_batches mb ON mb.id = a.batch_id "
                "WHERE a.id = ? AND a.life IS NOT NULL", (aid,)).fetchone()
            if r is None:
                continue
            row = {k: r[k] for k in ("id", "kind", "filename", "life", "life_at")}
            if r["life"] in (MadeLife.CLEARED, want):
                out.append({**row, "changed": False})
                continue
            c.execute("UPDATE attachments SET life = ?, life_at = ? WHERE id = ?",
                      (want.value, stamp, aid))
            entry = kept_entry(cfg, r["kind"], aid, r["filename"])
            blob = blob_path(cfg, r["sha256"])
            if not on:
                entry.unlink(missing_ok=True)
            elif blob.is_file():
                link(blob, entry)
            touched.add(r["batch_id"])
            out.append({**row, "life": want.value, "life_at": stamp, "changed": True})
        _publish(store, c, touched)
        return out

    return await store.write(fn)
