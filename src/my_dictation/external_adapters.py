from __future__ import annotations

import importlib
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .models import Change, StageResult
from .processors import LimitedJapaneseItn, MondegreenTerminology, _CONTEXT, _term_pattern


class WeTextProcessingJapaneseItn:
    """Isolated adapter for WeTextProcessing's Japanese ``Normalizer`` API.

    Only spans in the project's approved numeric categories are submitted to the
    external normalizer. Import or processing failures return the builtin result.
    """

    def __init__(self, fallback: LimitedJapaneseItn | None = None):
        self.fallback = fallback or LimitedJapaneseItn()

    def process(self, text: str) -> StageResult:
        try:
            module = importlib.import_module("tn.japanese.normalizer")
            normalizer = module.Normalizer()
            # Include the category suffix so WeText can disambiguate the number.
            pattern = re.compile(rf"[〇零一二三四五六七八九十百千万０-９0-9]+{_CONTEXT}")
            changes: list[Change] = []

            def replace(match: re.Match[str]) -> str:
                before = match.group()
                after = normalizer.normalize(before)
                if not isinstance(after, str):
                    raise TypeError("WeTextProcessing Normalizer.normalize() did not return str")
                if after != before:
                    changes.append(Change(before, after, "wetextprocessing-restricted"))
                return after

            output = pattern.sub(replace, text)
            return StageResult("itn", "wetextprocessing", text, output, changes)
        except Exception:
            return self.fallback.process(text)


class NagaYuMondegreenTerminology:
    """Adapter for NagaYu/mondegreen, explicitly disabling its LM reranker."""

    def __init__(self, glossary_path: Path, terms: dict[str, list[str]], threshold: float = .8,
                 fallback: MondegreenTerminology | None = None):
        self.glossary_path = glossary_path
        self.terms = terms
        self.threshold = threshold
        self.fallback = fallback or MondegreenTerminology(terms, threshold)

    @staticmethod
    def _output(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, tuple) and value and isinstance(value[0], str):
            return value[0]
        for name in ("text", "corrected_text", "output"):
            candidate = getattr(value, name, None)
            if isinstance(candidate, str):
                return candidate
        raise TypeError("ConstrainedCorrector.correct() returned an unsupported value")

    def process(self, text: str) -> StageResult:
        try:
            module = importlib.import_module("mondegreen")
            glossary = module.load_glossary(str(self.glossary_path))
            corrector = module.ConstrainedCorrector(glossary=glossary, threshold=self.threshold, use_lm=False)
            output = self._output(corrector.correct(text))
            changes: list[Change] = []
            for opcode, i1, i2, j1, j2 in SequenceMatcher(None, text, output).get_opcodes():
                if opcode != "equal":
                    changes.append(Change(text[i1:i2], output[j1:j2], "mondegreen-constrained"))
            protected: list[str] = []
            for canonical in sorted(self.terms, key=len, reverse=True):
                protected.extend([canonical] * len(list(re.finditer(_term_pattern(canonical), output))))
            return StageResult("terminology", "mondegreen-constrained", text, output, changes, protected)
        except Exception:
            return self.fallback.process(text)
