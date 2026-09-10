from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .asr import AsrResult
from .config import Settings
from .external_adapters import NagaYuMondegreenTerminology, WeTextProcessingJapaneseItn
from .processors import LimitedJapaneseItn, MondegreenTerminology, OpenAIProofreader
from .storage import AudioStore, RecordStore, Spool, validate_audio_file


class Asr(Protocol):
    def transcribe(self, audio: Path) -> AsrResult: ...


class Pipeline:
    def __init__(self, settings: Settings, asr: Asr | None = None):
        self.settings, self.asr = settings, asr
        self.store = RecordStore(settings.data_dir)
        self.spool = Spool(settings.data_dir)
        self.audio = AudioStore(settings.data_dir)
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

    def process_text(
        self,
        text: str,
        asr_result: AsrResult | None = None,
        *,
        record_id: str | None = None,
        created_at: datetime | None = None,
        audio_path: Path | None = None,
    ) -> tuple[str, Path]:
        # Never send an absent transcription to a generative processor. An LLM
        # can turn an empty prompt into plausible-looking text, which would be
        # indistinguishable from real dictation at the CLI/paste boundary.
        if not isinstance(text, str) or not text.strip():
            raise ValueError("transcription is empty")
        itn = self.itn.process(text)
        terminology = self.terminology.process(itn.output)
        llm = self.llm.process(terminology.output, terminology.protected_terms)
        now = created_at or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
        record_id = record_id or str(uuid.uuid4())
        record = {
            "id": record_id, "created_at": now.isoformat(),
            "asr": ({"provider": asr_result.provider, "model": asr_result.model, "raw": asr_result.text} if asr_result else None),
            "stages": [itn.to_dict(), terminology.to_dict(), llm.to_dict()],
            "output": llm.output, "final": None,
            "audio_path": audio_path.as_posix() if audio_path else None,
        }
        return llm.output, self.store.save(record)

    def transcribe(self, source: Path) -> tuple[str, Path]:
        if self.asr is None: raise RuntimeError("ASR is not configured")
        spooled = self.spool.put(source)
        return self.retry_file(spooled)

    def retry_file(self, spooled: Path) -> tuple[str, Path]:
        if self.asr is None: raise RuntimeError("ASR is not configured")
        validate_audio_file(spooled)
        record_id, created_at = self.spool.metadata(spooled)
        audio_path = self.audio.relative_path(record_id, created_at, spooled.suffix)
        result = self.asr.transcribe(spooled)  # On failure the durable spool is untouched.
        # Commit the record first.  If processing or record storage fails, the
        # spool remains the retry source.  The following rename is a move, not
        # a delete, so a successful audio transcription is retained permanently.
        output, record_path = self.process_text(
            result.text,
            result,
            record_id=record_id,
            created_at=created_at,
            audio_path=audio_path,
        )
        self.audio.retain(spooled, audio_path)
        return output, record_path
