#!/usr/bin/env bash
# Delegación a Codex desde el autopiloto (equivalente WSL de `/codex:rescue --write [--background] [--effort]`).
#
# Por qué existe: el runtime del plugin Codex corre en Windows y no puede ejecutar el .venv
# del repo, que vive en WSL. Este wrapper lanza el mismo Codex (cuenta ChatGPT, modelo de
# ~/.codex/config.toml) dentro de WSL, en el worktree del autopiloto.
#
# Uso:
#   scripts/autopilot/codex_job.sh <job_name> <effort> <spec_file> [session_id_para_resume]
#     effort: medium | high | xhigh
#   Lanzar en background:  nohup scripts/autopilot/codex_job.sh ... >/dev/null 2>&1 &
#
# Salidas (fuera del repo, no se commitean) en $LOG_DIR/<job_name>.*:
#   .jsonl   eventos de Codex (el primero lleva el thread/session id)
#   .last.md mensaje final de Codex
#   .err     stderr
#   .exit    código de salida (existe solo cuando el trabajo ha terminado)
set -u
WT=${WT:-$HOME/code/Paletizador/09_Software/paletizado-autopilot}
MAIN_GIT=${MAIN_GIT:-$HOME/code/Paletizador/09_Software/paletizado-caotico-V2/.git}
LOG_DIR=${LOG_DIR:-$HOME/code/Paletizador/09_Software/autopilot_logs}
name=$1; effort=$2; spec=$3; sid=${4:-}
mkdir -p "$LOG_DIR"
rm -f "$LOG_DIR/$name.exit"
cd "$WT" || exit 9
if [ -n "$sid" ]; then
  codex exec resume "$sid" --full-auto -c model_reasoning_effort="$effort" \
    --json -o "$LOG_DIR/$name.last.md" - < "$spec" > "$LOG_DIR/$name.jsonl" 2> "$LOG_DIR/$name.err"
else
  codex exec --full-auto -C "$WT" --add-dir "$MAIN_GIT" -c model_reasoning_effort="$effort" \
    --json -o "$LOG_DIR/$name.last.md" - < "$spec" > "$LOG_DIR/$name.jsonl" 2> "$LOG_DIR/$name.err"
fi
echo $? > "$LOG_DIR/$name.exit"
