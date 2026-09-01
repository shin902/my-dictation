from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .asr import AsrResult
from .config import Settings
from .external_adapters import NagaYuMondegreenTerminology, WeTextProcessingJapaneseItn
from .processors import LimitedJapaneseItn, MondegreenTerminology, OpenAIProofreader
from .storage import RecordStore, Spool, validate_audio_file


class Asr(Protocol):
    def transcribe(self, audio: Path) -> AsrResult: ...


class Pipeline:
    def __init__(self, settings: Settings, asr: Asr | None = None):
        self.settings, self.asr = settings, asr
        self.store, self.spool = RecordStore(settings.data_dir), Spool(settings.data_dir)
        builtin_itn = LimitedJapaneseItn()
        builtin_terminology = MondegreenTerminology(settings.terminology)
        if settings.itn_backend not in {"builtin", "wetextprocessing"}:
            raise ValueError(f"unknown ITN backend: {settings.itn_backend}")
        if settings.terminology_backend not in {"builtin", "mondegreen"}:
            raise ValueError(f"unknown terminology backend: {settings.terminology_backend}")
        if settings.terminology_backend == "mondegreen" and settings.terminology_glossary is None:
            raise ValueError("processors.terminology_glossary is required for the mondegreen backend")
        self.itn = (WeTextProcessingJapaneseItn(builtin_itn)
                    if settings.itn_backend == "wetextprocessing" else builtin_itn)
        self.terminology = (NagaYuMondegreenTerminology(settings.terminology_glossary, settings.terminology,
                                                       fallback=builtin_terminology)
                            if settings.terminology_backend == "mondegreen" else builtin_terminology)
        self.llm = OpenAIProofreader(settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.timeout, settings.temperature)

    def process_text(self, text: str, asr_result: AsrResult | None = None) -> tuple[str, Path]:
        # Never send an absent transcription to a generative processor. An LLM
        # can turn an empty prompt into plausible-looking text, which would be
        # indistinguishable from real dictation at the CLI/paste boundary.
        if not isinstance(text, str) or not text.strip():
            raise ValueError("transcription is empty")
        itn = self.itn.process(text)
        terminology = self.terminology.process(itn.output)
        llm = self.llm.process(terminology.output, terminology.protected_terms)
        now, record_id = datetime.now(timezone.utc), str(uuid.uuid4())
        record = {
            "id": record_id, "created_at": now.isoformat(),
            "asr": ({"provider": asr_result.provider, "model": asr_result.model, "raw": asr_result.text} if asr_result else None),
            "stages": [itn.to_dict(), terminology.to_dict(), llm.to_dict()],
            "output": llm.output, "manual_correction": None,
        }
        return llm.output, self.store.save(record)

    def transcribe(self, source: Path) -> tuple[str, Path]:
        if self.asr is None: raise RuntimeError("ASR is not configured")
        spooled = self.spool.put(source)
        return self.retry_file(spooled)

    def retry_file(self, spooled: Path) -> tuple[str, Path]:
        if self.asr is None: raise RuntimeError("ASR is not configured")
        validate_audio_file(spooled)
        result = self.asr.transcribe(spooled)  # On failure the durable spool is untouched.
        output, record_path = self.process_text(result.text, result)
        # Record has been atomically committed before the only audio copy is removed.
        spooled.unlink()
        return output, record_path
