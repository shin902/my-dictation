from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


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
