#!/usr/bin/env bash
# Cuota semanal de Codex (regla del usuario D-027: no pasar del 60 %).
#
# Lee el último evento `rate_limits` de las sesiones de Codex (~/.codex/sessions). "secondary" es la ventana semanal
# (10080 min) y "primary" la de 5 h. Si la ventana ya se reinició (resets_at pasado), cuenta como 0 %.
#
# Uso:
#   codex_quota.sh check            imprime "weekly=<pct> 5h=<pct>"; sale con 3 si weekly >= LIMIT
#   codex_quota.sh watch [segundos] guardián: cada N s (60) comprueba y, si weekly >= LIMIT, mata los `codex exec`
#                                   en marcha (el trabajo queda reanudable con su thread id) y termina.
set -u
LIMIT=${CODEX_WEEKLY_LIMIT:-60}
SESS=${CODEX_SESSIONS:-$HOME/.codex/sessions}

read_pct() {
  local f now line
  f=$(ls -t "$SESS"/*/*/*/*.jsonl 2>/dev/null | head -1)
  [ -n "$f" ] || { echo "0 0"; return; }
  line=$(grep -o '"rate_limits":{[^}]*}[^}]*}[^}]*}' "$f" | tail -1)
  now=$(date +%s)
  python3 - "$line" "$now" <<'EOF'
import json, sys
line, now = sys.argv[1], int(sys.argv[2])
try:
    rl = json.loads("{" + line + "}}")["rate_limits"]
except Exception:
    print("0 0"); sys.exit()
def pct(w):
    if not w: return 0.0
    return 0.0 if int(w.get("resets_at") or 0) <= now else float(w.get("used_percent") or 0)
print(f"{pct(rl.get('secondary')):.0f} {pct(rl.get('primary')):.0f}")
EOF
}

case "${1:-check}" in
  check)
    read -r wk h5 < <(read_pct)
    echo "weekly=$wk 5h=$h5 limit=$LIMIT"
    [ "$wk" -ge "$LIMIT" ] && exit 3
    exit 0 ;;
  watch)
    every=${2:-60}
    while true; do
      read -r wk h5 < <(read_pct)
      if [ "$wk" -ge "$LIMIT" ]; then
        echo "$(date '+%F %H:%M') weekly=$wk >= $LIMIT: deteniendo codex exec"
        pkill -f "codex exec" || true
        exit 3
      fi
      sleep "$every"
    done ;;
esac
