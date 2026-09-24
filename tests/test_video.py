"""Video/recording tests. FFmpeg is required for the real mux/concat tests (skipped otherwise)."""

import shutil
import subprocess
import time
import wave
from pathlib import Path

import pytest

from tutorial_ai.models.recording import RecordingResult, RecordingsManifest, RecordingStatus
from tutorial_ai.recording.recorder import capture_command
from tutorial_ai.video.processor import AudioClip, mux_command

ffmpeg = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(ffmpeg is None, reason="ffmpeg not installed")


def _tone(path: Path, seconds: float, rate: int = 24_000) -> None:
    import math

    path.parent.mkdir(parents=True, exist_ok=True)
    frames = bytearray()
    for i in range(int(seconds * rate)):
        value = int(8000 * math.sin(2 * math.pi * 440 * i / rate))
        frames += value.to_bytes(2, "little", signed=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))


def _silent_video(path: Path, seconds: float) -> None:
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", f"color=c=blue:s=320x180:r=30:d={seconds}", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(path)], check=True)


def test_capture_command_test_source_needs_no_device(tmp_path):
    command = capture_command("ffmpeg", None, tmp_path / "out.mp4", 1920, 1080, 30)
    assert "lavfi" in command and "avfoundation" not in command


def test_capture_command_scales_and_letterboxes_to_1080p(tmp_path):
    command = capture_command("ffmpeg", 1, tmp_path / "out.mp4", 1920, 1080, 30)
    assert command[command.index("-i") + 1] == "1:none"  # screen device, no microphone
    vf = command[command.index("-vf") + 1]
    assert "scale=1920:1080:force_original_aspect_ratio=decrease" in vf and "pad=1920:1080" in vf
    assert "-capture_cursor" in command and command[-1].endswith("out.mp4")


def test_mux_command_delays_each_clip_to_its_offset(tmp_path):
    clips = [AudioClip(path=tmp_path / "a.wav", start_seconds=0.5), AudioClip(path=tmp_path / "b.wav", start_seconds=3.25)]
    command = mux_command("ffmpeg", tmp_path / "v.mp4", clips, tmp_path / "o.mp4", normalize=False)
    filters = command[command.index("-filter_complex") + 1]
    assert "[1:a]adelay=500|500" in filters and "[2:a]adelay=3250|3250" in filters
    assert "amix=inputs=2" in filters and "-shortest" in command and "-c:v" in command


@needs_ffmpeg
def test_mux_and_concat_produce_playable_video_with_audio(tmp_path):
    from tutorial_ai.audio.duration import media_duration
    from tutorial_ai.video.processor import VideoProcessor

    _silent_video(tmp_path / "scene1.raw.mp4", 4)
    _silent_video(tmp_path / "scene2.raw.mp4", 3)
    _tone(tmp_path / "n1.wav", 1.0)
    _tone(tmp_path / "n2.wav", 1.5)
    processor = VideoProcessor()
    s1 = processor.mux(tmp_path / "scene1.raw.mp4", [AudioClip(path=tmp_path / "n1.wav", start_seconds=0.5),
                                                     AudioClip(path=tmp_path / "n2.wav", start_seconds=2.0)], tmp_path / "scene1.mp4")
    s2 = processor.mux(tmp_path / "scene2.raw.mp4", [], tmp_path / "scene2.mp4")
    assert media_duration(s1) == pytest.approx(4, abs=0.3)
    final = processor.concatenate([s1, s2], tmp_path / "tutorial.mp4")
    assert media_duration(final) == pytest.approx(7, abs=0.5)
    probe = subprocess.run([shutil.which("ffprobe"), "-v", "error", "-show_entries", "stream=codec_type",
                            "-of", "csv=p=0", str(final)], capture_output=True, text=True).stdout
    assert "video" in probe and "audio" in probe


def test_recordings_manifest_replaces_scene_results():
    manifest = RecordingsManifest()
    manifest.put(RecordingResult(scene_id="scene_002", status=RecordingStatus.PAUSED, error="x"))
    manifest.put(RecordingResult(scene_id="scene_001", status=RecordingStatus.SUCCESS, video_path="scenes/scene_001.mp4"))
    manifest.put(RecordingResult(scene_id="scene_002", status=RecordingStatus.SUCCESS, video_path="scenes/scene_002.mp4"))
    assert [r.scene_id for r in manifest.results] == ["scene_001", "scene_002"]
    assert manifest.get("scene_002").status is RecordingStatus.SUCCESS
    assert RecordingsManifest.model_validate_json(manifest.model_dump_json()) == manifest


@needs_ffmpeg
def test_recorder_reports_a_capture_that_dies_instead_of_reusing_old_output(tmp_path, monkeypatch):
    from tutorial_ai.recording import recorder as recorder_module
    from tutorial_ai.recording.recorder import RecorderError, ScreenRecorder

    stale = tmp_path / "scene.raw.mp4"
    stale.write_bytes(b"\0" * 4096)
    # A capture command that fails after start-up (invalid filter) stands in for a rejected device.
    monkeypatch.setattr(recorder_module, "capture_command",
                        lambda ff, dev, out, w, h, fps: [ff, "-hide_banner", "-loglevel", "error", "-y",
                                                         "-re", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=5:duration=0.2",
                                                         "-vf", "nonexistentfilter", str(out)])
    recorder = ScreenRecorder(source="test")
    with pytest.raises(RecorderError, match="FFmpeg"):
        recorder.start(stale)
        time.sleep(1.0)
        recorder.stop()
    assert not stale.exists()


@needs_ffmpeg
def test_recorder_test_source_records_real_time(tmp_path):
    from tutorial_ai.audio.duration import media_duration
    from tutorial_ai.recording.recorder import ScreenRecorder

    recorder = ScreenRecorder(width=320, height=180, fps=10, source="test")
    out = tmp_path / "t.mp4"
    recorder.start(out)
    time.sleep(2.0)
    elapsed = recorder.stop()
    assert 1.5 < elapsed < 4
    assert abs(media_duration(out) - elapsed) < 1.5  # wall clock == video time
