#!/usr/bin/env bash
set -euo pipefail

# Run any command with the project venv activated and PYTHONPATH set.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [[ ! -d ".venv" ]]; then
  echo "[dev_shell_cmd] ERROR: .venv not found in: $repo_root" >&2
  echo "[dev_shell_cmd] Create it with:" >&2
  echo "  python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && pip install pytest" >&2
  exit 2
fi

# shellcheck disable=SC1091
source "$repo_root/.venv/bin/activate"
export PYTHONPATH="$repo_root/src${PYTHONPATH:+:$PYTHONPATH}"

if [[ $# -lt 1 ]]; then
  echo "Usage: $(basename "$0") <command...>" >&2
  echo "Example: $(basename "$0") ./scripts/smoke_offline.sh" >&2
  exit 2
fi

exec "$@"
