#!/usr/bin/env bash
set -euo pipefail

# Ensure we run from repo root
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[smoke] repo_root=$ROOT_DIR"

# Guardrail: do not allow committing python bytecode / cache
if git ls-files | grep -E '(__pycache__/|\.pyc$)' >/dev/null; then
  echo "[smoke][FAIL] Tracked Python cache files detected:"
  git ls-files | grep -E '(__pycache__/|\.pyc$)' || true
  echo "[smoke] Fix: git rm -r --cached __pycache__/ ; git rm --cached '*.pyc' (and ensure .gitignore has rules)"
  exit 2
fi

# Make src importable for local runs (no packaging assumptions)
export PYTHONPATH="${ROOT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"

# Optional: show python info
if command -v python >/dev/null; then
  echo "[smoke] python=$(command -v python)"
  python -V || true
fi

echo "[smoke] running: pytest -q"
pytest -q

echo "[smoke] OK"
