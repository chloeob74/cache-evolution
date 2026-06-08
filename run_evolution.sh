#!/usr/bin/env bash
# Launch OpenEvolve with the Anthropic key sourced from a protected file,
# so the key does NOT need to live in the global (Claude Code) environment.
#
# Usage: ./run_evolution.sh [ITERATIONS]   (default: 50)
set -euo pipefail

KEY_FILE="$HOME/.config/openevolve/anthropic_api_key"
ITERATIONS="${1:-50}"
VENV_BIN="/home/ubuntu/ds5110_project/bin"

if [[ ! -r "$KEY_FILE" ]]; then
  echo "ERROR: key file not found at $KEY_FILE" >&2
  exit 1
fi

cd "$(dirname "$0")"

# Key is scoped to this command only — not exported to your shell session.
ANTHROPIC_API_KEY="$(cat "$KEY_FILE")" \
  "$VENV_BIN/openevolve-run" \
  initial_program.py evaluate.py \
  --config config.yaml \
  --iterations "$ITERATIONS" \
  --log-level INFO
