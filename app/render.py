"""Сборка строго 20s / 30-60fps MP4 из пяти GIF при помощи FFmpeg."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PIL import Image


class RenderError(RuntimeError):
    pass


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=480)
    if result.returncode:
        raise RenderError(f"FFmpeg: {(result.stderr or result.stdout)[-900:]}")


def render(gifs: list[Path], output: Path, *, fps: int = 30, interpolate: bool = True, width: int = 1280, height: int = 720) -> Path:
    if shutil.which("ffmpeg") is None:
        raise RenderError("FFmpeg не найден в PATH")
    if len(gifs) != 5:
        raise RenderError("Для 20 секунд требуется ровно 5 сцен")
    if fps not in (30, 60):
        raise RenderError("Допустимая частота: 30 или 60 FPS")
    clips: list[Path] = []
    for i, gif in enumerate(gifs, start=1):
        if not gif.is_file():
            raise RenderError(f"Отсутствует GIF сцены {i}")
        with Image.open(gif) as im:
            if im.n_frames < 2:
                raise RenderError(f"GIF сцены {i} не анимирован")
        clip = output.parent / f"clip_{i:02d}.mp4"
        # minterpolate interpolates real motion estimates; fps alone only duplicates frames.
        interpolation = f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=obmc:me_mode=bidir," if interpolate else f"fps={fps},"
        filters = (
            f"format=yuv420p,{interpolation}"
            f"tpad=stop_mode=clone:stop_duration=1,trim=duration=4,fps={fps},"
            f"scale={width}:{height}:flags=lanczos,setsar=1,format=yuv420p"
        )
        _run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(gif), "-vf", filters,
              "-frames:v", str(4 * fps), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
              "-pix_fmt", "yuv420p", "-r", str(fps), str(clip)])
        clips.append(clip)
    # concat демультиплексор сохраняет точное число кадров: 5 * 4 * fps.
    manifest = output.parent / "concat.txt"
    manifest.write_text("".join(f"file '{clip.name}'\n" for clip in clips), encoding="utf-8")
    _run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
          "-i", str(manifest), "-c", "copy", "-movflags", "+faststart", str(output)])
    return output
