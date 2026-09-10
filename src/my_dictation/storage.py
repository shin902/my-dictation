from __future__ import annotations

import json
import os
import re
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

    def set_final(self, record_path: Path, text: str) -> None:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record.pop("manual_correction", None)
        record["final"] = text
        # Preserve the existing record name while retaining atomic replacement.
        fd, temporary = tempfile.mkstemp(prefix=".record-", suffix=".tmp", dir=record_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, record_path)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise


_SPOOL_TIMESTAMP = re.compile(r"^\d{8}T\d{6}$")


def _fallback_spool_metadata(spooled: Path) -> tuple[str, datetime]:
    """Keep manually placed spool files stable across retry attempts."""
    identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, f"my-dictation-spool:{spooled.name}"))
    try:
        created = datetime.fromtimestamp(spooled.stat().st_mtime, timezone.utc)
    except OSError:
        created = datetime.now(timezone.utc)
    return identifier, created


class Spool:
    def __init__(self, data_dir: Path):
        self.root = data_dir / "spool"

    def put(
        self,
        source: Path,
        record_id: str | None = None,
        created_at: datetime | None = None,
    ) -> Path:
        validate_audio_file(source)
        self.root.mkdir(parents=True, exist_ok=True)
        created = created_at or datetime.now(timezone.utc)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        created = created.astimezone(timezone.utc)
        identifier = record_id or str(uuid.uuid4())
        stamp = created.strftime("%Y%m%dT%H%M%S")
        target = self.root / f"{stamp}-{identifier}{source.suffix}"
        fd, temporary = tempfile.mkstemp(prefix=".audio-", dir=self.root)
        os.close(fd)
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, target)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
        return target

    def metadata(self, spooled: Path) -> tuple[str, datetime]:
        """Return the stable record ID and creation time encoded in a spool path.

        Current spool names use ``<UTC timestamp>-<record ID>.<extension>``.
        The fallback keeps older or manually placed spool files retryable with
        a stable derived ID and filesystem modification time.
        """
        stamp, separator, remainder = spooled.name.partition("-")
        if not separator or not remainder or not _SPOOL_TIMESTAMP.fullmatch(stamp):
            return _fallback_spool_metadata(spooled)
        extension = spooled.suffix
        identifier = remainder[:-len(extension)] if extension else remainder
        if not identifier:
            return _fallback_spool_metadata(spooled)
        try:
            created = datetime.strptime(stamp, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            _, created = _fallback_spool_metadata(spooled)
        return identifier, created

    def pending(self, identifier: str | None = None) -> list[Path]:
        if not self.root.exists(): return []
        files = sorted(p for p in self.root.iterdir() if p.is_file() and not p.name.startswith("."))
        return [p for p in files if identifier is None or identifier in p.name]


class AudioStore:
    """Move successful spool entries into their durable audio archive."""

    def __init__(self, data_dir: Path):
        self.root = data_dir / "audio"

    def relative_path(self, record_id: str, created_at: datetime, extension: str) -> Path:
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        date = created_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
        return Path("audio") / date / f"{record_id}{extension}"

    def retain(self, spooled: Path, relative_path: Path) -> Path:
        """Atomically move a spool file into the data-dir-relative archive.

        The record is written before this method is called.  If the move fails,
        the source remains in spool for retry; on success there is no delete-only
        operation and the retained archive is the sole audio copy.
        """
        if relative_path.is_absolute() or not relative_path.parts or relative_path.parts[0] != "audio":
            raise ValueError(f"audio path must be relative to the data directory: {relative_path}")
        target = self.root / Path(*relative_path.parts[1:])
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(f"audio archive already exists: {target}")
        os.replace(spooled, target)
        return target
