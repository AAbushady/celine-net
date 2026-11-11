"""What a batch made gets its life (comfyui/life.py): `life` is temporary, kept or cleared
(core/statuses.py MadeLife), `life_at` the time it took that state, and `folder_path` its
entry in images/<date>/ or music/<date>/, relative to the data folder, which a clear
removes.

Every attachment a batch made starts temporary at its own created_at and is not kept. Its
entry in the dated folder is looked for in the folder of its day in the app's zone (General
settings, read as 0030 reads it) and the day after (the entry was written a moment after
the row), as a file whose name ends in `_<its file name>` and whose bytes hash to its blob.
A found entry is recorded and becomes a hard link to its blob, so its bytes are on disk
once; one that is not found stays where it is. Run again, it writes nothing new."""
from __future__ import annotations

import hashlib
import os
import tomllib
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LIFE = "life TEXT CHECK (life IN ('temporary','kept','cleared'))"


def _zone(db_path: Path) -> ZoneInfo:
    try:
        general = tomllib.loads((db_path.parent / "settings" / "general.toml")
                                .read_text(encoding="utf-8"))
        return ZoneInfo(str(general.get("timezone") or "UTC"))
    except (OSError, tomllib.TOMLDecodeError, ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _entry(data: Path, kind: str, created: str, tz: ZoneInfo, filename: str, sha: str,
           taken: set[Path]) -> Path | None:
    """Its file in the dated folder, or None."""
    day = datetime.fromisoformat(created).astimezone(tz).date()
    for d in (day, day + timedelta(days=1)):
        folder = data / kind / d.isoformat()
        if not folder.is_dir():
            continue
        for p in sorted(folder.iterdir()):
            if p not in taken and p.is_file() and p.name.endswith(f"_{filename}") \
                    and _sha(p) == sha:
                return p
    return None


def _link(blob: Path, entry: Path) -> None:
    """The entry as a hard link to the blob; a copy stays where links are refused."""
    if not blob.is_file() or os.path.samefile(blob, entry):
        return
    tmp = entry.with_name(f".{entry.name}.link")
    tmp.unlink(missing_ok=True)
    try:
        os.link(blob, tmp)
    except OSError:
        return
    os.replace(tmp, entry)


def up(conn, db_path) -> None:
    db_path = Path(db_path)
    data = db_path.parent
    tz = _zone(db_path)
    conn.execute("BEGIN")
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(attachments)")}
        if "life" not in cols:
            conn.execute(f"ALTER TABLE attachments ADD COLUMN {LIFE}")
        for name in ("life_at", "folder_path"):
            if name not in cols:
                conn.execute(f"ALTER TABLE attachments ADD COLUMN {name} TEXT")
        conn.execute("UPDATE attachments SET life = 'temporary', life_at = created_at "
                     "WHERE batch_id IS NOT NULL AND life IS NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS attachments_life "
                     "ON attachments(life, life_at)")
        rows = conn.execute(
            "SELECT a.id, a.sha256, a.filename, a.created_at, mb.kind FROM attachments a "
            "JOIN media_batches mb ON mb.id = a.batch_id WHERE a.folder_path IS NULL "
            "ORDER BY a.created_at, a.id").fetchall()
        taken = {data / r[0] for r in conn.execute(
            "SELECT folder_path FROM attachments WHERE folder_path IS NOT NULL")}
        for aid, sha, filename, created, kind in rows:
            entry = _entry(data, kind, created, tz, filename, sha, taken)
            if entry is None:
                continue
            taken.add(entry)
            _link(data / "blobs" / sha[:2] / sha[2:4] / sha, entry)
            conn.execute("UPDATE attachments SET folder_path = ? WHERE id = ?",
                         (entry.relative_to(data).as_posix(), aid))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
