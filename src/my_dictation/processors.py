from __future__ import annotations

import json
import re
import unicodedata
from .http import json_request
from .models import Change, StageResult

_KANJI = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}
_CONTEXT = r"(?:年|月|日|時|分|秒|円|ドル|ユーロ|個|本|枚|人|件|回|度|％|%|キロ(?:グラム|メートル)?|グラム|センチ(?:メートル)?|ミリ(?:メートル)?|メートル)"


def _term_pattern(term: str) -> str:
    escaped = re.escape(term)
    return r"(?<![A-Za-z0-9_])" + escaped + r"(?![A-Za-z0-9_])" if term.isascii() else escaped


def _ascii_anchors_preserved(source: str, candidate: str) -> bool:
    """Keep high-risk ASCII facts while allowing small local corrections.

    Tokens containing numbers and long identifiers remain immutable. Short
    alphabetic tokens may be corrected locally (for example ``Chrome`` ->
    ``clone``), while spacing/casing normalization such as ``JSON L`` ->
    ``JSONL`` remains possible.
    """
    anchors = [token for token in re.findall(r"[A-Za-z0-9]+", source) if any(c.isdigit() for c in token) or len(token) >= 8]
    lowered, position = candidate.lower(), 0
    for anchor in anchors:
        found = lowered.find(anchor.lower(), position)
        if found < 0:
            return False
        position = found + 1
    return True


def _content_length_ratio(source: str, candidate: str) -> float:
    def content(value: str) -> str:
        return "".join(c for c in value if not unicodedata.category(c).startswith(("P", "Z")))

    original, edited = content(source), content(candidate)
    return len(edited) / len(original) if original else 1.0


def _speaker_voice_preserved(source: str, candidate: str) -> bool:
    marker = re.compile(r"俺|僕|私|わたし|あたし|自分")
    return marker.findall(source) == marker.findall(candidate)


def _protected_term_sequence(text: str, terms: object) -> list[str]:
    """Return protected occurrences in source order, with ASCII lexical boundaries."""
    matches: list[tuple[int, int, str]] = []
    for term in dict.fromkeys(terms):
        matches.extend((match.start(), match.end(), term) for match in re.finditer(_term_pattern(term), text))
    return [term for _, _, term in sorted(matches, key=lambda item: (item[0], -(item[1] - item[0])))]


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
        translation = str.maketrans("０１２３４５６７８９", "0123456789")
        pattern = re.compile(rf"([〇零一二三四五六七八九十百千万０-９0-9]+)(?={_CONTEXT})")

        def replace(match: re.Match[str]) -> str:
            before = match.group()
            translated = before.translate(translation)
            after = str(_number(translated)) if any(char in _KANJI or char in _UNITS for char in translated) else translated
            if after != before:
                changes.append(Change(before, after, "approved-context-number"))
            return after

        output = pattern.sub(replace, text)
        # Canonical separators are limited to fully numeric date/time expressions.
        rules = [(r"(\d{4})年(\d{1,2})月(\d{1,2})日", r"\1-\2-\3", "date"),
                 (r"(\d{1,2})時(\d{1,2})分", r"\1:\2", "time")]
        for pattern, replacement, name in rules:
            before = output; output = re.sub(pattern, replacement, output)
            if output != before: changes.append(Change(before, output, name))
        return StageResult("itn", "limited-japanese-itn", text, output, changes)


