from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path


def _wav_has_signal(path: Path) -> bool | None:
    """Return whether a readable PCM WAV contains a non-silent sample.

    ``None`` means that the file is not a WAV format that this lightweight
    check understands; those files are left to the ASR provider to validate.
    """
    try:
        with wave.open(str(path), "rb") as audio:
            if audio.getcomptype() != "NONE":
                return None
            if audio.getnframes() == 0:
                return False
            width = audio.getsampwidth()
            if width not in {1, 2, 3, 4}:
                return None
            silence = 128 if width == 1 else 0
            while chunk := audio.readframes(4096):
                if width == 1:
                    has_signal = any(sample != silence for sample in chunk)
                else:
                    has_signal = any(chunk)
                if has_signal:
                    return True
            return False
    except (OSError, EOFError, wave.Error):
        return None


def validate_audio_file(path: Path) -> None:
    """Reject inputs that cannot contain an audio recording."""
    if not path.is_file():
        raise ValueError(f"audio file is not a regular file: {path}")
    if path.stat().st_size == 0:
        raise ValueError(f"audio file is empty: {path}")
    wav_signal = _wav_has_signal(path)
    if wav_signal is False:
        raise ValueError(f"audio file contains no audio signal: {path}")


class RecordStore:
    def __init__(self, data_dir: Path):
        self.root = data_dir / "records"

    def save(self, record: dict) -> Path:
        created = datetime.fromisoformat(record["created_at"])
        directory = self.root / created.astimezone(timezone.utc).strftime("%Y-%m-%d")
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f'{created.strftime("%H%M%S")}-{record["id"]}.json'
        fd, temporary = tempfile.mkstemp(prefix=".record-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, target)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
        return target

    def correct(self, record_path: Path, text: str) -> None:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["manual_correction"] = text
        # Preserve the existing record name while retaining atomic replacement.
        fd, temporary = tempfile.mkstemp(prefix=".record-", suffix=".tmp", dir=record_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush(); os.fsync(f.fileno())
            os.replace(temporary, record_path)
        except BaseException:
            try: os.unlink(temporary)
            except FileNotFoundError: pass
            raise


class Spool:
    def __init__(self, data_dir: Path):
        self.root = data_dir / "spool"

    def put(self, source: Path) -> Path:
        validate_audio_file(source)
        self.root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        target = self.root / f"{stamp}-{uuid.uuid4().hex}{source.suffix.lower()}"
        fd, temporary = tempfile.mkstemp(prefix=".audio-", dir=self.root)
        os.close(fd)
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, target)
        except BaseException:
            try: os.unlink(temporary)
            except FileNotFoundError: pass
            raise
        return target

    def pending(self, identifier: str | None = None) -> list[Path]:
        if not self.root.exists(): return []
        files = sorted(p for p in self.root.iterdir() if p.is_file() and not p.name.startswith("."))
        return [p for p in files if identifier is None or identifier in p.name]
