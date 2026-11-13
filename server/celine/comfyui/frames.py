"""A clip's frames, sampled at even times by ffmpeg (in the app's image), so the agent sees
how a clip it made moves: `sample` gives each frame's index, its time and a JPEG of it, the
longer side at most EDGE pixels. Nothing here is stored; video_generator__look keeps what it
shows with its call."""
from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import anyio

EDGE = 768                  # px of a sampled frame's longer side, at most
MOST = 24                   # frames one look samples of all its clips together
SECONDS = 60.0              # one ffprobe or ffmpeg run, at most


class FramesError(Exception):
    """The sentence why a clip's frames could not be sampled."""


@dataclass(frozen=True)
class Frame:
    index: int              # its frame number, from 0
    seconds: float          # its time in the clip
    jpeg: bytes


def picks(total: int, n: int) -> list[int]:
    """`n` frame numbers spread evenly from the first to the last of `total`, each once."""
    if total <= 0 or n <= 0:
        return []
    if n == 1 or total == 1:
        return [0]
    return sorted({round(k * (total - 1) / (n - 1)) for k in range(n)})


async def _run(*cmd: str) -> bytes:
    if shutil.which(cmd[0]) is None:
        raise FramesError(f"{cmd[0]} is not in the app's image.")
    try:
        with anyio.fail_after(SECONDS):
            done = await anyio.run_process(list(cmd), check=False)
    except TimeoutError:
        raise FramesError(f"{cmd[0]} did not finish within {SECONDS:.0f} s.") from None
    if done.returncode != 0:
        last = (done.stderr or b"").decode(errors="replace").strip().splitlines()
        raise FramesError(f"{cmd[0]} failed: {last[-1] if last else done.returncode}")
    return done.stdout


async def audio_streams(data: bytes) -> int:
    """How many audio streams the clip `data`, an MP4, has."""
    with tempfile.TemporaryDirectory(prefix="celine-frames-") as tmp:
        clip = Path(tmp) / "clip.mp4"
        clip.write_bytes(data)
        probe = json.loads(await _run("ffprobe", "-v", "error", "-select_streams", "a",
                                      "-show_entries", "stream=index", "-of", "json", str(clip)))
        return len(probe.get("streams") or [])


async def sample(data: bytes, n: int) -> tuple[int, list[Frame]]:
    """(how many frames the clip `data`, an MP4, has; `n` of them, spread evenly from its
    first to its last)."""
    with tempfile.TemporaryDirectory(prefix="celine-frames-") as tmp:
        clip = Path(tmp) / "clip.mp4"
        clip.write_bytes(data)
        probe = json.loads(await _run(
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
            "-show_entries", "stream=nb_read_packets,avg_frame_rate", "-of", "json", str(clip)))
        try:
            stream = probe["streams"][0]
            total = int(stream["nb_read_packets"])
            num, _, den = str(stream["avg_frame_rate"]).partition("/")
            fps = float(num) / float(den or 1)
        except (KeyError, IndexError, ValueError, ZeroDivisionError):
            raise FramesError("ffprobe found no video stream in the clip.") from None
        chosen = picks(total, n)
        select = "+".join(f"eq(n\\,{i})" for i in chosen)
        await _run("ffmpeg", "-v", "error", "-i", str(clip), "-vf",
                   f"select='{select}',scale={EDGE}:{EDGE}:force_original_aspect_ratio=decrease",
                   "-fps_mode", "vfr", "-q:v", "3", str(Path(tmp) / "f%03d.jpg"))
        files = sorted(Path(tmp).glob("f*.jpg"))
        if len(files) != len(chosen):
            raise FramesError(f"ffmpeg gave {len(files)} of the {len(chosen)} frames asked for.")
        return total, [Frame(i, round(i / fps, 2) if fps > 0 else 0.0, f.read_bytes())
                       for i, f in zip(chosen, files)]
