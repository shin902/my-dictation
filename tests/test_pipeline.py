import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from my_dictation.asr import AsrResult
from my_dictation.config import Settings
from my_dictation.pipeline import Pipeline
from my_dictation.processors import LimitedJapaneseItn, MondegreenTerminology, OpenAIProofreader
from my_dictation.storage import RecordStore


class FakeAsr:
    def __init__(self, fail=False): self.fail = fail
    def transcribe(self, audio):
        if self.fail: raise RuntimeError("offline")
        return AsrResult("二千二十四年三月五日 クバネティス", "groq", "mock")


class ProcessorTests(unittest.TestCase):
    def test_limited_itn_and_non_target(self):
        result = LimitedJapaneseItn().process("二千二十四年三月五日 十二時三十分 一石二鳥")
        self.assertEqual(result.output, "2024-3-5 12:30 一石二鳥")
        self.assertTrue(result.changes)

    def test_builtin_itn_only_normalizes_approved_spans_not_phone_or_address(self):
        source = "電話は０９０-１２３４-５６７８、住所は東京都新宿区３丁目、日付は２０２４年三月五日"
        result = LimitedJapaneseItn().process(source)
        self.assertEqual(result.output, "電話は０９０-１２３４-５６７８、住所は東京都新宿区３丁目、日付は2024-3-5")

    def test_terminology_kana_fuzzy_all_occurrences_and_non_target(self):
        processor = MondegreenTerminology({"Kubernetes": ["クバネティス"]})
        result = processor.process("くばねてぃすとクバネテスを使う 普通の文章")
        self.assertEqual(result.output, "KubernetesとKubernetesを使う 普通の文章")
        self.assertEqual(result.protected_terms, ["Kubernetes", "Kubernetes"])
        self.assertEqual(len(result.changes), 2)
        self.assertEqual(processor.process("普通の文章").output, "普通の文章")

    def test_builtin_terminology_protected_terms_follow_source_order(self):
        processor = MondegreenTerminology({"Kubernetes": ["クバネティス"], "Python": ["パイソン"]})
        result = processor.process("パイソン、クバネティス、パイソン")
        self.assertEqual(result.protected_terms, ["Python", "Kubernetes", "Python"])

    @patch("my_dictation.processors.json_request")
    def test_llm_protected_term_violation_falls_back(self, request):
        request.return_value = {"choices": [{"message": {"content": '{"text":"別物を使う","changes":[]}'}}]}
        result = OpenAIProofreader("http://mock", "key", "model", 1, 0).process("Kubernetesを使う", ["Kubernetes"])
        self.assertFalse(result.accepted); self.assertEqual(result.output, "Kubernetesを使う")
        self.assertEqual(result.rejected_output, "別物を使う"); self.assertTrue(result.rejection_reason)

    @patch("my_dictation.processors.json_request")
    def test_llm_protection_checks_occurrence_count_and_not_substrings(self, request):
        proofreader = OpenAIProofreader("http://mock", "key", "model", 1, 0)
        request.return_value = {"choices": [{"message": {"content": '{"text":"Kubernetes","changes":[]}'}}]}
        duplicate = proofreader.process("Kubernetes Kubernetes", ["Kubernetes", "Kubernetes"])
        self.assertFalse(duplicate.accepted)
        request.return_value = {"choices": [{"message": {"content": '{"text":"Kubernetes","changes":[]}'}}]}
        substring = proofreader.process("Kube", ["Kube"])
        self.assertFalse(substring.accepted)

    @patch("my_dictation.processors.json_request")
    def test_llm_rejects_protected_term_order_change(self, request):
        request.return_value = {"choices": [{"message": {"content": '{"text":"Kubernetes Python","changes":[]}'}}]}
        result = OpenAIProofreader("http://mock", "key", "model", 1, 0).process(
            "Python Kubernetes", ["Python", "Kubernetes"]
        )
        self.assertFalse(result.accepted)
        self.assertEqual(result.output, "Python Kubernetes")

    @patch("my_dictation.processors.json_request", side_effect=TimeoutError("timeout"))
    def test_llm_api_failure_falls_back(self, request):
        result = OpenAIProofreader("http://mock", "key", "model", 1, 0).process("安全", [])
        self.assertFalse(result.accepted); self.assertEqual(result.output, "安全")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.settings = Settings(data_dir=self.root, terminology={"Kubernetes": ["クバネティス"]})
        self.audio = self.root / "input.wav"; self.audio.write_bytes(b"RIFFmock")
    def tearDown(self): self.temp.cleanup()

    def test_success_commits_record_then_removes_audio(self):
        pipeline = Pipeline(self.settings, FakeAsr())
        output, path = pipeline.transcribe(self.audio)
        self.assertEqual(output, "2024-3-5 Kubernetes")
        self.assertEqual(pipeline.spool.pending(), [])
        record = json.loads(path.read_text())
        self.assertEqual(record["asr"]["raw"], "二千二十四年三月五日 クバネティス")
        self.assertEqual([s["name"] for s in record["stages"]], ["itn", "terminology", "llm"])
        self.assertNotIn("api_key", path.read_text())

    def test_asr_failure_leaves_spool_for_retry(self):
        pipeline = Pipeline(self.settings, FakeAsr(True))
        with self.assertRaises(RuntimeError): pipeline.transcribe(self.audio)
        self.assertEqual(len(pipeline.spool.pending()), 1)

    def test_record_failure_does_not_remove_spool(self):
        pipeline = Pipeline(self.settings, FakeAsr())
        spooled = pipeline.spool.put(self.audio)
        with patch.object(pipeline.store, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError): pipeline.retry_file(spooled)
        self.assertTrue(spooled.exists())

    def test_atomic_record_and_manual_correction(self):
        store = RecordStore(self.root)
        record = {"id": "id", "created_at": "2024-01-02T03:04:05+00:00", "manual_correction": None}
        path = store.save(record)
        self.assertFalse(any(p.suffix == ".tmp" for p in path.parent.iterdir()))
        store.correct(path, "修正文")
        self.assertEqual(json.loads(path.read_text())["manual_correction"], "修正文")


if __name__ == "__main__": unittest.main()
