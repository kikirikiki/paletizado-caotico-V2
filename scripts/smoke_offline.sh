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

tmp_json="$(mktemp /tmp/palca-smoke-height-slack-XXXXXX.json)"
echo "[smoke] running height-slack scenario (output=$tmp_json)"
"$python" -m sim.run \
  --excel "data/Flujo_smoke_60.xlsx" \
  --model M1 \
  --n_per_pallet 999999 \
  --t_pick_place 14.0 \
  --staging_cap 0 \
  --policy palca \
  --k 15 \
  --stability-mode ratio+corners+settle \
  --min-support 0.90 \
  --overhang_mm 20 \
  --force-destination 1 \
  --continuous-pallets \
  --arrival-mode immediate \
  --time-budget-ms 900 \
  --score-mode min_height_slack_then_gain \
  --height-slack-mm 80 \
  --micro-plan \
  --micro-depth 3 \
  --micro-width 8 \
  --micro-topk 15 \
  --out "$tmp_json"
rm -f "$tmp_json"

echo "[smoke] OK"
