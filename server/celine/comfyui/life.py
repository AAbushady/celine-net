"""How long what a batch makes stays.

An image or a track a batch made (an attachment with its batch_id) is temporary: it is
cleared `generated_keep_days` days (General settings, Storage; 0: no limit) after its life
began, when it was made or let go. A kept one stays until it is let go. A cleared one keeps
its row and its batch item, marked cleared with the time (attachments.life, life_at), and
its batch says so wherever it appears (batches.view); its bytes go: its entry in the dated
folder, and its blob with the blob's smaller copies unless another attachment that is not
cleared holds the same bytes. The rows are marked and committed first, the files go after
(`sweep`), so a failed write never leaves a row that names bytes that are gone. Made again
(batches.Batches.retry), it is a new attachment with a new life.

Never cleared, whatever its age: a kept one, and one whose bytes something durable shows:
the agent's portrait, or a file an Intercom message carries (attachments.ON_INTERCOM, a
copy of its row that holds the same blob). Pages are sandboxed and read no attachment,
and a share serves the box's own files, so neither holds one.

The folders: everything made is also an entry in images/<date>/ or music/<date>/ of the
data folder (its time zone), a plain folder to browse, and a kept one in images/kept/ or
music/kept/ until it is let go. Each entry is a hard link to the blob, so the bytes are on
disk once; a plain copy where the two are on different filesystems or the filesystem takes
no links.

The cleanup look, at each start and every CLEAR_EVERY seconds (`keep_clearing`), on the
app's clock: attachments.gc, then `clear`, then `sweep`, which removes the files no row
holds any more. A row goes without a word to this module: a conversation purged or a message
deleted takes its attachments with it (conversations/store.py `_drop`, and the database's
ON DELETE CASCADE), so the sweep looks at the files, not at the deletes. It removes:
- the dated entry a cleared row still names, and a blob whose every attachment is cleared
  (not the agent's portrait), with its smaller copies;
- a file the app wrote under images/ or music/ that no row holds, once it is MARGIN old (a
  row may still be on its way): a dated entry `HH-MM-SS_<name>` in a `YYYY-MM-DD` folder
  that no attachment that is not cleared names, a kept entry `<short id>_<name>` in kept/
  whose attachment is not kept, and `link`'s half-placed `.<name>.<8 hex>.part`. Any other
  file in those folders stays;
- an empty dated folder two days past its date.
A file that cannot be removed is tried again at the next look. A look that finds nothing
writes nothing. The blob of a deleted attachment is attachments.gc's (KEEP after it was
stored).
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

from ..attachments import ON_INTERCOM, blob_path, drop_blob, gc
from ..core.ids import iso, new_id, when
from ..core.statuses import MadeLife

log = logging.getLogger("celine.comfyui")

DAYS = "generated_keep_days"     # the General setting
CLEAR_EVERY = 3600.0             # seconds between two looks of the app's loop
MARGIN = timedelta(hours=1)      # an app's file no row holds is left alone this long
SHORT = 8                        # a made file's short id: the first characters of its id
UNIT = {"images": "image", "music": "track"}
KINDS = tuple(UNIT)

# the names the app writes under images/ and music/ (dated, kept, link's file on its way)
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_DATED = re.compile(r"\d{2}-\d{2}-\d{2}_.+")
_KEPT = re.compile(r"([0-9a-f]{8})_(.+)")
_PART = re.compile(r"\..+\.[0-9a-f]{8}\.part")

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


# ---- clear and sweep ---------------------------------------------------------------

async def clear(cfg, store, at: datetime | None = None) -> int:
    """Marks what is due as of `at` (now) cleared, commits, then `sweep`s the files (the
    module docstring). -> how many were cleared."""
    n = days(cfg)
    t = at or datetime.now(timezone.utc)
    cleared = 0
    cutoff = iso(t - timedelta(days=n))
    if n > 0 and await store.read(
            lambda c: c.execute(_DUE + " LIMIT 1", (cutoff,)).fetchone()) is not None:
        def fn(c: sqlite3.Connection) -> int:
            due = [dict(r) for r in c.execute(_DUE, (cutoff,))]
            c.executemany("UPDATE attachments SET life = 'cleared', life_at = ? WHERE id = ?",
                          [(iso(t), r["id"]) for r in due])
            _publish(store, c, {r["batch_id"] for r in due})
            return len(due)
        cleared = await store.write(fn)
        log.info("generated files cleared", extra={"event": "comfyui.cleared",
                                                   "count": cleared})
    await sweep(cfg, store, t)
    return cleared


async def keep_clearing(cfg, store, every: float = CLEAR_EVERY) -> None:
    """The app's loop: the cleanup look every `every` seconds (the startup looks once
    before): attachments.gc, then `clear`."""
    while True:
        await anyio.sleep(every)
        try:
            await gc(cfg, store)
            await clear(cfg, store)
        except Exception:
            log.exception("cleanup failed", extra={"event": "comfyui.clear_failed"})


def _publish(store, c: sqlite3.Connection, batch_ids) -> None:
    from . import batches
    for bid in sorted(b for b in batch_ids if b):
        batches.publish_stored(store, c, bid)


# every attachment of a blob is cleared, and it is not the agent's portrait
_LOOSE = ("SELECT DISTINCT a.sha256 FROM attachments a WHERE a.life = 'cleared' "
          "AND NOT EXISTS (SELECT 1 FROM attachments o WHERE o.sha256 = a.sha256 "
          "AND coalesce(o.life, '') <> 'cleared') "
          "AND a.sha256 NOT IN (SELECT portrait FROM agent WHERE portrait IS NOT NULL)")


def _held(c: sqlite3.Connection) -> dict:
    """What the rows hold now: `dated`, the entries of files that are not cleared; `kept`,
    the kept ones as (kind, short id, file name); `named`, (id, entry) of cleared rows that
    still name one; `blobs`, the blobs only cleared rows hold."""
    return {
        "dated": {r[0] for r in c.execute(
            "SELECT folder_path FROM attachments WHERE folder_path IS NOT NULL "
            "AND coalesce(life, '') <> 'cleared'")},
        "kept": {(r[0], r[1][:SHORT], r[2]) for r in c.execute(
            "SELECT mb.kind, a.id, a.filename FROM attachments a JOIN media_batches mb "
            "ON mb.id = a.batch_id WHERE a.life = 'kept'")},
        "named": [(r[0], r[1]) for r in c.execute(
            "SELECT id, folder_path FROM attachments WHERE life = 'cleared' "
            "AND folder_path IS NOT NULL")],
        "blobs": [r[0] for r in c.execute(_LOOSE)],
    }


def _shaped(cfg, before: float) -> list[tuple[str, tuple[str, str, str] | None]]:
    """The files under images/ and music/ named as the app names them and last changed
    before `before` (a link counts as a change): (their path in the data folder, (kind,
    short id, file name) for a kept one)."""
    out: list[tuple[str, tuple[str, str, str] | None]] = []
    for kind in KINDS:
        base = cfg.data_dir / kind
        if not base.is_dir():
            continue
        for d in base.iterdir():
            kept = d.name == "kept"
            if not d.is_dir() or not (kept or _DAY.fullmatch(d.name)):
                continue
            for f in d.iterdir():
                m = _KEPT.fullmatch(f.name) if kept else _DATED.fullmatch(f.name)
                if not (m or _PART.fullmatch(f.name)) or not f.is_file():
                    continue
                try:
                    st = f.stat()
                except OSError:
                    continue
                if max(st.st_mtime, st.st_ctime) < before:
                    out.append((f.relative_to(cfg.data_dir).as_posix(),
                                (kind, m[1], m[2]) if kept and m else None))
    return out


def _loose(held: dict, shaped) -> list[str]:
    return [rel for rel, key in shaped
            if (key not in held["kept"] if key else rel not in held["dated"])]


def _on_disk(cfg, sha: str) -> bool:
    path = blob_path(cfg, sha)
    return path.exists() or any(path.parent.glob(f"{sha}.w*.webp"))


def _unlink(path: Path) -> bool:
    """The file off the disk; False when it is still there."""
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError as e:
        log.warning("file not removed", extra={"event": "comfyui.not_removed",
                                               "path": str(path), "error": str(e)})
        return False


async def sweep(cfg, store, at: datetime | None = None) -> int:
    """Removes the files no row holds any more, as of `at` (now): the module docstring.
    The rows are read again in the writer, so a file a row takes meanwhile stays.
    -> how many files went."""
    t = at or datetime.now(timezone.utc)
    shaped = await anyio.to_thread.run_sync(_shaped, cfg, (t - MARGIN).timestamp())
    held = await store.read(_held)
    gone = 0
    if _loose(held, shaped) or held["named"] or any(_on_disk(cfg, s) for s in held["blobs"]):
        def fn(c: sqlite3.Connection) -> int:
            now_held = _held(c)
            n = 0
            for rel in _loose(now_held, shaped):
                path = cfg.data_dir / rel
                if path.exists() and _unlink(path):
                    n += 1
            for aid, rel in now_held["named"]:
                path = cfg.data_dir / rel
                there = path.exists()
                if _unlink(path):
                    n += int(there)
                    c.execute("UPDATE attachments SET folder_path = NULL WHERE id = ?",
                              (aid,))
            for sha in now_held["blobs"]:
                if _on_disk(cfg, sha):
                    try:
                        drop_blob(cfg, sha)
                        n += 1
                    except OSError as e:
                        log.warning("blob not removed", extra={
                            "event": "comfyui.not_removed", "sha": sha, "error": str(e)})
            return n
        gone = await store.write(fn)
        if gone:
            log.info("generated files removed", extra={"event": "comfyui.swept",
                                                       "count": gone})
    await anyio.to_thread.run_sync(_drop_days, cfg, t)
    return gone


def _drop_days(cfg, t: datetime) -> None:
    """The empty dated folders two days past their date (the app's zone): no entry lands
    there any more."""
    from ..clock import zone
    last = (t.astimezone(zone(cfg)).date() - timedelta(days=2)).isoformat()
    for kind in KINDS:
        base = cfg.data_dir / kind
        if not base.is_dir():
            continue
        for d in base.iterdir():
            if d.is_dir() and _DAY.fullmatch(d.name) and d.name <= last \
                    and not any(d.iterdir()):
                try:
                    d.rmdir()
                except OSError:
                    pass                # a file came: it stays


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
    """Keeps (`on`) or lets go of what a batch made, the attachments `aids`, in one write;
    after it, a kept one gets its entry in the kept folder and a let go one loses it (one
    that could not be removed goes at the next look's sweep). A let go one's days begin
    again; a cleared one stays cleared. -> one row each, as it is after: {id, kind,
    filename, life, life_at, changed}."""
    stamp = iso(at or datetime.now(timezone.utc))
    want = MadeLife.KEPT if on else MadeLife.TEMPORARY
    entries: list[tuple[Path, Path]] = []      # (its kept entry, its blob), after the write

    def fn(c: sqlite3.Connection) -> list[dict]:
        entries.clear()
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
            entries.append((kept_entry(cfg, r["kind"], aid, r["filename"]),
                            blob_path(cfg, r["sha256"])))
            touched.add(r["batch_id"])
            out.append({**row, "life": want.value, "life_at": stamp, "changed": True})
        _publish(store, c, touched)
        return out

    out = await store.write(fn)
    for entry, blob in entries:
        if not on:
            _unlink(entry)
            continue
        try:
            if blob.is_file():
                link(blob, entry)
        except OSError as e:
            log.warning("kept entry not made", extra={"event": "comfyui.not_kept",
                                                      "path": str(entry), "error": str(e)})
    return out
