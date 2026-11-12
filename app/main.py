"""BLOCKFRAME: текст -> сценарий -> keyframes/in-betweens -> MP4.

Прототип запускается локально, одна очередь генерации. Для внешнего доступа нужна авторизация.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .demo import make_demo_frames
from .images import build_reference, make_shot_frames
from .render import concat_clips, render, render_scene_clip
from .story import SHOTS, build_shot_plan, create_storyboard, demo_storyboard

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "outputs"))
if not OUTPUT_DIR.is_absolute():
    OUTPUT_DIR = ROOT / OUTPUT_DIR
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2.6-sunburst")
STORY_MODEL = os.getenv("OPENAI_STORY_MODEL", "gpt-5.6-terra")
API_KEY = os.getenv("OPENAI_API_KEY", "")
BASE_URL = "https://api.openai.com/v1"

app = FastAPI(title="BLOCKFRAME — Minecraft Machinima Generator", version="0.3.0")
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="machinima")
lock = threading.Lock()
jobs: dict[str, dict] = {}


class NewJob(BaseModel):
    prompt: str = Field(min_length=12, max_length=2000)
    mode: Literal["demo", "ai"] = "demo"
    fps: Literal[30, 60] = 30
    interpolate: bool = False
    frames_per_shot: Literal[4, 6, 8, 12] = 6


class RegenerateShot(BaseModel):
    instruction: str = Field(default="", max_length=500)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _folder(job_id: str) -> Path:
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(404, "Задание не найдено") from None
    if len(job_id) != 32:
        raise HTTPException(404, "Задание не найдено")
    return OUTPUT_DIR / job_id


def _set(job_id: str, **changes: object) -> None:
    with lock:
        jobs[job_id].update(changes)


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _persist(job_id: str) -> None:
    with lock:
        snapshot = dict(jobs[job_id])
    _write_json(_folder(job_id) / "project_state.json", snapshot)


def _current_job(job_id: str) -> dict:
    folder = _folder(job_id)
    with lock:
        job = jobs.get(job_id)
    if job is not None:
        return dict(job)
    saved = folder / "project_state.json"
    if not saved.is_file():
        raise HTTPException(404, "Задание не найдено")
    try:
        recovered = json.loads(saved.read_text(encoding="utf-8"))
        if recovered.get("status") != "done" or recovered.get("id") != job_id:
            raise ValueError("Incomplete saved job")
    except (ValueError, OSError) as exc:
        raise HTTPException(404, "Сохранённое задание не удалось восстановить") from exc
    with lock:
        jobs.setdefault(job_id, recovered)
        return dict(jobs[job_id])


def _frames_for_job(job_id: str, shot_number: int) -> list[Path]:
    job = _current_job(job_id)
    folder = _folder(job_id).resolve()
    rel = job["shot_dirs"][shot_number - 1]
    path = (folder / rel).resolve()
    if not path.is_relative_to(folder):
        raise HTTPException(500, "Некорректный каталог кадров")
    frames = sorted(path.glob("frame_*.png"))
    if len(frames) != job["frames_per_shot"]:
        raise HTTPException(500, "Не хватает кадров сцены")
    return frames


def _do_job(job_id: str, payload: NewJob) -> None:
    folder = _folder(job_id)
    frame_root = folder / "frames"
    frame_root.mkdir(parents=True, exist_ok=True)
    try:
        _set(job_id, status="running", stage="Составляем сценарий", progress=5)
        if payload.mode == "ai":
            with httpx.Client(base_url=BASE_URL, timeout=240) as client:
                story = create_storyboard(payload.prompt, client=client, api_key=API_KEY, model=STORY_MODEL)
                plan = build_shot_plan(story, payload.frames_per_shot)
                _write_json(folder / "storyboard.json", story)
                _write_json(folder / "shot_plan.json", plan)
                _set(job_id, storyboard=story, shot_plan=plan, stage="Генерируем master reference", progress=13)
                reference = build_reference(story, folder, client=client, api_key=API_KEY, model=IMAGE_MODEL)
                shot_groups: list[list[Path]] = []
                for i, shot in enumerate(story["shots"], start=1):
                    _set(job_id, stage=f"AI: ключевые и промежуточные кадры сцены {i}/5")
                    boundary = shot_groups[-1][-1] if shot_groups else None
                    def on_frame(num: int, count: int, shot_index: int = i) -> None:
                        _set(job_id, progress=15 + int(60 * ((shot_index - 1) * count + num) / (SHOTS * count)))
                    shot_groups.append(make_shot_frames(
                        story, shot, i, payload.frames_per_shot, reference,
                        frame_root / f"shot_{i:02d}", client=client, api_key=API_KEY,
                        model=IMAGE_MODEL, previous_shot_frame=boundary, on_frame=on_frame,
                    ))
        else:
            story = demo_storyboard(payload.prompt)
            plan = build_shot_plan(story, payload.frames_per_shot)
            _write_json(folder / "storyboard.json", story)
            _write_json(folder / "shot_plan.json", plan)
            _set(job_id, storyboard=story, shot_plan=plan, progress=15)
            shot_groups = []
            for i in range(1, SHOTS + 1):
                _set(job_id, stage=f"Demo: создаём PNG сцены {i}/{SHOTS}", progress=15 + i * 12)
                shot_groups.append(make_demo_frames(i, frame_root / f"shot_{i:02d}", frame_count=payload.frames_per_shot))
        _set(job_id, stage="Монтаж 5 сцен в MP4", progress=80)
        render(shot_groups, folder / "result.mp4", fps=payload.fps, interpolate=payload.interpolate)
        _set(job_id, status="done", stage="Готово", progress=100,
             shot_dirs=[str(Path("frames") / f"shot_{i:02d}") for i in range(1, SHOTS + 1)],
             clip_dirs=[f"clip_{i:02d}.mp4" for i in range(1, SHOTS + 1)],
             revisions=[0] * SHOTS, history=[], video_version=1,
             video_url=f"/api/jobs/{job_id}/video", frame_count_total=payload.frames_per_shot * SHOTS)
        _persist(job_id)
    except Exception as exc:
        _set(job_id, status="failed", stage="Ошибка", error=f"{type(exc).__name__}: {str(exc)[:700]}")


def _do_regenerate(job_id: str, shot_number: int, instruction: str) -> None:
    folder = _folder(job_id)
    old = copy.deepcopy(_current_job(job_id))
    revision = old["revisions"][shot_number - 1] + 1
    revision_id = uuid.uuid4().hex[:10]
    revision_folder = folder / "revisions" / f"shot_{shot_number:02d}" / f"r{revision:03d}_{revision_id}"
    new_frame_folder = revision_folder / "frames"
    published = False
    backup_video = revision_folder / "previous_result.mp4"
    try:
        _set(job_id, status="regenerating", stage=f"Регенерируем сцену {shot_number}", progress=10, regen_error=None)
        if old["mode"] == "ai":
            reference = folder / "character_reference.png"
            if not reference.is_file():
                raise RuntimeError("Отсутствует мастер-референс персонажа")
            boundary = _frames_for_job(job_id, shot_number - 1)[-1] if shot_number > 1 else None
            with httpx.Client(base_url=BASE_URL, timeout=240) as client:
                def on_frame(num: int, count: int) -> None:
                    _set(job_id, stage=f"AI-кадры сцены {shot_number}: {num}/{count}", progress=10 + int(60 * num / count))
                frames = make_shot_frames(
                    old["storyboard"], old["storyboard"]["shots"][shot_number - 1],
                    shot_number, old["frames_per_shot"], reference, new_frame_folder,
                    client=client, api_key=API_KEY, model=IMAGE_MODEL,
                    previous_shot_frame=boundary, instruction=instruction, on_frame=on_frame,
                )
        else:
            frames = make_demo_frames(shot_number, new_frame_folder, old["frames_per_shot"], variant=revision)
        _set(job_id, stage="Пересчитываем только выбранный клип", progress=77)
        staged_clip = revision_folder / f"clip_{shot_number:02d}.mp4"
        render_scene_clip(frames, staged_clip, fps=old["fps"], interpolate=old["interpolate"])
        clips = [staged_clip if i == shot_number else folder / old["clip_dirs"][i - 1] for i in range(1, SHOTS + 1)]
        _set(job_id, stage="Пересобираем финальный MP4", progress=90)
        staged_video = revision_folder / "result.mp4"
        concat_clips(clips, staged_video)
        # Версионированные клипы и кадры не перезаписываются. Публикуем только один MP4.
        shutil.copy2(folder / "result.mp4", backup_video)
        os.replace(staged_video, folder / "result.mp4")
        published = True
        with lock:
            job = jobs[job_id]
            job["shot_dirs"][shot_number - 1] = str(new_frame_folder.relative_to(folder))
            job["clip_dirs"][shot_number - 1] = str(staged_clip.relative_to(folder))
            job["revisions"][shot_number - 1] = revision
            job["history"].append({"shot_number": shot_number, "revision": revision, "instruction": instruction, "at": _now()})
            job.update(status="done", stage=f"Сцена {shot_number} обновлена", progress=100, video_version=old["video_version"] + 1, regen_error=None)
        _persist(job_id)
    except Exception as exc:
        # Если возникла ошибка после публикации, восстанавливаем старый файл и метаданные.
        if published and backup_video.is_file():
            os.replace(backup_video, folder / "result.mp4")
        with lock:
            jobs[job_id] = {**old, "status": "done", "stage": "Регенерация не удалась — старый ролик сохранён",
                            "progress": 100, "regen_error": f"{type(exc).__name__}: {str(exc)[:700]}"}
        _persist(job_id)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "app" / "static" / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "api_key_configured": bool(API_KEY), "image_model": IMAGE_MODEL,
            "story_model": STORY_MODEL, "pipeline": "images-to-video", "version": "0.3.0"}


@app.post("/api/jobs", status_code=202)
def create_job(request: NewJob) -> dict:
    if request.mode == "ai" and not API_KEY:
        raise HTTPException(400, "Для режима AI задайте OPENAI_API_KEY в .env")
    job_id = uuid.uuid4().hex
    job = {
        "id": job_id, "mode": request.mode, "fps": request.fps, "frames_per_shot": request.frames_per_shot,
        "interpolate": request.interpolate, "status": "queued", "stage": "В очереди", "progress": 0,
        "storyboard": None, "shot_plan": None, "video_url": None, "video_version": 0,
        "shot_dirs": [], "clip_dirs": [], "revisions": [0] * SHOTS, "history": [], "frame_count_total": 0,
        "error": None, "regen_error": None, "created_at": _now(),
    }
    with lock:
        jobs[job_id] = job
    executor.submit(_do_job, job_id, request)
    return dict(job)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    return _current_job(job_id)


@app.post("/api/jobs/{job_id}/shots/{shot_number}/regenerate", status_code=202)
def regenerate_shot(job_id: str, shot_number: int, request: RegenerateShot) -> dict:
    if shot_number < 1 or shot_number > SHOTS:
        raise HTTPException(422, "Номер сцены должен быть от 1 до 5")
    job = _current_job(job_id)
    if job["mode"] == "ai" and not API_KEY:
        raise HTTPException(400, "Для AI-регенерации задайте OPENAI_API_KEY")
    with lock:
        if jobs[job_id]["status"] != "done":
            raise HTTPException(409, "Задание занято или ещё не готово")
        jobs[job_id].update(status="regenerating", stage="В очереди на регенерацию", progress=0, regen_error=None)
    executor.submit(_do_regenerate, job_id, shot_number, request.instruction.strip())
    return {"id": job_id, "shot_number": shot_number, "status": "regenerating"}


@app.get("/api/jobs/{job_id}/video")
def get_video(job_id: str) -> FileResponse:
    job = _current_job(job_id)
    path = _folder(job_id) / "result.mp4"
    if not job.get("video_url") or not path.is_file():
        raise HTTPException(404, "Ролик ещё не готов")
    return FileResponse(path, media_type="video/mp4", filename="blockframe_20s.mp4")


@app.get("/api/jobs/{job_id}/storyboard")
def get_storyboard(job_id: str) -> FileResponse:
    job = _current_job(job_id)
    if not job.get("storyboard"):
        raise HTTPException(404, "Раскадровка ещё не готова")
    return FileResponse(_folder(job_id) / "storyboard.json", media_type="application/json", filename="storyboard.json")


@app.get("/api/jobs/{job_id}/shot-plan")
def get_shot_plan(job_id: str) -> FileResponse:
    job = _current_job(job_id)
    if not job.get("shot_plan"):
        raise HTTPException(404, "План кадров ещё не готов")
    return FileResponse(_folder(job_id) / "shot_plan.json", media_type="application/json", filename="shot_plan.json")


@app.get("/api/jobs/{job_id}/shots/{shot_number}/frames/{frame_number}")
def get_frame(job_id: str, shot_number: int, frame_number: int) -> FileResponse:
    if not (1 <= shot_number <= SHOTS):
        raise HTTPException(404, "Сцена не найдена")
    job = _current_job(job_id)
    if not job.get("shot_dirs") or not (1 <= frame_number <= job["frames_per_shot"]):
        raise HTTPException(404, "Кадр не найден")
    return FileResponse(_frames_for_job(job_id, shot_number)[frame_number - 1], media_type="image/png")