class MondegreenTerminology:
    """Conservative local terminology correction over kana-normalized local spans."""
    def __init__(self, terms: dict[str, list[str]], threshold: float = .8):
        self.terms, self.threshold = terms, threshold

    @staticmethod
    def _phonetic(value: str) -> str:
        value = unicodedata.normalize("NFKC", value).lower().replace(" ", "").replace("・", "")
        return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in value)

    @staticmethod
    def _distance(left: str, right: str) -> int:
        row = list(range(len(right) + 1))
        for i, a in enumerate(left, 1):
            next_row = [i]
            for j, b in enumerate(right, 1):
                next_row.append(min(next_row[-1] + 1, row[j] + 1, row[j - 1] + (a != b)))
            row = next_row
        return row[-1]

    def _matches(self, text: str, alias: str) -> list[tuple[int, int]]:
        exact = list(re.finditer(re.escape(alias), text))
        if exact:
            return [(m.start(), m.end()) for m in exact]
        target = self._phonetic(alias)
        if len(target) < 4:
            return []
        matches: list[tuple[int, int]] = []
        # Search only kana runs and alias-sized local spans, never whole arbitrary tokens.
        for run in re.finditer(r"[ぁ-ゖァ-ヶー]+", text):
            value = run.group()
            for size in range(max(4, len(alias) - 1), len(alias) + 2):
                for offset in range(0, len(value) - size + 1):
                    candidate = value[offset:offset + size]
                    phonetic = self._phonetic(candidate)
                    similarity = 1 - self._distance(phonetic, target) / max(len(phonetic), len(target))
                    if similarity >= self.threshold:
                        matches.append((run.start() + offset, run.start() + offset + size))
        # Prefer the strongest non-overlapping spans and preserve textual order.
        selected: list[tuple[int, int]] = []
        for span in sorted(matches, key=lambda s: (abs((s[1] - s[0]) - len(alias)), s[0])):
            if not any(span[0] < end and start < span[1] for start, end in selected):
                selected.append(span)
        return sorted(selected)

    def process(self, text: str) -> StageResult:
        output, changes = text, []
        for canonical, aliases in self.terms.items():
            for alias in sorted(aliases, key=len, reverse=True):
                spans = self._matches(output, alias)
                for start, end in reversed(spans):
                    before = output[start:end]
                    output = output[:start] + canonical + output[end:]
                    changes.append(Change(before, canonical, "mondegreen-alias"))
        # Keep one entry per occurrence: downstream validation must detect duplicate loss.
        protected = _protected_term_sequence(output, self.terms)
        changes.reverse()
        return StageResult("terminology", "mondegreen", text, output, changes, protected)


class OpenAIProofreader:
    def __init__(self, base_url: str, api_key: str | None, model: str | None, timeout: float, temperature: float):
        self.base_url, self.api_key, self.model, self.timeout, self.temperature = base_url, api_key, model, timeout, temperature

    def process(self, text: str, protected: list[str]) -> StageResult:
        if not self.api_key or not self.model:
            return StageResult("llm", "openai-compatible", text, text, accepted=False, error="LLM is not configured")
        prompt = ("これはリライトではなく、原文忠実性を最優先する局所校正です。変更しないことを既定にしてください。\n"
                  "【絶対禁止】情報の削除・追加、要約、意訳、語順変更、文の統合、段落の再構成、一人称・口調・敬語・文体の変更。"
                  "不明瞭または確信できない箇所は、誤っていても原文をそのまま残してください。\n"
                  "【許可】句読点の追加、明白なフィラー/直後の完全重複の削除、文脈上かなり確度が高いASR誤認識の局所置換。"
                  "特に、分割された著名な技術用語（JSON L→JSONL、SQL Lite→SQLite）や、文脈上明白な音声認識由来の英単語（Chrome→clone等）は積極的に直してください。"
                  "ただし推測だけで広げず、修正範囲は該当語句だけに限定してください。数字、単位、長い識別子、固有情報、否定、条件、程度、例示は一文字も落とさないでください。"
                  "『俺』を『私』にする等の整文は禁止です。長文でも短縮せず、全情報を同じ順序で保持してください。\n"
                  "protected_termsは出現回数と順序を含め一字も変更・削除しないでください。"
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
            # Exact sequence comparison enforces occurrence order and multiplicity. The
            # shared matcher also applies lexical boundaries to ASCII protected terms.
            actual = _protected_term_sequence(output, protected)
            violations: list[str] = []
            if actual != protected:
                violations.append("protected term occurrence sequence or span was changed")
            if not _ascii_anchors_preserved(text, output):
                violations.append("an ASCII or numeric source token was removed or reordered")
            if not _speaker_voice_preserved(text, output):
                violations.append("first-person voice was changed")
            length_ratio = _content_length_ratio(text, output)
            if not 0.9 <= length_ratio <= 1.1:
                violations.append("too much source content was removed or added")
            accepted = not violations
            reason = None if accepted else "; ".join(violations)
            return StageResult("llm", "openai-compatible", text, output if accepted else text, changes, model=self.model, accepted=accepted,
                               error=reason, candidate_output=output, rejected_output=None if accepted else output, rejection_reason=reason)
        except Exception as exc:
            reason = str(exc)
            return StageResult("llm", "openai-compatible", text, text, model=self.model, accepted=False, error=reason,
                               candidate_output="", rejected_output="", rejection_reason=reason)
