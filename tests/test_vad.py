import json
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from my_dictation.asr import AsrResult
from my_dictation.config import Settings, load_settings
from my_dictation.pipeline import Pipeline
from my_dictation.vad import VoiceActivityTrimmer


def write_pcm_wav(path: Path, samples: list[int], sample_rate: int = 1000) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(struct.pack(f"<{len(samples)}h", *samples))


class VadTests(unittest.TestCase):
    def test_trims_outer_silence_and_keeps_configured_padding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "voice.wav"
            write_pcm_wav(path, [0] * 500 + [12000] * 500 + [0] * 500)

            result = VoiceActivityTrimmer(
                backend="energy", padding_ms=100, frame_ms=20, min_speech_ms=90
            ).trim(path)

            self.assertTrue(result.applied)
            self.assertEqual(result.start_ms, 400)
            self.assertEqual(result.end_ms, 1100)
            self.assertEqual(result.original_duration_ms, 1500)
            self.assertEqual(result.trimmed_duration_ms, 700)
            with wave.open(str(path), "rb") as audio:
                self.assertEqual(audio.getnframes(), 700)

    def test_only_outer_silence_is_trimmed_internal_pause_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "voice.wav"
            write_pcm_wav(path, [0] * 300 + [12000] * 300 + [0] * 200 + [12000] * 300 + [0] * 300)

            result = VoiceActivityTrimmer(
                backend="energy", padding_ms=100, frame_ms=20, min_speech_ms=90
            ).trim(path)

            self.assertTrue(result.applied)
            with wave.open(str(path), "rb") as audio:
                samples = struct.unpack(f"<{audio.getnframes()}h", audio.readframes(audio.getnframes()))
            self.assertEqual(len(samples), 1000)
            self.assertIn(0, samples[100:-100])

    def test_webrtc_backend_uses_optional_detector_when_available(self):
        class FakeVad:
            def __init__(self, aggressiveness):
                self.aggressiveness = aggressiveness

            def is_speech(self, frame, sample_rate):
                return any(frame)

        class FakeWebRtc:
            Vad = FakeVad

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "voice.wav"
            write_pcm_wav(path, [0] * 8000 + [12000] * 8000 + [0] * 8000, sample_rate=16000)
            with patch.dict(sys.modules, {"webrtcvad": FakeWebRtc}):
                result = VoiceActivityTrimmer(
                    backend="webrtc", padding_ms=100, frame_ms=20, min_speech_ms=90
                ).trim(path)

            self.assertEqual(result.backend, "webrtc")
            self.assertIsNone(result.fallback_reason)
            self.assertTrue(result.applied)

    def test_webrtc_backend_falls_back_for_unsupported_sample_rate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "voice.wav"
            write_pcm_wav(path, [0] * 500 + [12000] * 500 + [0] * 500)

            result = VoiceActivityTrimmer(
                backend="webrtc", padding_ms=100, frame_ms=20, min_speech_ms=90
            ).trim(path)

            self.assertEqual(result.backend, "energy")
            self.assertIn("sample rate", result.fallback_reason)
            self.assertTrue(result.applied)

    def test_disabled_vad_leaves_bytes_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "voice.wav"
            write_pcm_wav(path, [0] * 500 + [12000] * 500 + [0] * 500)
            original = path.read_bytes()

            result = VoiceActivityTrimmer(enabled=False).trim(path)

            self.assertFalse(result.applied)
            self.assertEqual(result.reason, "disabled")
            self.assertEqual(path.read_bytes(), original)

    def test_unsupported_format_is_left_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "voice.ogg"
            path.write_bytes(b"not an ogg file")
            original = path.read_bytes()

            result = VoiceActivityTrimmer(backend="energy").trim(path)

            self.assertFalse(result.applied)
            self.assertEqual(result.reason, "unsupported_format")
            self.assertEqual(path.read_bytes(), original)

    def test_short_noise_is_not_archived_as_a_speech_segment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "voice.wav"
            write_pcm_wav(path, [0] * 500 + [12000] * 40 + [0] * 500)
            original = path.read_bytes()

            result = VoiceActivityTrimmer(
                backend="energy", padding_ms=100, frame_ms=20, min_speech_ms=90
            ).trim(path)

            self.assertFalse(result.applied)
            self.assertEqual(result.reason, "no_speech_detected")
            self.assertEqual(path.read_bytes(), original)


class VadPipelineTests(unittest.TestCase):
    def test_asr_and_archive_receive_vad_trimmed_audio_and_record_boundary(self):
        class InspectingAsr:
            def __init__(self):
                self.frames = None

            def transcribe(self, audio: Path) -> AsrResult:
                with wave.open(str(audio), "rb") as source:
                    self.frames = source.getnframes()
                return AsrResult("テストです", "mock", "model")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            write_pcm_wav(source, [0] * 500 + [12000] * 500 + [0] * 500)
            original = source.read_bytes()
            asr = InspectingAsr()
            settings = Settings(
                data_dir=root,
                vad_backend="energy",
                vad_padding_ms=100,
                vad_frame_ms=20,
                vad_min_speech_ms=90,
            )

            _, record_path = Pipeline(settings, asr).transcribe(source)

            self.assertEqual(asr.frames, 700)
            self.assertEqual(source.read_bytes(), original)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(record["vad"]["backend"], "energy")
            self.assertTrue(record["vad"]["applied"])
            archived = root / record["audio_path"]
            with wave.open(str(archived), "rb") as audio:
                self.assertEqual(audio.getnframes(), 700)

    def test_vad_failure_before_asr_keeps_spool_entry(self):
        class FakeAsr:
            def transcribe(self, audio: Path) -> AsrResult:
                return AsrResult("テスト", "mock", "model")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            write_pcm_wav(source, [0] * 500 + [12000] * 500 + [0] * 500)
            pipeline = Pipeline(Settings(data_dir=root, vad_backend="energy"), FakeAsr())
            spooled = pipeline.spool.put(source)
            with patch.object(pipeline.vad, "trim", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    pipeline.retry_file(spooled)
            self.assertTrue(spooled.exists())
            self.assertEqual(pipeline.spool.pending(), [spooled])


class VadConfigTests(unittest.TestCase):
    def test_loads_vad_options_from_toml_and_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            config.write_text(
                "[vad]\n"
                "enabled = false\n"
                "backend = 'energy'\n"
                "padding_ms = 450\n"
                "frame_ms = 20\n"
                "min_speech_ms = 120\n"
                "aggressiveness = 1\n",
                encoding="utf-8",
            )
            with patch.dict(
                "os.environ",
                {"MY_DICTATION_VAD_ENABLED": "true", "MY_DICTATION_VAD_PADDING_MS": "500"},
                clear=True,
            ):
                settings = load_settings(config)
            self.assertTrue(settings.vad_enabled)
            self.assertEqual(settings.vad_backend, "energy")
            self.assertEqual(settings.vad_padding_ms, 500)
            self.assertEqual(settings.vad_frame_ms, 20)
            self.assertEqual(settings.vad_min_speech_ms, 120)
            self.assertEqual(settings.vad_aggressiveness, 1)


if __name__ == "__main__":
    unittest.main()
