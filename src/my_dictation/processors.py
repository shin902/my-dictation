from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher

from .http import json_request
from .models import Change, StageResult

_KANJI = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}
_CONTEXT = r"(?:年|月|日|時|分|秒|円|ドル|ユーロ|個|本|枚|人|件|回|度|％|%|キロ(?:グラム|メートル)?|グラム|センチ(?:メートル)?|ミリ(?:メートル)?|メートル)"


def _number(text: str) -> int:
    if not any(c in _UNITS for c in text): return int("".join(str(_KANJI[c]) for c in text))
    total = section = digit = 0
    for c in text:
        if c in _KANJI: digit = _KANJI[c]
        elif c == "万": total += (section + digit or 1) * 10000; section = digit = 0
        else: section += (digit or 1) * _UNITS[c]; digit = 0
    return total + section + digit


class LimitedJapaneseItn:
    """Small deterministic ITN; only contextual numerals are converted."""
    def process(self, text: str) -> StageResult:
        changes: list[Change] = []
        numeric_translation = str.maketrans("０１２３４５６７８９％", "0123456789%")
        normalized = text.translate(numeric_translation)
        if normalized != text: changes.append(Change(text, normalized, "fullwidth-numeric"))
        pattern = re.compile(rf"[〇零一二三四五六七八九十百千万]+(?={_CONTEXT})")
        def replace(match: re.Match[str]) -> str:
            after = str(_number(match.group()))
            changes.append(Change(match.group(), after, "contextual-japanese-number")); return after
        output = pattern.sub(replace, normalized)
        # Canonical separators are limited to fully numeric date/time expressions.
        rules = [(r"(\d{4})年(\d{1,2})月(\d{1,2})日", r"\1-\2-\3", "date"),
                 (r"(\d{1,2})時(\d{1,2})分", r"\1:\2", "time")]
        for pattern, replacement, name in rules:
            before = output; output = re.sub(pattern, replacement, output)
            if output != before: changes.append(Change(before, output, name))
        return StageResult("itn", "limited-japanese-itn", text, output, changes)


class MondegreenTerminology:
    """Conservative local terminology connector using explicit pronunciation aliases."""
    def __init__(self, terms: dict[str, list[str]], threshold: float = .94):
        self.terms, self.threshold = terms, threshold

    @staticmethod
    def _phonetic(value: str) -> str:
        return unicodedata.normalize("NFKC", value).lower().replace(" ", "").replace("・", "")

    def process(self, text: str) -> StageResult:
        output, changes, protected = text, [], []
        for canonical, aliases in self.terms.items():
            if canonical in output: protected.append(canonical); continue
            for alias in sorted(aliases, key=len, reverse=True):
                # Exact aliases are preferred. Fuzzy matching is intentionally only over
                # whitespace-delimited tokens to avoid rewriting arbitrary substrings.
                candidate = alias if alias in output else None
                if candidate is None:
                    for token in re.findall(r"[^\s、。！？]+", output):
                        if len(token) >= 4 and SequenceMatcher(None, self._phonetic(token), self._phonetic(alias)).ratio() >= self.threshold:
                            candidate = token; break
                if candidate:
                    output = output.replace(candidate, canonical, 1)
                    changes.append(Change(candidate, canonical, "mondegreen-alias")); protected.append(canonical); break
        return StageResult("terminology", "mondegreen", text, output, changes, list(dict.fromkeys(protected)))


class OpenAIProofreader:
    def __init__(self, base_url: str, api_key: str | None, model: str | None, timeout: float, temperature: float):
        self.base_url, self.api_key, self.model, self.timeout, self.temperature = base_url, api_key, model, timeout, temperature

    def process(self, text: str, protected: list[str]) -> StageResult:
        if not self.api_key or not self.model:
            return StageResult("llm", "openai-compatible", text, text, accepted=False, error="LLM is not configured")
        prompt = ("入力の意味・情報・文体を変えず、フィラー、明白な重複/言い直し/誤認識、最小限の句読点だけを修正してください。"
                  "要約、情報追加、不要な言い換えは禁止です。protected_termsは一字も変更・削除しないでください。"
                  "JSONのみを返してください: {\"text\": string, \"changes\": [{\"before\": string, \"after\": string, \"rule\": string}]}\n"
                  f"protected_terms={json.dumps(protected, ensure_ascii=False)}\ninput={json.dumps(text, ensure_ascii=False)}")
        try:
            response = json_request(self.base_url.rstrip("/") + "/chat/completions", {
                "model": self.model, "temperature": self.temperature,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": "You conservatively proofread Japanese dictation."}, {"role": "user", "content": prompt}]
            }, self.api_key, self.timeout)
            content = response["choices"][0]["message"]["content"]
            body = json.loads(content); output = body["text"]
            if not isinstance(output, str): raise ValueError("text is not a string")
            changes = [Change(str(c["before"]), str(c["after"]), str(c.get("rule", "llm"))) for c in body.get("changes", [])]
            accepted = all(term in output for term in protected)
            return StageResult("llm", "openai-compatible", text, output if accepted else text, changes, model=self.model, accepted=accepted,
                               error=None if accepted else "protected term was changed or removed")
        except Exception as exc:
            return StageResult("llm", "openai-compatible", text, text, model=self.model, accepted=False, error=str(exc))
