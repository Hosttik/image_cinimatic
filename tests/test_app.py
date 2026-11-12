import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from app.demo import make_demo_gif
from app.render import render
from app.story import DURATION_SECONDS, SHOTS, StoryboardError, demo_storyboard, validate_storyboard


def test_storyboard_contract():
    story = demo_storyboard("Robinson is shipwrecked on a tropical island")
    assert story["duration_seconds"] == DURATION_SECONDS
    assert len(story["shots"]) == SHOTS
    assert validate_storyboard(story, story["prompt"]) == story
    with pytest.raises(StoryboardError):
        validate_storyboard({**story, "shots": story["shots"][:3]}, story["prompt"])


def test_demo_gif(tmp_path):
    path = make_demo_gif(1, tmp_path / "shot.gif")
    with Image.open(path) as im:
        assert im.n_frames == 16
        assert im.size == (512, 288)
        assert im.info.get("duration") == 250


def test_render_20s(tmp_path):
    gifs = [make_demo_gif(i, tmp_path / f"shot_{i}.gif") for i in range(1, 6)]
    video = render(gifs, tmp_path / "out.mp4", fps=30, interpolate=False, width=320, height=180)
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries", "stream=nb_read_frames,r_frame_rate,width,height", "-of", "json", str(video)], capture_output=True, text=True, check=True)
    stream = json.loads(probe.stdout)["streams"][0]
    assert stream["nb_read_frames"] == "600"
    assert stream["r_frame_rate"] == "30/1"
    assert stream["width"] == 320 and stream["height"] == 180


def test_storyboard_api_schema_and_custom_prompt():
    import httpx
    from app.story import create_storyboard

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


def test_gif_api_payload(tmp_path):
    import httpx
    import base64
    from app.images import make_gif

    story = demo_storyboard("A Robinson Crusoe Minecraft shipwreck adventure")
    fake_gif = make_demo_gif(1, tmp_path / "source.gif").read_bytes()
    reference = tmp_path / "reference.png"
    Image.new("RGB", (100, 100), "#ffffff").save(reference)
    captured = {}
    def handle(request):
        captured["url"] = str(request.url)
        captured["body"] = request.content
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(fake_gif).decode()}]})
    with httpx.Client(base_url="https://api.openai.com/v1", transport=httpx.MockTransport(handle)) as client:
        gif = make_gif(story, story["shots"][0], 1, reference, tmp_path, client=client, api_key="test-key", model="gpt-image-2.6-sunburst")
    assert gif.read_bytes() == fake_gif
    assert captured["url"].endswith("/images/edits")
    assert b'frame_count' in captured["body"] and b'16' in captured["body"]
    assert b'output_format' in captured["body"] and b'gif' in captured["body"]
