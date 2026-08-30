from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .asr import AsrResult
from .config import Settings
from .processors import LimitedJapaneseItn, MondegreenTerminology, OpenAIProofreader
from .storage import RecordStore, Spool


class Asr(Protocol):
    def transcribe(self, audio: Path) -> AsrResult: ...


class Pipeline:
    def __init__(self, settings: Settings, asr: Asr | None = None):
        self.settings, self.asr = settings, asr
        self.store, self.spool = RecordStore(settings.data_dir), Spool(settings.data_dir)
        self.itn = LimitedJapaneseItn()
        self.terminology = MondegreenTerminology(settings.terminology)
        self.llm = OpenAIProofreader(settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.timeout, settings.temperature)

    def process_text(self, text: str, asr_result: AsrResult | None = None) -> tuple[str, Path]:
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
        result = self.asr.transcribe(spooled)  # On failure the durable spool is untouched.
        output, record_path = self.process_text(result.text, result)
        # Record has been atomically committed before the only audio copy is removed.
        spooled.unlink()
        return output, record_path
