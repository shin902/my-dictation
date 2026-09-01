#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CLI=${MY_DICTATION_CLI:-"$ROOT/.venv/bin/my-dictation"}
CONFIG=${MY_DICTATION_CONFIG:-"$ROOT/config.toml"}

if (($# != 1)); then
  echo "usage: scripts/hotkey-transcribe.sh AUDIO" >&2
  exit 2
fi

AUDIO=$1
[[ -f $AUDIO ]] || { echo "error: audio file not found: $AUDIO" >&2; exit 2; }
[[ -s $AUDIO ]] || { echo "error: audio file is empty: $AUDIO" >&2; exit 2; }
[[ -x $CLI ]] || { echo "error: CLI is not executable: $CLI" >&2; exit 2; }
[[ -f $CONFIG ]] || { echo "error: config file not found: $CONFIG" >&2; exit 2; }

# Running from the repository root lets the CLI load the local .env while the
# explicit config path makes invocation independent of Hammerspoon's cwd.
cd "$ROOT"
exec "$CLI" --config "$CONFIG" transcribe "$AUDIO"
