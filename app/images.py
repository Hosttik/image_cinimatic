"""Обёртка REST для GIF и референса GPT-Image-2.6 Sunburst."""
from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any

import httpx


def _post_with_retry(client: httpx.Client, url: str, **kwargs: Any) -> dict[str, Any]:
    for attempt in range(3):
        response = client.post(url, timeout=240, **kwargs)
        if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            time.sleep(2**attempt)
            continue
        if response.is_error:
            # Полный API-ответ и API-ключ никогда не отправляются в браузер.
            raise RuntimeError(f"OpenAI Image API: HTTP {response.status_code}; проверьте права модели, лимиты и параметры")
        return response.json()
    raise RuntimeError("OpenAI Image API: исчерпаны попытки")


def _save_result(response: dict[str, Any], target: Path) -> None:
    try:
        encoded = response["data"][0]["b64_json"]
        target.write_bytes(base64.b64decode(encoded, validate=True))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError("Image API не вернул base64 изображение") from exc


def build_reference(story: dict[str, Any], folder: Path, *, client: httpx.Client, api_key: str, model: str) -> Path:
    path = folder / "character_reference.png"
    prompt = (
        "Single still master style and character reference for all subsequent shots, "
        "16:9 landscape full-body cinematic Minecraft machinima key art. "
        f"Style: {story['visual_bible']} Protagonist: {story['character_bible']} "
        "A readable three-quarter full-body view of Robinson on a tropical voxel island, "
        "ocean in background, clear consistent costume/colors, no text, no HUD."
    )
    answer = _post_with_retry(
        client, "/images/generations", headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "prompt": prompt, "size": "1024x576", "quality": "medium", "complexity": "medium", "output_format": "png", "n": 1},
    )
    _save_result(answer, path)
    return path


def make_gif(story: dict[str, Any], shot: dict[str, str], index: int, reference: Path, folder: Path, *, client: httpx.Client, api_key: str, model: str) -> Path:
    """16 кадров × 250 ms = 4 s; GIF output may require preview access."""
    target = folder / f"shot_{index:02d}.gif"
    prompt = (
        "Create one ANIMATED GIF of 16 chronologically ordered frames showing 4 seconds "
        "of one continuous Minecraft machinima shot. No cuts, no montages, no scene changes, "
        "no loops, no titles, no HUD. Treat the provided image as the STRICT character "
        "and visual-style reference; preserve face, costume, voxel proportions and palette. "
        "Use very small incremental changes per frame suitable for optical-flow interpolation; "
        "avoid teleportation, spinning, rapid motion, object morphing and camera jumps. "
        f"Style: {story['visual_bible']} Character: {story['character_bible']} "
        f"Shot {index} of 5: {shot['title']}. Location: {shot['location']}. "
        f"Action: {shot['action']}. Camera: {shot['camera']}. "
        f"Motion: {shot['motion']}. Mood: {shot['mood']}."
    )
    with reference.open("rb") as ref:
        answer = _post_with_retry(
            client, "/images/edits", headers={"Authorization": f"Bearer {api_key}"},
            data={"model": model, "prompt": prompt, "size": "512x288", "quality": "medium", "complexity": "medium", "output_format": "gif", "frame_count": "16", "frame_duration_ms": "250", "n": "1"},
            files={"image": ("character_reference.png", ref, "image/png")},
        )
    _save_result(answer, target)
    return target
