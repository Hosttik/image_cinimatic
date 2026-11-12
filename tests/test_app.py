import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import pytest
from PIL import Image

from app.demo import make_demo_frames
from app.images import make_frame
from app.render import render
from app.story import (
    DURATION_SECONDS,
    SHOTS,
    StoryboardError,
    build_shot_plan,
    create_storyboard,
    demo_storyboard,
    validate_storyboard,
)


def test_storyboard_contract_and_shot_plan():
    story = demo_storyboard("Robinson is shipwrecked on a tropical island")
    assert story["duration_seconds"] == DURATION_SECONDS
    assert len(story["shots"]) == SHOTS
    assert validate_storyboard(story, story["prompt"]) == story
    plan = build_shot_plan(story, 6)
    assert plan["frames_per_shot"] == 6
    assert plan["total_frames"] == 30
    assert len(plan["shots"]) == 5
    assert len(plan["shots"][0]["frames"]) == 6
    with pytest.raises(StoryboardError):
        validate_storyboard({**story, "shots": story["shots"][:3]}, story["prompt"])


def test_demo_frames(tmp_path):
    frames = make_demo_frames(1, tmp_path / "shot_01", frame_count=6)
    assert len(frames) == 6
    with Image.open(frames[0]) as im:
        assert im.size == (1024, 576)


def test_optical_flow_render_scene(tmp_path):
    from app.render import render_scene_clip
    frames = make_demo_frames(1, tmp_path / "frames", frame_count=4, size=(320, 180))
    clip = render_scene_clip(frames, tmp_path / "flow.mp4", fps=30, interpolate=True, width=320, height=180)
    out = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
        "-show_entries", "stream=nb_read_frames", "-of", "json", str(clip)
    ], capture_output=True, check=True, text=True)
    assert json.loads(out.stdout)["streams"][0]["nb_read_frames"] == "120"


def test_render_20s_from_frames(tmp_path):
    shots = [make_demo_frames(i, tmp_path / f"shot_{i:02d}", frame_count=6, size=(320, 180)) for i in range(1, 6)]
    video = render(shots, tmp_path / "out.mp4", fps=30, interpolate=False, width=320, height=180)
    probe = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
        "-show_entries", "stream=nb_read_frames,r_frame_rate,width,height", "-of", "json", str(video)
    ], capture_output=True, text=True, check=True)
    stream = json.loads(probe.stdout)["streams"][0]
    assert stream["nb_read_frames"] == "600"
    assert stream["r_frame_rate"] == "30/1"
    assert stream["width"] == 320 and stream["height"] == 180


def test_storyboard_api_schema_and_custom_prompt():
    story = demo_storyboard("A space explorer arrives on Mars in Minecraft")
    captured = {}
    def handle(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(story)}]}]})
    with httpx.Client(base_url="https://api.openai.com/v1", transport=httpx.MockTransport(handle)) as client:
        result = create_storyboard(story["prompt"], client=client, api_key="test-key", model="test-model")
    assert len(result["shots"]) == 5
    assert result["prompt"] == "A space explorer arrives on Mars in Minecraft"
    assert captured["text"]["format"]["strict"]
    assert "never force Robinson" in captured["instructions"]


def test_frame_api_payload(tmp_path):
    import base64

    story = demo_storyboard("A Robinson Crusoe Minecraft shipwreck adventure")
    reference = tmp_path / "reference.png"
    previous = tmp_path / "previous.png"
    Image.new("RGB", (100, 100), "#ffffff").save(reference)
    Image.new("RGB", (100, 100), "#eeeeee").save(previous)
    fake_png = reference.read_bytes()
    captured = {}
    def handle(request):
        captured["url"] = str(request.url)
        captured["body"] = request.content
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(fake_png).decode()}]})
    with httpx.Client(base_url="https://api.openai.com/v1", transport=httpx.MockTransport(handle)) as client:
        frame = make_frame(story, story["shots"][0], 1, 2, 6, reference, tmp_path, client=client, api_key="test-key", model="gpt-image-2.6-sunburst", previous_frame=previous)
    assert frame.read_bytes() == fake_png
    assert captured["url"].endswith("/images/edits")
    assert b'output_format' in captured["body"] and b'png' in captured["body"]
    assert b'frame 2/6' in captured["body"]
    assert b'B_left_anchor.png' in captured["body"]



