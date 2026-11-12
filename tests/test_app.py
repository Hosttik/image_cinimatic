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
