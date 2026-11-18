"""A track a music batch made keeps how long it lasts (`lasts`, seconds, as ffprobe reads its
file) beside how long it took to make (`seconds`). The tracks made before this get theirs
from their files; one whose file is gone, or that ffprobe cannot read, keeps none. Without
ffprobe on the machine it changes nothing. Run again, it reads only the tracks still
without one."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

SECONDS = 60.0          # one ffprobe run, at most


def _lasts(path: Path) -> float | None:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                              "default=noprint_wrappers=1:nokey=1", str(path)],
                             capture_output=True, text=True, timeout=SECONDS)
        return round(float(out.stdout.strip()), 1)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def up(conn, db_path) -> None:
    if shutil.which("ffprobe") is None:
        return
    blobs = Path(db_path).parent / "blobs"
    changed = []
    for bid, raw in conn.execute("SELECT id, items FROM media_batches WHERE kind = 'music'").fetchall():
        items = json.loads(raw)
        read = False
        for it in items:
            sha = (it.get("attachment") or {}).get("sha256")
            if not sha or it.get("lasts") is not None:
                continue
            blob = blobs / sha[:2] / sha[2:4] / sha
            if blob.is_file() and (s := _lasts(blob)) is not None:
                it["lasts"] = s
                read = True
        if read:
            changed.append((json.dumps(items, ensure_ascii=False), bid))
    conn.execute("BEGIN")
    try:
        conn.executemany("UPDATE media_batches SET items = ? WHERE id = ?", changed)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
