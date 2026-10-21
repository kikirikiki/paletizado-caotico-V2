## Reglas fijas de ahorro de tokens (D-038; las añade codex_job.sh a cada spec)
- No explores el repo más allá de los archivos que nombra la spec, salvo que sea imprescindible.
- No vigiles procesos largos: nada de comprobar logs en bucle. Pasa solo los tests de los módulos que tocas
  (`pytest -q tests/<archivo>`); el smoke completo, los sweeps y los entrenamientos los lanza el director.
- Si un comando tarda más de 10 min, déjalo preparado (comando exacto) y entrégalo sin ejecutarlo.
- Entrega final breve: archivos cambiados (1 línea cada uno) y un bloque JSON con las comprobaciones
  (`{"tests": ..., "equivalencia": ..., "tiempo_ms": ..., "pendiente": [...]}`). Sin repetir la spec.

