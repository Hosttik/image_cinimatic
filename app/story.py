"""Генерация раскадровки и планов кадров для режима images -> video."""
from __future__ import annotations

import json
from typing import Any

import httpx

SHOTS = 5
DURATION_SECONDS = 20
SHOT_DURATION_SECONDS = DURATION_SECONDS // SHOTS

STORY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "visual_bible": {"type": "string"},
        "character_bible": {"type": "string"},
        "shots": {
            "type": "array",
            "minItems": SHOTS,
            "maxItems": SHOTS,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "location": {"type": "string"},
                    "action": {"type": "string"},
                    "camera": {"type": "string"},
                    "motion": {"type": "string"},
                    "mood": {"type": "string"},
                },
                "required": ["title", "location", "action", "camera", "motion", "mood"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "visual_bible", "character_bible", "shots"],
    "additionalProperties": False,
}


class StoryboardError(RuntimeError):
    pass


def demo_storyboard(prompt: str) -> dict[str, Any]:
    return validate_storyboard(
        {
            "title": "Робинзон: первый день",
            "prompt": prompt,
            "duration_seconds": DURATION_SECONDS,
            "shot_duration_seconds": SHOT_DURATION_SECONDS,
            "visual_bible": (
                "Minecraft machinima, unmistakable voxel world, blocky textures, square low-poly silhouettes, "
                "cinematic composition, no HUD, no text, no logos, warm tropical light, consistent colors and materials."
            ),
            "character_bible": (
                "Robinson: same blocky adult survivor in every shot, square head, short dark beard, weathered blue shirt, "
                "brown pants and a simple brown backpack."
            ),
            "shots": [
                {"title": "После кораблекрушения", "location": "берег, рассвет", "action": "Робинзон лежит у кромки воды, волна откатывается", "camera": "широкий плавный наезд", "motion": "медленное пробуждение, вода шевелится", "mood": "тревога и тишина"},
                {"title": "Незнакомый берег", "location": "песчаная бухта", "action": "Робинзон встаёт и оглядывает пустынный остров", "camera": "средний план, мягкая дуга", "motion": "поворот головы и корпуса", "mood": "растерянность"},
                {"title": "Следы корабля", "location": "обломки у воды", "action": "Робинзон поднимает деревянную доску рядом с обломками", "camera": "низкий ракурс, лёгкий dolly", "motion": "простое плавное движение руки", "mood": "появляется надежда"},
                {"title": "Первый инструмент", "location": "у кромки джунглей", "action": "Робинзон собирает простой деревянный инструмент", "camera": "детальный боковой план", "motion": "очень небольшой жест рук", "mood": "решимость"},
                {"title": "Остров ждёт", "location": "высокий берег, закатный луч", "action": "Робинзон смотрит на гору и джунгли вдали", "camera": "медленный отъезд назад", "motion": "лёгкое шевеление травы и спокойная поза", "mood": "начало приключения"},
            ],
        },
        prompt,
    )


def _extract_text(payload: dict[str, Any]) -> str:
    texts: list[str] = []
    for item in payload.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    texts.append(part.get("text", ""))
    if not texts:
        raise StoryboardError("Агент вернул ответ без текста раскадровки")
    return "\n".join(texts)


def validate_storyboard(obj: dict[str, Any], prompt: str) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise StoryboardError("Раскадровка должна быть JSON-объектом")
    if not isinstance(obj.get("shots"), list) or len(obj["shots"]) != SHOTS:
        raise StoryboardError(f"Агент должен вернуть ровно {SHOTS} сцен")
    for name in ("title", "visual_bible", "character_bible"):
        if not isinstance(obj.get(name), str) or not obj[name].strip():
            raise StoryboardError(f"Отсутствует поле {name}")
    for i, shot in enumerate(obj["shots"]):
        if not isinstance(shot, dict):
            raise StoryboardError(f"Сцена {i + 1} должна быть объектом")
        for key in ("title", "location", "action", "camera", "motion", "mood"):
            if not isinstance(shot.get(key), str) or not shot[key].strip():
                raise StoryboardError(f"Неполное описание сцены {i + 1}: {key}")
    obj = dict(obj)
    obj["prompt"] = prompt
    obj.setdefault("duration_seconds", DURATION_SECONDS)
    obj.setdefault("shot_duration_seconds", SHOT_DURATION_SECONDS)
    return obj


def create_storyboard(prompt: str, *, client: httpx.Client, api_key: str, model: str) -> dict[str, Any]:
    instructions = (
        "You are a screenplay agent for an image-to-video machinima system. "
        f"Return JSON only. Create exactly {SHOTS} shots that together form a {DURATION_SECONDS}-second silent cinematic short. "
        "The output must be tailored to the user's prompt. Keep the visual identity stable across all shots. "
        "Describe only what is useful for image generation and later frame interpolation. "
        "If the user names a protagonist, keep that protagonist; otherwise invent one. never force Robinson when the prompt asks for another character."
    )
    payload = {
        "model": model,
        "instructions": instructions,
        "input": (
            "Produce a storyboard for an images-to-video pipeline in the Minecraft Machinima aesthetic. "
            "Every shot lasts 4 seconds. Keep motion simple and continuous so later interpolation is feasible. "
            "The returned JSON schema must be followed exactly. User prompt: "
            + prompt
        ),
        "text": {"format": {"type": "json_schema", "name": "storyboard", "schema": STORY_SCHEMA, "strict": True}},
    }
    response = client.post(
        "/responses",
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=240,
    )
    if response.is_error:
        raise StoryboardError(f"OpenAI Responses API: HTTP {response.status_code}")
    text = _extract_text(response.json())
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StoryboardError("Агент вернул невалидный JSON") from exc
    return validate_storyboard(data, prompt)


def build_shot_plan(story: dict[str, Any], frames_per_shot: int) -> dict[str, Any]:
    """План из трёх keyframes и промежуточных кадров для каждой сцены."""
    if frames_per_shot < 3:
        raise StoryboardError("Минимум 3 кадра на сцену")
    plan_shots: list[dict[str, Any]] = []
    for i, shot in enumerate(story["shots"], start=1):
        anchor_indices = [1, frames_per_shot // 2 + 1, frames_per_shot]
        frames = []
        for frame_number in range(1, frames_per_shot + 1):
            progress = (frame_number - 1) / max(frames_per_shot - 1, 1)
            role = "keyframe" if frame_number in anchor_indices else "inbetween"
            left = max((a for a in anchor_indices if a < frame_number), default=None)
            right = min((a for a in anchor_indices if a > frame_number), default=None)
            frames.append({
                "frame_number": frame_number,
                "role": role,
                "timecode_seconds": round(progress * SHOT_DURATION_SECONDS, 3),
                "left_anchor": left if role == "inbetween" else None,
                "right_anchor": right if role == "inbetween" else None,
                "prompt_hint": (
                    "Create the exact opening, mid-action or end pose" if role == "keyframe"
                    else "Create a plausible intermediate pose between two fixed keyframes"
                ),
            })
        plan_shots.append({
            "shot_number": i,
            "title": shot["title"],
            "duration_seconds": SHOT_DURATION_SECONDS,
            "camera": shot["camera"],
            "motion": shot["motion"],
            "anchor_indices": anchor_indices,
            "frames": frames,
        })
    return {
        "title": story["title"],
        "prompt": story["prompt"],
        "duration_seconds": story["duration_seconds"],
        "frames_per_shot": frames_per_shot,
        "total_frames": frames_per_shot * SHOTS,
        "shots": plan_shots,
    }