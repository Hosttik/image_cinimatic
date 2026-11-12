"""Генерация раскадровки через Responses API и безопасный demo-fallback."""
from __future__ import annotations

import json
from typing import Any

import httpx

SHOTS = 5
DURATION_SECONDS = 20

STORY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "visual_bible": {"type": "string"},
        "character_bible": {"type": "string"},
        "shots": {
            "type": "array",
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
    """Изолированный тестовый сценарий: не выдаём за ответ модели."""
    return {
        "title": "Робинзон: первый день",
        "prompt": prompt,
        "duration_seconds": DURATION_SECONDS,
        "visual_bible": (
            "Minecraft machinima, unmistakable voxel world, blocky textures, "
            "square low-poly silhouettes, sunrise sunlight, cinematic composition, "
            "no HUD, no text, no logos; same palette and materials throughout."
        ),
        "character_bible": (
            "Robinson: same blocky adult survivor in every shot, square head, "
            "short dark beard, weathered blue shirt, brown pants and brown backpack."
        ),
        "shots": [
            {"title": "После кораблекрушения", "location": "берег, рассвет", "action": "Робинзон лежит у кромки воды, волна откатывается", "camera": "широкий плавный наезд", "motion": "волны и очень медленное пробуждение", "mood": "тревога и тишина"},
            {"title": "Незнакомый берег", "location": "песчаная бухта", "action": "Робинзон встаёт и оглядывает пустынный остров", "camera": "средний план, мягкая дуга", "motion": "один поворот головы, движение листьев пальмы", "mood": "растерянность"},
            {"title": "Следы корабля", "location": "обломки у воды", "action": "Робинзон поднимает деревянную доску", "camera": "низкий ракурс, лёгкий dolly", "motion": "одна ясная плавная траектория руки", "mood": "появляется надежда"},
            {"title": "Первый инструмент", "location": "у кромки джунглей", "action": "Робинзон собирает простой деревянный инструмент", "camera": "детальный боковой план", "motion": "очень небольшой жест рук", "mood": "решимость"},
            {"title": "Остров ждёт", "location": "высокий берег, закатный луч", "action": "Робинзон смотрит на гору и джунгли вдали", "camera": "медленный отъезд назад", "motion": "шевеление травы, спокойная поза", "mood": "начало приключения"},
        ],
    }


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
    if not isinstance(obj, dict) or not isinstance(obj.get("shots"), list) or len(obj["shots"]) != SHOTS:
        raise StoryboardError(f"Агент должен вернуть ровно {SHOTS} сцен")
    for name in ("title", "visual_bible", "character_bible"):
        if not isinstance(obj.get(name), str) or not obj[name].strip():
            raise StoryboardError(f"Отсутствует поле {name}")
    for i, shot in enumerate(obj["shots"]):
        if not isinstance(shot, dict) or any(not isinstance(shot.get(key), str) or not shot[key].strip() for key in ("title", "location", "action", "camera", "motion", "mood")):
            raise StoryboardError(f"Неполное описание сцены {i + 1}")
    obj["prompt"] = prompt
    obj["duration_seconds"] = DURATION_SECONDS
    return obj


def create_storyboard(prompt: str, *, client: httpx.Client, api_key: str, model: str) -> dict[str, Any]:
    instructions = (
        "You are a professional cinematic Minecraft machinima director and storyboard agent. "
        "Write a coherent story for EXACTLY 20 seconds, divided into EXACTLY five 4-second shots. "
        "Follow the user-provided characters, setting, genre and action; never force Robinson Crusoe if the user asked for something else. "
        "Build a single coherent world and stable, recurring character designs. "
        "Describe gentle, physically plausible continuous movement in each shot: 16 consecutive "
        "animation frames must show one action, not a montage. Keep camera and actions subtle to "
        "enable motion interpolation. No titles, logos, watermarks, UI, copyrighted soundtrack. "
        "Produce character_bible and visual_bible in English, detailed enough to repeat verbatim "
        "in every image request. Shots may be in English or Russian. Respect the user's premise."
    )
    response = client.post(
        "/responses",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "instructions": instructions,
            "input": prompt,
            "text": {"format": {"type": "json_schema", "name": "machinima_story", "strict": True, "schema": STORY_SCHEMA}},
        },
        timeout=150,
    )
    response.raise_for_status()
    try:
        return validate_storyboard(json.loads(_extract_text(response.json())), prompt)
    except (ValueError, KeyError, TypeError) as exc:
        raise StoryboardError("Некорректный JSON раскадровки от агента") from exc
