import sys
import wave
from pathlib import Path

import pytest
from conftest import FakeProvider, make_plan, make_sections

from tutorial_ai.audio.duration import wav_duration
from tutorial_ai.audio.generator import NarrationGenerator, NarrationManifest
from tutorial_ai.models.script import ScriptBeat, ScriptSection, TutorialScript
from tutorial_ai.providers.tts.base import AudioResult, TTSProvider
from tutorial_ai.tutorial.scene_builder import build_scenes


def _write_silence(path: Path, seconds: float, rate: int = 24_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\0\0" * int(seconds * rate))


class FakeTTS(TTSProvider):
    name = "fake"

    def __init__(self, voice: str = "v1") -> None:
        self.voice = voice
        self.calls: list[str] = []

    def synthesize(self, text: str, output_path: Path) -> AudioResult:
        self.calls.append(text)
        seconds = 0.1 * len(text.split())  # 0.1 s per word
        _write_silence(output_path, seconds)
        return AudioResult(path=output_path, duration_seconds=seconds, sample_rate=24_000)


def _scenes():
    sections = []
    for number, draft in make_sections().items():
        beats = [ScriptBeat(id=f"s{number:02d}_b{i:02d}", **b.model_dump()) for i, b in enumerate(draft.beats, 1)]
        sections.append(ScriptSection(section_number=number, title=make_plan().section(number).title,
                                      beats=beats, state_summary=draft.state_summary))
    return build_scenes(TutorialScript(title="T", sections=sections), make_plan())



def test_generator_writes_one_file_per_segment_and_records_durations(tmp_path):
    scenes = _scenes()
    tts = FakeTTS()
    manifest = NarrationGenerator(tts, tmp_path).generate(scenes)
    segments = [seg for s in scenes.scenes for seg in s.narration_segments]
    assert len(manifest.entries) == len(segments) == len(tts.calls)
    for entry, segment in zip(manifest.entries, segments):
        assert (tmp_path / entry.audio_path).exists()
        assert entry.audio_path.startswith("audio/scene_")
        assert segment.audio_path == entry.audio_path
        assert segment.measured_duration_seconds == entry.duration_seconds
        assert wav_duration(tmp_path / entry.audio_path)[0] == pytest.approx(entry.duration_seconds, abs=0.01)


def test_generator_reuses_unchanged_audio_and_regenerates_changed_text(tmp_path):
    scenes = _scenes()
    first = NarrationGenerator(FakeTTS(), tmp_path).generate(scenes)

    scenes.scenes[0].narration_segments[0].text = "A different opening line."
    tts = FakeTTS()
    second = NarrationGenerator(tts, tmp_path).generate(scenes, existing=first)
    assert tts.calls == ["A different opening line."]
    assert len(second.entries) == len(first.entries)

    # a different voice regenerates everything
    tts = FakeTTS(voice="v2")
    NarrationGenerator(tts, tmp_path).generate(scenes, existing=second)
    assert len(tts.calls) == len(second.entries)


def test_manifest_round_trips(tmp_path):
    manifest = NarrationGenerator(FakeTTS(), tmp_path).generate(_scenes())
    assert NarrationManifest.model_validate_json(manifest.model_dump_json()) == manifest


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS `say` only")
def test_macos_say_produces_wav_with_measured_duration(tmp_path):
    from tutorial_ai.providers.tts.macos_say import MacOSSayProvider

    result = MacOSSayProvider().synthesize("Alright, let's start by creating the project.", tmp_path / "a.wav")
    assert result.path.exists() and result.sample_rate == 24_000
    assert 1.5 < result.duration_seconds < 6


def test_gemini_tts_writes_pcm_as_wav(tmp_path):
    from types import SimpleNamespace

    from google.genai import types

    from tutorial_ai.providers.tts.gemini_tts import GeminiTTSProvider

    pcm = b"\0\0" * 24_000  # one second of silence
    response = types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(
        role="model", parts=[types.Part(inline_data=types.Blob(data=pcm, mime_type="audio/pcm"))]))])
    calls = []

    class Models:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            return response

    provider = GeminiTTSProvider("k", "tts-model", client=SimpleNamespace(models=Models()))
    result = provider.synthesize("Hello there.", tmp_path / "g.wav")
    assert result.duration_seconds == pytest.approx(1.0)
    assert calls[0]["config"].response_modalities == ["AUDIO"]
    assert "Hello there." in calls[0]["contents"]


def test_wav_duration_ignores_streaming_placeholder_header(tmp_path):
    import struct

    rate, seconds = 24_000, 1.5
    pcm = b"\0\0" * int(rate * seconds)
    # A header claiming 0xFFFFFFFF bytes of data, as streamed WAV encoders write it.
    header = b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    header += b"data" + struct.pack("<I", 0xFFFFFFFF)
    path = tmp_path / "streamed.wav"
    path.write_bytes(header + pcm)
    duration, measured_rate = wav_duration(path)
    assert duration == pytest.approx(seconds, abs=0.01) and measured_rate == rate
