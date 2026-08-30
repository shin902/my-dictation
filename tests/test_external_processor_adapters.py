import types
import unittest
from pathlib import Path
from unittest.mock import patch

from my_dictation.external_adapters import NagaYuMondegreenTerminology, WeTextProcessingJapaneseItn


class ExternalProcessorAdapterTests(unittest.TestCase):
    def test_wetext_contract_and_restricted_spans(self):
        calls = []

        class Normalizer:
            def normalize(self, text):
                calls.append(text)
                return {"三個": "3個", "二時": "2時"}[text]

        module = types.SimpleNamespace(Normalizer=Normalizer)
        with patch("my_dictation.external_adapters.importlib.import_module", return_value=module) as imported:
            result = WeTextProcessingJapaneseItn().process("三個 一石二鳥 二時")
        imported.assert_called_once_with("tn.japanese.normalizer")
        self.assertEqual(calls, ["三個", "二時"])
        self.assertEqual(result.output, "3個 一石二鳥 2時")
        self.assertEqual(result.processor, "wetextprocessing")
        self.assertEqual(len(result.changes), 2)

    def test_wetext_absence_safely_uses_builtin(self):
        with patch("my_dictation.external_adapters.importlib.import_module", side_effect=ModuleNotFoundError):
            result = WeTextProcessingJapaneseItn().process("三個")
        self.assertEqual(result.output, "3個")
        self.assertEqual(result.processor, "limited-japanese-itn")

    def test_wetext_failure_fallback_preserves_phone_and_address(self):
        source = "電話０９０-１２３４-５６７８ 住所東京都新宿区３丁目 日付２０２４年三月五日"
        module = types.SimpleNamespace(Normalizer=lambda: (_ for _ in ()).throw(RuntimeError("failed")))
        with patch("my_dictation.external_adapters.importlib.import_module", return_value=module):
            result = WeTextProcessingJapaneseItn().process(source)
        self.assertEqual(result.output, "電話０９０-１２３４-５６７８ 住所東京都新宿区３丁目 日付2024-3-5")
        self.assertEqual(result.processor, "limited-japanese-itn")

    def test_mondegreen_contract_disables_lm_and_preserves_terms(self):
        calls = {}

        def load_glossary(path):
            calls["path"] = path
            return {"loaded": True}

        class ConstrainedCorrector:
            def __init__(self, **kwargs): calls["kwargs"] = kwargs
            def correct(self, text):
                calls["text"] = text
                return "Kubernetesを使う"

        module = types.SimpleNamespace(load_glossary=load_glossary, ConstrainedCorrector=ConstrainedCorrector)
        adapter = NagaYuMondegreenTerminology(Path("terms.csv"), {"Kubernetes": ["クバネティス"]})
        with patch("my_dictation.external_adapters.importlib.import_module", return_value=module) as imported:
            result = adapter.process("クバネティスを使う")
        imported.assert_called_once_with("mondegreen")
        self.assertEqual(calls["path"], "terms.csv")
        self.assertEqual(calls["kwargs"]["glossary"], {"loaded": True})
        self.assertIs(calls["kwargs"]["use_lm"], False)
        self.assertEqual(result.output, "Kubernetesを使う")
        self.assertEqual(result.protected_terms, ["Kubernetes"])
        self.assertTrue(result.changes)

    def test_external_mondegreen_protected_terms_follow_source_order(self):
        class ConstrainedCorrector:
            def __init__(self, **kwargs): pass
            def correct(self, text): return "Python Kubernetes Python"

        module = types.SimpleNamespace(load_glossary=lambda path: {}, ConstrainedCorrector=ConstrainedCorrector)
        adapter = NagaYuMondegreenTerminology(
            Path("terms.csv"), {"Kubernetes": ["クバネティス"], "Python": ["パイソン"]}
        )
        with patch("my_dictation.external_adapters.importlib.import_module", return_value=module):
            result = adapter.process("パイソン クバネティス パイソン")
        self.assertEqual(result.protected_terms, ["Python", "Kubernetes", "Python"])

    def test_mondegreen_failure_safely_uses_builtin(self):
        adapter = NagaYuMondegreenTerminology(Path("missing.csv"), {"Kubernetes": ["クバネティス"]})
        with patch("my_dictation.external_adapters.importlib.import_module", side_effect=ModuleNotFoundError):
            result = adapter.process("クバネティス")
        self.assertEqual(result.output, "Kubernetes")
        self.assertEqual(result.processor, "mondegreen")


if __name__ == "__main__":
    unittest.main()
