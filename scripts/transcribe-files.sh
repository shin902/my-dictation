#!/usr/bin/env bash
set -uo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CLI=${MY_DICTATION_CLI:-"$ROOT/.venv/bin/my-dictation"}
PYTHON=${MY_DICTATION_PYTHON:-"$ROOT/.venv/bin/python"}
delay=${MY_DICTATION_BATCH_DELAY:-0}
list_file=
inputs=()

usage() {
  cat >&2 <<'EOF'
Usage:
  scripts/transcribe-files.sh [--delay SECONDS] AUDIO...
  scripts/transcribe-files.sh [--delay SECONDS] --list FILE
  printf '%s\n' AUDIO... | scripts/transcribe-files.sh [--delay SECONDS]

The CLI's generated stdout is discarded. This script prints one JSON array to
stdout containing only each saved record's final output and LLM status.
EOF
}

while (($#)); do
  case $1 in
    --delay)
      [[ $# -ge 2 ]] || { echo "error: --delay requires a value" >&2; exit 2; }
      delay=$2
      shift 2
      ;;
    --list)
      [[ $# -ge 2 ]] || { echo "error: --list requires a file" >&2; exit 2; }
      list_file=$2
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    --)
      shift
      while (($#)); do inputs+=("$1"); shift; done
      ;;
    *)
      inputs+=("$1")
      shift
      ;;
  esac
done

[[ $delay =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo "error: invalid delay: $delay" >&2; exit 2; }
if [[ -n $list_file ]]; then
  ((${#inputs[@]} == 0)) || { echo "error: --list cannot be combined with AUDIO arguments" >&2; exit 2; }
  [[ -f $list_file ]] || { echo "error: list file not found: $list_file" >&2; exit 2; }
  while IFS= read -r line || [[ -n $line ]]; do
    [[ -z $line || $line == \#* ]] || inputs+=("$line")
  done < "$list_file"
elif ((${#inputs[@]} == 0)) && [[ ! -t 0 ]]; then
  while IFS= read -r line || [[ -n $line ]]; do
    [[ -z $line || $line == \#* ]] || inputs+=("$line")
  done
fi

((${#inputs[@]} > 0)) || { usage; exit 2; }
[[ -x $CLI ]] || { echo "error: CLI is not executable: $CLI" >&2; exit 2; }
[[ -x $PYTHON ]] || { echo "error: Python is not executable: $PYTHON" >&2; exit 2; }

tmp=$(mktemp -d "${TMPDIR:-/tmp}/my-dictation-batch.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
failed=0
index=0

for input in "${inputs[@]}"; do
  index=$((index + 1))
  audio=$($PYTHON - "$input" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).expanduser().resolve())
PY
)
  error_log="$tmp/error-$index.log"
  echo "transcribing: $audio" >&2
  (cd "$ROOT" && "$CLI" transcribe "$audio" >/dev/null 2>"$error_log")
  status=$?
  record=$(awk '/^record: / { sub(/^record: /, ""); print; exit }' "$error_log")
  if [[ -n $record && $record != /* ]]; then record="$ROOT/$record"; fi
  error=$(tr '\n' ' ' < "$error_log")
  if ((status != 0)) || [[ -z $record || ! -f $record ]]; then
    failed=$((failed + 1))
    echo "failed: $audio${error:+ ($error)}" >&2
  fi
  AUDIO=$audio RECORD=$record STATUS=$status ERROR_TEXT=$error \
    "$PYTHON" - "$tmp/result-$index.json" <<'PY'
import json
import os
import sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    "audio": os.environ["AUDIO"],
    "record": os.environ["RECORD"],
    "status": int(os.environ["STATUS"]),
    "error": os.environ["ERROR_TEXT"],
}, ensure_ascii=False), encoding="utf-8")
PY
  if ((index < ${#inputs[@]})) && [[ $delay != 0 ]]; then sleep "$delay"; fi
done

"$PYTHON" - "$tmp" <<'PY'
import json
import sys
from pathlib import Path

results = []
for metadata_path in sorted(Path(sys.argv[1]).glob("result-*.json"), key=lambda p: int(p.stem.split("-")[-1])):
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    item = {"audio": metadata["audio"], "record": metadata["record"] or None}
    record_path = Path(metadata["record"]) if metadata["record"] else None
    if metadata["status"] == 0 and record_path and record_path.is_file():
        record = json.loads(record_path.read_text(encoding="utf-8"))
        llm = next((stage for stage in record.get("stages", []) if stage.get("name") == "llm"), {})
        item.update({
            "output": record.get("output", ""),
            "llm_accepted": llm.get("accepted"),
            "llm_model": llm.get("model"),
            "llm_error": llm.get("error"),
        })
    else:
        item.update({"output": None, "llm_accepted": False, "llm_model": None, "llm_error": metadata["error"]})
    results.append(item)
print(json.dumps(results, ensure_ascii=False, indent=2))
PY

exit "$failed"
