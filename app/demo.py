"""Процедурные воксельные кадры и GIF для проверки сборки без платного API."""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

W, H = 512, 288


def _fill(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], color: tuple[int, int, int]) -> None:
    draw.rectangle(box, fill=color)


def _draw_demo_frame(index: int, t: float, size: tuple[int, int] = (W, H), variant: int = 0) -> Image.Image:
    w, h = size
    sx, sy = w / W, h / H
    def scale(box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        x1, y1, x2, y2 = box
        return int(x1 * sx), int(y1 * sy), int(x2 * sx), int(y2 * sy)

    im = Image.new("RGB", (w, h), "#9bc8dc")
    d = ImageDraw.Draw(im)
    _fill(d, scale((0, 0, W, 144)), (100 + int(15 * t) + min(variant * 6, 30), 169, 202 - min(variant * 5, 30)))
    _fill(d, scale((0, 144, W, 201)), (39, 119, 160))
    for r in range(8):
        y = 145 + r * 7
        offset = int(5 * math.sin(t * math.pi * 2 + r))
        _fill(d, scale((0, y, W - 10 + offset, y + 2)), (72 + r * 2, 162 + r * 2, 187))
    d.polygon([(int(0 * sx), int(203 * sy)), (int(80 * sx), int(190 * sy)), (int(270 * sx), int(212 * sy)), (int(W * sx), int(193 * sy)), (int(W * sx), h), (0, h)], fill=(211, 193, 135))
    for j in range(9):
        x = j * 59 + 8
        y = 224 + (j % 3) * 16
        _fill(d, scale((x, y, x + 34, y + 6)), (193, 173, 119))
    _fill(d, scale((420, 80, 441, 205)), (91, 72, 40))
    for k in range(4):
        y = 84 + k * 31
        _fill(d, scale((422, y, 439, y + 4)), (116, 91, 51))
    for dx, dy, ww in [(-78, -2, 75), (-50, -23, 65), (-10, -34, 85), (27, -20, 69)]:
        _fill(d, scale((431 + dx, 74 + dy, 431 + dx + ww, 88 + dy)), (37, 93, 47))
    if index <= 3:
        _fill(d, scale((55, 174, 131, 207)), (105, 76, 50))
        _fill(d, scale((73, 151, 81, 177)), (91, 68, 47))
        d.polygon([(int(82 * sx), int(153 * sy)), (int(123 * sx), int(171 * sy)), (int(83 * sx), int(170 * sy))], fill=(214, 207, 178))
    dx = (index - 1) * 18 + int(10 * t) + min(variant * 7, 35)
    x, y = 213 + dx, 187 - int(3 * math.sin(math.pi * t))
    _fill(d, scale((x - 20, y + 48, x - 3, y + 82)), (70, 55, 43))
    _fill(d, scale((x + 7, y + 48, x + 23, y + 82)), (70, 55, 43))
    _fill(d, scale((x - 22, y, x + 24, y + 50)), (50, 91, 137))
    _fill(d, scale((x - 40, y + 4, x - 23, y + 45)), (206, 164, 118))
    arm = int(7 * math.sin(math.pi * t)) if index in (3, 4) else 0
    _fill(d, scale((x + 25, y + 5 - arm, x + 40, y + 45 - arm)), (206, 164, 118))
    _fill(d, scale((x - 19, y - 41, x + 21, y - 1)), (199, 152, 107))
    _fill(d, scale((x - 19, y - 45, x + 21, y - 33)), (61, 45, 37))
    _fill(d, scale((x - 15, y - 6, x + 15, y)), (69, 47, 34))
    _fill(d, scale((x + 4, y - 22, x + 9, y - 17)), (29, 31, 31))
    for cx, cy in [(28, 32), (250, 45)]:
        shift = int(8 * t)
        _fill(d, scale((cx + shift, cy, cx + shift + 70, cy + 14)), (228, 230, 211))
        _fill(d, scale((cx + shift + 19, cy - 10, cx + shift + 50, cy)), (228, 230, 211))
    return im


def make_demo_frames(index: int, folder: Path, frame_count: int = 6, size: tuple[int, int] = (1024, 576), variant: int = 0) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    frames: list[Path] = []
    for n in range(frame_count):
        t = n / max(frame_count - 1, 1)
        path = folder / f"frame_{n + 1:03d}.png"
        _draw_demo_frame(index, t, size=size, variant=variant).save(path)
        frames.append(path)
    return frames

