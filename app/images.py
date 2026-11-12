"""OpenAI Image API: постоянная идентичность героя, keyframes и in-between кадры.

Не используем GIF и не выдаём покадровую генерацию за гарантию плавного видео.
"""
from __future__ import annotations

import base64
from contextlib import ExitStack
import io
import time
from pathlib import Path
from typing import Any, Callable

import httpx
from PIL import Image

IMAGE_SIZE = "1280x720"  # валидный для GPT Image размер: обе стороны кратны 16


class ImageGenerationError(RuntimeError):
    pass


def _post_with_retry(client: httpx.Client, url: str, **kwargs: Any) -> dict[str, Any]:
    for attempt in range(3):
        response = client.post(url, timeout=240, **kwargs)
        if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            time.sleep(2**attempt)
            continue
        if response.is_error:
            # Нельзя выводить сырой ответ, потенциально содержащий пользовательские данные.
            raise ImageGenerationError(f"OpenAI Image API: HTTP {response.status_code}; проверьте модель, лимиты и параметры")
        return response.json()
    raise ImageGenerationError("OpenAI Image API: исчерпаны попытки")


def _save_result(response: dict[str, Any], target: Path) -> Path:
    try:
        encoded = response["data"][0]["b64_json"]
        decoded = base64.b64decode(encoded, validate=True)
        # Проверяем, что получено именно неподвижное изображение, а не GIF.
        with Image.open(io.BytesIO(decoded)) as image:
            if image.format != "PNG" or getattr(image, "n_frames", 1) != 1:
                raise ImageGenerationError("Image API вернул не одиночный PNG-кадр")
            image.verify()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(decoded)
    except (KeyError, IndexError, TypeError, ValueError, OSError) as exc:
        raise ImageGenerationError("Image API не вернул валидное PNG-изображение") from exc
    return target


def build_reference(story: dict[str, Any], folder: Path, *, client: httpx.Client, api_key: str, model: str) -> Path:
    path = folder / "character_reference.png"
    prompt = (
        "Create ONE STILL cinematic master reference image for a Minecraft Machinima short. "
        "This reference defines the only protagonist, their skin, head shape, face, clothing, backpack, "
        "voxel materials, palette and lighting. Three-quarter full-body pose, readable details. "
        "No extra characters. This is a reference, not a video frame. "
        f"Global style: {story['visual_bible']} Main character: {story['character_bible']} "
        "No captions, no watermark, no HUD."
    )
    answer = _post_with_retry(
        client, "/images/generations", headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "prompt": prompt, "size": IMAGE_SIZE, "quality": "medium", "complexity": "medium", "output_format": "png", "n": 1},
    )
    return _save_result(answer, path)


def _generate_edit(
    prompt: str,
    images: list[tuple[str, Path]],
    target: Path,
    *,
    client: httpx.Client,
    api_key: str,
    model: str,
) -> Path:
    # Передаём файлы с одинаковым ключом image[]; порядок соответствует именам A/B/C в промпте.
    with ExitStack() as stack:
        files = [("image[]", (name, stack.enter_context(path.open("rb")), "image/png")) for name, path in images]
        answer = _post_with_retry(
            client, "/images/edits", headers={"Authorization": f"Bearer {api_key}"},
            data={"model": model, "prompt": prompt, "size": IMAGE_SIZE, "quality": "medium", "complexity": "medium", "output_format": "png", "n": "1"},
            files=files,
        )
    return _save_result(answer, target)


