#!/usr/bin/env bash
# Estado del autopiloto en ~10 líneas (D-038: el director lee resúmenes, no logs).
# Uso: bash scripts/autopilot/status.sh
set -u
WT=$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)
LOGS=${CODEX_JOB_LOGS:-$HOME/code/Paletizador/09_Software/autopilot_logs}
cd "$WT"
echo "[$(date '+%F %H:%M')] $(git branch --show-current) @ $(git log -1 --format='%h %s' | cut -c1-70)"
echo "codex: $(bash scripts/autopilot/codex_quota.sh check 2>/dev/null)"
last=$(ls -t "$LOGS"/*.jsonl 2>/dev/null | head -1)
if [ -n "$last" ]; then
  j=$(basename "$last" .jsonl)
  echo "último job: $j exit=$(cat "$LOGS/$j.exit" 2>/dev/null || echo 'en curso') ($(date -r "$last" '+%H:%M'))"
fi
run=""
for pat in "codex exec" "run_sweep.py" "smoke_offline" "scripts/train"; do
  n=$(pgrep -fc -- "^[^ ]*(python|node|bash)[^ ]* .*$pat" 2>/dev/null || true)
  [ "${n:-0}" -gt 0 ] && run="$run $pat×$n"
done
echo "en marcha: ${run:-nada}"
for s in $(ls -td out/sweeps/*/ 2>/dev/null | head -2); do
  n=$(basename "$s")
  best=$(grep -E '^\| [a-z0-9_]+ \(' "$s/summary.md" 2>/dev/null | awk -F'|' '{gsub(/ /,"",$2); gsub(/^ +| +$/,"",$4); print $2"="$4}' | head -4 | paste -sd' ')
  echo "sweep $n: ${best:-sin resumen}"
done
command -v nvidia-smi >/dev/null && echo "gpu: $(nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader 2>/dev/null)"
echo "carga: $(cut -d' ' -f1-3 /proc/loadavg)"
