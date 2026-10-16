#!/usr/bin/env bash
# Delegación a Codex desde el autopiloto (equivalente WSL de `/codex:rescue --write [--background] [--effort]`).
#
# Por qué existe: el runtime del plugin Codex corre en Windows y no puede ejecutar el .venv
# del repo, que vive en WSL. Este wrapper lanza Codex (cuenta ChatGPT) dentro de WSL,
# en el worktree del autopiloto. Ver docs/autopilot/DECISIONS.md D-001.
#
# Uso:
#   scripts/autopilot/codex_job.sh <job_name> <model> <effort> <spec_file> [session_id_para_resume]
#     model:  gpt-6.1-sol (implementación) | gpt-6-astra (algoritmos difíciles, revisiones adversariales)
#     effort: low | medium | high | xhigh | max
#
# Salidas (fuera del repo, no se commitean) en $LOG_DIR/<job_name>.*:
#   .jsonl   eventos de Codex (el primero lleva el thread id, útil para resume)
#   .last.md mensaje final de Codex
#   .err     stderr
#   .exit    código de salida (existe solo cuando el trabajo ha terminado)
set -u
CODEX=${CODEX:-$HOME/.local/bin/codex}
WT=${WT:-$HOME/code/Paletizador/09_Software/paletizado-autopilot}
MAIN_GIT=${MAIN_GIT:-$HOME/code/Paletizador/09_Software/paletizado-caotico-V2/.git}
LOG_DIR=${LOG_DIR:-$HOME/code/Paletizador/09_Software/autopilot_logs}
name=$1; model=$2; effort=$3; spec=$4; sid=${5:-}
mkdir -p "$LOG_DIR"
rm -f "$LOG_DIR/$name.exit"
cd "$WT" || exit 9
if [ -n "$sid" ]; then
  "$CODEX" exec resume "$sid" -c sandbox_mode='"workspace-write"' -m "$model" -c model_reasoning_effort="$effort" \
    --json -o "$LOG_DIR/$name.last.md" - < "$spec" > "$LOG_DIR/$name.jsonl" 2> "$LOG_DIR/$name.err"
else
  "$CODEX" exec -s workspace-write -C "$WT" --add-dir "$MAIN_GIT" -m "$model" -c model_reasoning_effort="$effort" \
    --json -o "$LOG_DIR/$name.last.md" - < "$spec" > "$LOG_DIR/$name.jsonl" 2> "$LOG_DIR/$name.err"
fi
echo $? > "$LOG_DIR/$name.exit"
