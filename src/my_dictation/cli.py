from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .asr import GroqAsr
from .config import load_settings
from .pipeline import Pipeline


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="my-dictation")
    p.add_argument("--config", help="TOML configuration path")
    sub = p.add_subparsers(dest="command", required=True)
    transcribe = sub.add_parser("transcribe"); transcribe.add_argument("audio", type=Path)
    retry = sub.add_parser("retry"); retry.add_argument("identifier", nargs="?")
    process = sub.add_parser("process-text"); process.add_argument("text")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv); settings = load_settings(args.config)
    asr = GroqAsr(settings.groq_base_url, settings.groq_api_key, settings.groq_model, settings.timeout)
    pipeline = Pipeline(settings, asr)
    try:
        if args.command == "process-text":
            output, record = pipeline.process_text(args.text); print(output); print(f"record: {record}", file=sys.stderr)
        elif args.command == "transcribe":
            output, record = pipeline.transcribe(args.audio); print(output); print(f"record: {record}", file=sys.stderr)
        else:
            files = pipeline.spool.pending(args.identifier)
            if not files: print("no matching spooled audio", file=sys.stderr); return 1
            failed = 0
            for audio in files:
                try:
                    output, record = pipeline.retry_file(audio); print(output); print(f"record: {record}", file=sys.stderr)
                except Exception as exc:
                    failed += 1; print(f"retry failed ({audio.name}): {exc}", file=sys.stderr)
            return 1 if failed else 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__": raise SystemExit(main())
