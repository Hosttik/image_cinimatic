"""Простой локальный FastAPI сервис: промпт -> агент -> GIF -> MP4."""
from __future__ import annotations

import json
import os
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

from .demo import make_demo_gif
from .images import build_reference, make_gif
from .render import render
from .story import create_storyboard, demo_storyboard

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

app = FastAPI(title="BLOCKFRAME — Minecraft Machinima Generator", version="0.1.0")
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="machinima")
lock = threading.Lock()
jobs: dict[str, dict] = {}


class NewJob(BaseModel):
    prompt: str = Field(min_length=12, max_length=2000)
    mode: Literal["demo", "ai"] = "demo"
    fps: Literal[30, 60] = 30
    interpolate: bool = True


def _set(job_id: str, **changes: object) -> None:
    with lock:
        jobs[job_id].update(changes)


def _do_job(job_id: str, payload: NewJob) -> None:
    folder = OUTPUT_DIR / job_id
    folder.mkdir(parents=True, exist_ok=True)
    try:
        _set(job_id, status="running", stage="Составляем сценарий", progress=5)
        if payload.mode == "ai":
            with httpx.Client(base_url=BASE_URL, timeout=240) as client:
                story = create_storyboard(payload.prompt, client=client, api_key=API_KEY, model=STORY_MODEL)
                _set(job_id, storyboard=story, stage="Генерируем референс персонажа", progress=18)
                (folder / "storyboard.json").write_text(json.dumps(story, ensure_ascii=False, indent=2), encoding="utf-8")
                reference = build_reference(story, folder, client=client, api_key=API_KEY, model=IMAGE_MODEL)
                gifs = []
                for i, shot in enumerate(story["shots"], start=1):
                    _set(job_id, stage=f"Генерируем анимацию {i}/5", progress=20 + i * 10)
                    gifs.append(make_gif(story, shot, i, reference, folder, client=client, api_key=API_KEY, model=IMAGE_MODEL))
        else:
            story = demo_storyboard(payload.prompt)
            _set(job_id, storyboard=story, progress=18)
            (folder / "storyboard.json").write_text(json.dumps(story, ensure_ascii=False, indent=2), encoding="utf-8")
            gifs = []
            for i in range(1, 6):
                _set(job_id, stage=f"Создаём демонстрационные кадры {i}/5", progress=20 + i * 10)
                gifs.append(make_demo_gif(i, folder / f"shot_{i:02d}.gif"))
        _set(job_id, stage="Интерполяция и монтаж в MP4", progress=78)
        video = render(gifs, folder / "result.mp4", fps=payload.fps, interpolate=payload.interpolate)
        _set(job_id, status="done", stage="Готово", progress=100, video_url=f"/api/jobs/{job_id}/video")
        assert video.exists()
    except Exception as exc:
        # Local-dev only: don't expose third-party API response bodies or keys.
        _set(job_id, status="failed", stage="Ошибка", error=f"{type(exc).__name__}: {str(exc)[:900]}")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "app" / "static" / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "api_key_configured": bool(API_KEY), "image_model": IMAGE_MODEL, "story_model": STORY_MODEL}


@app.post("/api/jobs", status_code=202)
def create_job(request: NewJob) -> dict:
    if request.mode == "ai" and not API_KEY:
        raise HTTPException(400, "Для режима AI задайте OPENAI_API_KEY в .env")
    job_id = uuid.uuid4().hex
    job = {"id": job_id, "mode": request.mode, "fps": request.fps, "status": "queued", "stage": "В очереди", "progress": 0,
           "storyboard": None, "video_url": None, "error": None,
           "created_at": datetime.now(timezone.utc).isoformat()}
    with lock:
        jobs[job_id] = job
    executor.submit(_do_job, job_id, request)
    return job


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    with lock:
        if job_id not in jobs:
            raise HTTPException(404, "Задание не найдено (после перезапуска сервиса история сбрасывается)")
        return dict(jobs[job_id])


@app.get("/api/jobs/{job_id}/video")
def get_video(job_id: str) -> FileResponse:
    with lock:
        if job_id not in jobs or jobs[job_id]["status"] != "done":
            raise HTTPException(404, "Ролик ещё не готов")
    return FileResponse(OUTPUT_DIR / job_id / "result.mp4", media_type="video/mp4", filename="blockframe_20s.mp4")


@app.get("/api/jobs/{job_id}/storyboard")
def get_storyboard(job_id: str) -> FileResponse:
    with lock:
        if job_id not in jobs or not jobs[job_id]["storyboard"]:
            raise HTTPException(404, "Раскадровка ещё не готова")
    return FileResponse(OUTPUT_DIR / job_id / "storyboard.json", media_type="application/json", filename="storyboard.json")
