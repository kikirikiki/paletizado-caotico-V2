# Estado del proyecto — Paletizado Caótico

## Fecha: Marzo 2026

## Qué se ha hecho
- Estudio de viabilidad FIFO completado (ver docs/ si existe el PDF)
- Scorer de coherencia de capa implementado: src/palca/packer/scoring_coherencia.py
- Clase CoherenciaCapaScorer: puntúa colocaciones según vecinos a la misma altura (radio 80mm, banda ±50mm)
- Smoke test pasando en rama feat/fifo-scorer-coherencia

## Resultado del estudio de viabilidad
- FIFO con scorer de coherencia: 81% del rendimiento humano con buffer=1 caja
- Buffer de 15 cajas planificado es innecesario: con 1-3 cajas se obtiene el mismo resultado
- Gap residual 19%: hueco estructural de ~355mm en X (800mm palet - 445mm caja típica)
- Causa identificada: no rellenable con caja del mismo tipo → solución: detección activa del hueco

## Próximo paso inmediato
Integrar CoherenciaCapaScorer en el pipeline como score_mode="coherencia_capa" en:
- src/palca/packer/scoring.py → añadir referencia al nuevo modo
- src/palca/integration/policy_packer_sched.py → activar cuando score_mode=="coherencia_capa"
Sin modificar el comportamiento actual de ningún otro score_mode.

## Arquitectura clave
- PalletModel (src/palca/packer/pallet_model.py) → clase central, preview_place + commit_place
- ScoringWeights (src/palca/packer/scoring.py) → pesos del scorer actual
- score_mode → string que selecciona scorer: "gain_frag", "min_height_then_gain", etc.
- CoherenciaCapaScorer (src/palca/packer/scoring_coherencia.py) → scorer nuevo, independiente

## Reglas de trabajo
- Smoke test obligatorio antes de commit: ./scripts/smoke_offline.sh
- Ramas: feat/<tema> o fix/<tema>, nunca directo a main
- No commitear: out/, outputs/, logs/, .venv/, __pycache__/
- PR por cada objetivo técnico acotado
