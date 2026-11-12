"""Процедурные воксельные GIF для проверки сборки без платного API."""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

W, H = 512, 288


def _fill(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], color: tuple[int, int, int]) -> None:
    draw.rectangle(box, fill=color)


def make_demo_gif(index: int, target: Path) -> Path:
    frames: list[Image.Image] = []
    for n in range(16):
        t = n / 15
        im = Image.new("RGB", (W, H), "#9bc8dc")
        d = ImageDraw.Draw(im)
        # Chunky Minecraft-like 2.5D beach; intentionally NOT an output of GPT Image.
        _fill(d, (0, 0, W, 144), (100 + int(15 * t), 169, 202))
        _fill(d, (0, 144, W, 201), (39, 119, 160))
        for r in range(8):
            y = 145 + r * 7
            offset = int(5 * math.sin(t * math.pi * 2 + r))
            _fill(d, (0, y, W - 10 + offset, y + 2), (72 + r * 2, 162 + r * 2, 187))
        d.polygon([(0, 203), (80, 190), (270, 212), (W, 193), (W, H), (0, H)], fill=(211, 193, 135))
        # shoreline shade / voxel dunes
        for j in range(9):
            x = j * 59 + 8
            y = 224 + (j % 3) * 16
            _fill(d, (x, y, x + 34, y + 6), (193, 173, 119))
        # palm tree constructed from boxes, pixel-art cuboid look
        _fill(d, (420, 80, 441, 205), (91, 72, 40))
        for k in range(4):
            y = 84 + k * 31
            _fill(d, (422, y, 439, y + 4), (116, 91, 51))
        for dx, dy, ww in [(-78, -2, 75), (-50, -23, 65), (-10, -34, 85), (27, -20, 69)]:
            _fill(d, (431 + dx, 74 + dy, 431 + dx + ww, 88 + dy), (37, 93, 47))
        # blocky wreck in shot 1-3
        if index <= 3:
            _fill(d, (55, 174, 131, 207), (105, 76, 50))
            _fill(d, (73, 151, 81, 177), (91, 68, 47))
            d.polygon([(82, 153), (123, 171), (83, 170)], fill=(214, 207, 178))
        # Robinson, moving only several pixels per animation frame.
        dx = (index - 1) * 18 + int(8 * t)
        x, y = 213 + dx, 187 - int(3 * math.sin(math.pi * t))
        _fill(d, (x - 20, y + 48, x - 3, y + 82), (70, 55, 43))
        _fill(d, (x + 7, y + 48, x + 23, y + 82), (70, 55, 43))
        _fill(d, (x - 22, y, x + 24, y + 50), (50, 91, 137))
        _fill(d, (x - 40, y + 4, x - 23, y + 45), (206, 164, 118))
        arm = int(7 * math.sin(math.pi * t)) if index in (3, 4) else 0
        _fill(d, (x + 25, y + 5 - arm, x + 40, y + 45 - arm), (206, 164, 118))
        _fill(d, (x - 19, y - 41, x + 21, y - 1), (199, 152, 107))
        _fill(d, (x - 19, y - 45, x + 21, y - 33), (61, 45, 37))
        _fill(d, (x - 15, y - 6, x + 15, y), (69, 47, 34))
        _fill(d, (x + 4, y - 22, x + 9, y - 17), (29, 31, 31))
        # corner-like block clouds
        for cx, cy in [(28, 32), (250, 45)]:
            shift = int(8 * t)
            _fill(d, (cx + shift, cy, cx + shift + 70, cy + 14), (228, 230, 211))
            _fill(d, (cx + shift + 19, cy - 10, cx + shift + 50, cy), (228, 230, 211))
        frames.append(im)
    target.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(target, save_all=True, append_images=frames[1:], duration=250, loop=0, optimize=False, disposal=2)
    return target
