#!/usr/bin/env bash
# Cuota semanal de Codex (regla del usuario D-027: no pasar del 60 %).
#
# Lee el último evento `rate_limits` de las sesiones de Codex (~/.codex/sessions), buscando hacia atrás hasta la sesión
# más reciente que lo tenga con ventana semanal (desde el 2026-10-21 también llegan eventos "premium" con ventanas null).
# "secondary" es la ventana semanal (10080 min) y "primary" la de 5 h; si la ventana ya se reinició (resets_at pasado), cuenta como 0 %.
# La lectura es DESCONOCIDA (nunca 0 %) si no hay ninguna o si después hubo sesiones que consumieron tokens sin lectura.
# Además, si el último job de autopilot_logs (< 5 h) terminó con "usage limit", Codex está SIN CUOTA hasta que se
# reinicie la ventana de 5 h de la lectura.
#
# Uso:
#   codex_quota.sh check            imprime "weekly=<pct|?> 5h=<pct|?> limit=<L> status=<ok|over|unknown|blocked>";
#                                   sale con 0 (ok), 3 (semanal >= LIMIT), 4 (usage limit del último job) o 5 (desconocida).
#                                   CODEX_QUOTA_ASSUME_OK=1 deja pasar "unknown" (solo con decisión explícita del director).
#   codex_quota.sh watch [segundos] guardián: cada N s (60) comprueba y, si weekly >= LIMIT, mata los `codex exec`
#                                   en marcha (el trabajo queda reanudable con su thread id) y termina.
set -u
LIMIT=${CODEX_WEEKLY_LIMIT:-60}
SESS=${CODEX_SESSIONS:-$HOME/.codex/sessions}
LOGS=${CODEX_JOB_LOGS:-$HOME/code/Paletizador/09_Software/autopilot_logs}

read_status() {
  python3 - "$SESS" "$LOGS" "$LIMIT" <<'PY'
import glob, json, os, sys, time
sess, logs, limit = sys.argv[1], sys.argv[2], float(sys.argv[3])
now = time.time()

def find_rl(obj):
    if isinstance(obj, dict):
        rl = obj.get("rate_limits")
        # Solo vale una lectura con ventana semanal (las de limit_id "premium" llegan con primary/secondary null).
        if isinstance(rl, dict) and isinstance(rl.get("secondary"), dict):
            return rl
        for v in obj.values():
            r = find_rl(v)
            if r: return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_rl(v)
            if r: return r
    return None

files = sorted(glob.glob(os.path.join(sess, "*/*/*/*.jsonl")), key=os.path.getmtime, reverse=True)
rl, newer_without = None, 0
for f in files:
    last, used = None, False
    with open(f, errors="replace") as fh:
        for line in fh:
            if '"rate_limits"' not in line: continue
            try: ev = json.loads(line)
            except Exception: continue
            last = find_rl(ev) or last
            # token_count con info != null = la sesión consumió tokens (una sesión rechazada al empezar trae info null).
            used = used or (isinstance(ev.get("payload"), dict) and ev["payload"].get("info") is not None)
    if last:
        rl = last; break
    newer_without += int(used)

def pct(w):
    if not w: return 0.0
    return 0.0 if float(w.get("resets_at") or 0) <= now else float(w.get("used_percent") or 0)

wk = h5 = "?"
status = "unknown"
if rl is not None:
    wk, h5 = f"{pct(rl.get('secondary')):.0f}", f"{pct(rl.get('primary')):.0f}"
    status = "over" if float(wk) >= limit else ("unknown" if newer_without else "ok")

# Señal directa: el último job terminó por límite de uso (vale 5 h).
jobs = sorted(glob.glob(os.path.join(logs, "*.jsonl")) + glob.glob(os.path.join(logs, "*.err")),
              key=os.path.getmtime, reverse=True)
# Deja de valer cuando la ventana de 5 h de la lectura se reinicia (pct -> 0).
if jobs and now - os.path.getmtime(jobs[0]) < 5 * 3600 and status != "over" and h5 != "0":
    with open(jobs[0], errors="replace") as fh:
        if "usage limit" in fh.read():
            status = "blocked"
print(wk, h5, status, newer_without)
PY
}

case "${1:-check}" in
  check)
    read -r wk h5 st nw < <(read_status)
    echo "weekly=$wk 5h=$h5 limit=$LIMIT status=$st sesiones_sin_lectura=$nw"
    case "$st" in
      ok) exit 0 ;;
      over) exit 3 ;;
      blocked) exit 4 ;;
      *) [ "${CODEX_QUOTA_ASSUME_OK:-0}" = 1 ] && exit 0; exit 5 ;;
    esac ;;
  watch)
    every=${2:-60}
    while true; do
      read -r wk h5 st nw < <(read_status)
      if [ "$st" = over ]; then
        echo "$(date '+%F %H:%M') weekly=$wk >= $LIMIT: deteniendo codex exec"
        pkill -f "codex exec" || true
        exit 3
      fi
      sleep "$every"
    done ;;
esac
