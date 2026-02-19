#!/usr/bin/env bash
set -euo pipefail

# Ensure we run from repo root
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

python="$repo_root/.venv/bin/python"

echo "[smoke] repo_root=$repo_root"
echo "[smoke] python=$python"

if [[ ! -x "$python" ]]; then
  echo "[smoke][FAIL] Python del venv no encontrado o no ejecutable: $python"
  exit 2
fi

# Guardrail: do not allow committing python bytecode / cache
if git ls-files | grep -E '(__pycache__/|\.pyc$)' >/dev/null; then
  echo "[smoke][FAIL] Tracked Python cache files detected:"
  git ls-files | grep -E '(__pycache__/|\.pyc$)' || true
  echo "[smoke] Fix: git rm -r --cached __pycache__/ ; git rm --cached '*.pyc' (and ensure .gitignore has rules)"
  exit 2
fi

# Make src importable for local runs (no packaging assumptions)
export PYTHONPATH="${repo_root}/src${PYTHONPATH:+:${PYTHONPATH}}"

# Optional: show python info
"$python" -V || true

echo "[smoke] running: $python -m pytest -q"
"$python" -m pytest -q

echo "[smoke] OK"
