"""Компоновка неподвижных PNG в 20-секундный MP4, с отдельным рендером сцены."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PIL import Image


class RenderError(RuntimeError):
    pass


SHOT_SECONDS = 4
SHOT_COUNT = 5


def _run(cmd: list[str]) -> None:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=480)
    except subprocess.TimeoutExpired as exc:
        raise RenderError("FFmpeg превысил лимит выполнения (480 секунд)") from exc
    if result.returncode:
        raise RenderError(f"FFmpeg: {(result.stderr or result.stdout)[-900:]}")


def _check_ffmpeg(fps: int) -> None:
    if shutil.which("ffmpeg") is None:
        raise RenderError("FFmpeg не найден в PATH")
    if fps not in (30, 60):
        raise RenderError("Допустимая частота: 30 или 60 FPS")


def render_scene_clip(
    frames: list[Path],
    output: Path,
    *,
    fps: int = 30,
    interpolate: bool = False,
    width: int = 1280,
    height: int = 720,
) -> Path:
    """Кодирует ровно 4 секунды из PNG. В regenerate рендерится только один клип."""
    _check_ffmpeg(fps)
    if len(frames) < 3:
        raise RenderError("Для сцены нужно минимум 3 кадра")
    for frame in frames:
        if not frame.is_file():
            raise RenderError(f"Отсутствует кадр: {frame.name}")
        with Image.open(frame) as im:
            if im.format != "PNG" or getattr(im, "n_frames", 1) != 1:
                raise RenderError("Ожидался одиночный PNG")
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = output.with_suffix(".frames.txt")
    duration = SHOT_SECONDS / len(frames)
    lines = []
    for frame in frames:
        if "'" in str(frame.resolve()):
            raise RenderError("Недопустимый символ в пути к кадру")
        lines.extend((f"file '{frame.resolve()}'\n", f"duration {duration:.7f}\n"))
    lines.append(f"file '{frames[-1].resolve()}'\n")
    manifest.write_text("".join(lines), encoding="utf-8")

    if interpolate:
        # Промежуточные кадры считаются в пониженном разрешении — это заметно быстрее 720p
        # и избегает многочасового рендера при 60 FPS. На выходе возвращаем 720p.
        scale_down = f"scale={min(width, 480)}:{min(height, 270)}:flags=bicubic,"
        motion = f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=obmc:me_mode=bidir,"
        scale_up = f"scale={width}:{height}:flags=lanczos,"
        filters = scale_down + motion + scale_up
    else:
        filters = f"scale={width}:{height}:flags=lanczos,"
    filters += f"tpad=stop_mode=clone:stop_duration=1,trim=duration={SHOT_SECONDS},fps={fps},setsar=1,format=yuv420p"
    _run([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(manifest),
        "-vf", filters, "-frames:v", str(SHOT_SECONDS * fps),
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", str(fps), str(output),
    ])
    return output


def concat_clips(clips: list[Path], output: Path) -> Path:
    _check_ffmpeg(30)
    if len(clips) != SHOT_COUNT or any(not clip.is_file() for clip in clips):
        raise RenderError("Для монтажа нужны все пять готовых клипов")
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = output.with_suffix(".concat.txt")
    lines = []
    for clip in clips:
        if "'" in str(clip.resolve()):
            raise RenderError("Недопустимый символ в пути к клипу")
        lines.append(f"file '{clip.resolve()}'\n")
    manifest.write_text("".join(lines), encoding="utf-8")
    _run([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(manifest),
        "-c", "copy", "-movflags", "+faststart", str(output),
    ])
    return output


def render(
    frame_groups: list[list[Path]],
    output: Path,
    *,
    fps: int = 30,
    interpolate: bool = False,
    width: int = 1280,
    height: int = 720,
) -> Path:
    if len(frame_groups) != SHOT_COUNT:
        raise RenderError("Для 20 секунд требуется ровно 5 сцен")
    clips = [
        render_scene_clip(frames, output.parent / f"clip_{i:02d}.mp4", fps=fps, interpolate=interpolate, width=width, height=height)
        for i, frames in enumerate(frame_groups, 1)
    ]
    return concat_clips(clips, output)