def test_anchor_first_generation_and_anchored_inbetweens(tmp_path, monkeypatch):
    from app import images
    story = demo_storyboard("Robinson Crusoe on an island")
    master = tmp_path / "master.png"
    Image.new("RGB", (80, 45)).save(master)
    prior = tmp_path / "last_shot.png"
    Image.new("RGB", (80, 45)).save(prior)
    calls = []

    def fake_make_frame(story, shot, shot_index, frame_index, frame_count, reference, folder, **kwargs):
        calls.append((frame_index, kwargs.get("previous_frame"), kwargs.get("right_frame"), kwargs.get("boundary_frame")))
        path = folder / f"frame_{frame_index:03d}.png"
        Image.new("RGB", (80, 45), (frame_index * 14, 80, 80)).save(path)
        return path

    monkeypatch.setattr(images, "make_frame", fake_make_frame)
    frames = images.make_shot_frames(story, story["shots"][1], 2, 6, master, tmp_path / "frames", client=None, api_key="x", model="test", previous_shot_frame=prior)
    assert [item[0] for item in calls] == [1, 4, 6, 2, 3, 5]
    assert calls[0][3] == prior
    assert calls[3][1] == frames[0] and calls[3][2] == frames[3]
    assert len(frames) == 6


def _poll_job(client, job_id, terminal="done"):
    import time
    for _ in range(140):
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["status"] in (terminal, "failed"):
            return job
        time.sleep(0.1)
    raise AssertionError("Background job timed out")


def test_scene_regeneration_preserves_other_scenes_and_job_recovery(tmp_path, monkeypatch):
    from hashlib import sha256
    from fastapi.testclient import TestClient
    from app import main

    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    with TestClient(main.app) as client:
        res = client.post("/api/jobs", json={"prompt": "Robinson survives on a Minecraft island", "mode": "demo", "fps": 30, "frames_per_shot": 4, "interpolate": False})
        assert res.status_code == 202
        job_id = res.json()["id"]
        job = _poll_job(client, job_id)
        assert job["status"] == "done", job.get("error")
        root = tmp_path / job_id
        assert (root / "project_state.json").is_file()
        first_before = sha256((root / "frames/shot_01/frame_001.png").read_bytes()).hexdigest()
        second_before = sha256((root / "frames/shot_02/frame_001.png").read_bytes()).hexdigest()
        clip1 = sha256((root / "clip_01.mp4").read_bytes()).hexdigest()
        assert client.get(f"/api/jobs/{job_id}/shots/1/frames/1").status_code == 200
        assert client.get(f"/api/jobs/{job_id}/shots/999/frames/1").status_code == 404
        assert client.get(f"/api/jobs/{job_id}/shots/1/frames/999").status_code == 404

        r = client.post(f"/api/jobs/{job_id}/shots/2/regenerate", json={"instruction": "closer camera"})
        assert r.status_code == 202
        assert client.post(f"/api/jobs/{job_id}/shots/2/regenerate", json={}).status_code == 409
        job = _poll_job(client, job_id)
        assert job["status"] == "done" and job["video_version"] == 2, job.get("regen_error")
        assert job["revisions"] == [0,1,0,0,0]
        assert job["history"][0]["instruction"] == "closer camera"
        assert sha256((root / "frames/shot_01/frame_001.png").read_bytes()).hexdigest() == first_before
        assert sha256((root / "frames/shot_02/frame_001.png").read_bytes()).hexdigest() == second_before
        assert sha256((root / "clip_01.mp4").read_bytes()).hexdigest() == clip1
        assert job["shot_dirs"][1].startswith("revisions/shot_02/")
        new_frame = root / job["shot_dirs"][1] / "frame_001.png"
        assert new_frame.read_bytes() != (root / "frames/shot_02/frame_001.png").read_bytes()
        assert client.get(f"/api/jobs/{job_id}/video").status_code == 200
        assert client.get(f"/api/jobs/{job_id}/shots/2/frames/1").content == new_frame.read_bytes()

        main.jobs.pop(job_id)
        restored = client.get(f"/api/jobs/{job_id}").json()
        assert restored["video_version"] == 2
        assert restored["shot_dirs"][1] == job["shot_dirs"][1]


def test_failed_regeneration_keeps_previous_result(tmp_path, monkeypatch):
    from hashlib import sha256
    from fastapi.testclient import TestClient
    from app import main
    from app.render import RenderError
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    with TestClient(main.app) as client:
        r = client.post("/api/jobs", json={"prompt": "A blocky Robinson Crusoe island adventure", "mode": "demo", "frames_per_shot": 4, "interpolate": False})
        job_id = r.json()["id"]
        assert _poll_job(client, job_id)["status"] == "done"
        before = sha256((tmp_path / job_id / "result.mp4").read_bytes()).hexdigest()

        def fail(*args, **kwargs):
            raise RenderError("intentional test failure")
        monkeypatch.setattr(main, "render_scene_clip", fail)
        assert client.post(f"/api/jobs/{job_id}/shots/3/regenerate", json={}).status_code == 202
        job = _poll_job(client, job_id)
        assert job["status"] == "done"
        assert job["regen_error"] and "intentional test failure" in job["regen_error"]
        assert job["video_version"] == 1
        assert job["revisions"] == [0] * 5
        assert sha256((tmp_path / job_id / "result.mp4").read_bytes()).hexdigest() == before