def make_frame(
    story: dict[str, Any],
    shot: dict[str, str],
    shot_index: int,
    frame_index: int,
    frame_count: int,
    reference: Path,
    folder: Path,
    *,
    client: httpx.Client,
    api_key: str,
    model: str,
    previous_frame: Path | None = None,
    right_frame: Path | None = None,
    inbetween_alpha: float | None = None,
    boundary_frame: Path | None = None,
    instruction: str = "",
) -> Path:
    """Рисует отдельный PNG на основе мастер-референса и до двух якорей.

    - previous_frame: предыдущий keyframe или левый anchor;
    - right_frame: правый anchor для промежуточного кадра;
    - boundary_frame: последний кадр предшествующей сцены для стилевого соответствия.
    """
    target = folder / f"frame_{frame_index:03d}.png"
    beat = (frame_index - 1) / max(frame_count - 1, 1)
    files = [("A_master_reference.png", reference)]
    if previous_frame is not None:
        files.append(("B_left_anchor.png", previous_frame))
    elif boundary_frame is not None:
        files.append(("B_previous_shot.png", boundary_frame))
    if right_frame is not None:
        files.append(("C_right_anchor.png", right_frame))

    pose_hint = ("opening pose" if beat < 0.05 else "final pose" if beat > 0.95 else "intermediate action pose")
    continuity = (
        "Image B is the left keyframe and Image C is the right keyframe. "
        f"Render a pose at {round(100 * (inbetween_alpha or 0.5))}% of the progression from B to C. "
        "Render the character pose and scene state at that exact intermediate point, preserving geometry. "
        "Avoid overlays, ghosting, duplicate figures, frame blends or motion smears."
        if right_frame is not None
        else (
            "Image B is the earlier keyframe. Continue the exact same character and scene while moving toward "
            "the described action pose. Preserve framing and object topology except for the requested movement."
            if previous_frame is not None
            else (
                "Image B is from the preceding shot: use it ONLY for protagonist identity and color continuity; "
                "the new camera composition must match this scene description."
                if boundary_frame is not None
                else "Establish the opening composition of this new shot, preserving the master identity."
            )
        )
    )
    prompt = (
        "Produce ONE STATIC PNG FRAME (not a GIF, not a storyboard grid) for an images-to-video Minecraft Machinima film. "
        "There is exactly ONE protagonist. Image A is the immutable character/style reference, NOT the shot composition. "
        f"Shot {shot_index}/5; frame {frame_index}/{frame_count}; normalized story progress {beat:.3f}; target is the {pose_hint}. "
        f"Global visual bible: {story['visual_bible']}. Character bible: {story['character_bible']}. "
        f"Scene: {shot['title']}; place: {shot['location']}; action: {shot['action']}; "
        f"camera: {shot['camera']}; intended motion: {shot['motion']}; mood: {shot['mood']}. "
        f"{continuity} "
        "No camera jump inside shot, no sudden changes to wardrobe, time of day or environment, "
        "no HUD, no text, no borders, no new objects except the planned action. "
        + (f"Director's revision note (must remain consistent with the visual bible): {instruction}." if instruction else "")
    )
    return _generate_edit(prompt, files, target, client=client, api_key=api_key, model=model)


def make_shot_frames(
    story: dict[str, Any],
    shot: dict[str, str],
    shot_index: int,
    frame_count: int,
    reference: Path,
    folder: Path,
    *,
    client: httpx.Client,
    api_key: str,
    model: str,
    previous_shot_frame: Path | None = None,
    instruction: str = "",
    on_frame: Callable[[int, int], None] | None = None,
) -> list[Path]:
    """Сначала 3 ключевых позы, потом промежуточные, привязанные к двум ближайшим позам."""
    if frame_count < 3:
        raise ValueError("Для схемы keyframe/in-between требуется минимум 3 изображения")
    folder.mkdir(parents=True, exist_ok=True)
    frames: dict[int, Path] = {}
    middle = frame_count // 2
    anchors = [1, middle + 1, frame_count]
    for a_index, frame_number in enumerate(anchors):
        previous_anchor = frames[anchors[a_index - 1]] if a_index else None
        frames[frame_number] = make_frame(
            story, shot, shot_index, frame_number, frame_count, reference, folder,
            client=client, api_key=api_key, model=model,
            previous_frame=previous_anchor,
            boundary_frame=previous_shot_frame if a_index == 0 else None,
            instruction=instruction,
        )
        if on_frame:
            on_frame(len(frames), frame_count)

    for frame_number in range(1, frame_count + 1):
        if frame_number in frames:
            continue
        left = max(a for a in anchors if a < frame_number)
        right = min(a for a in anchors if a > frame_number)
        frames[frame_number] = make_frame(
            story, shot, shot_index, frame_number, frame_count, reference, folder,
            client=client, api_key=api_key, model=model,
            previous_frame=frames[left], right_frame=frames[right],
            inbetween_alpha=(frame_number - left) / (right - left), instruction=instruction,
        )
        if on_frame:
            on_frame(len(frames), frame_count)
    return [frames[number] for number in range(1, frame_count + 1)]