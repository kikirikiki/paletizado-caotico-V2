"""Scorer de coherencia de capa: premia colocaciones rodeadas de vecinos
a la misma altura.

Criterio: una colocación candidata es "coherente" si tiene al menos un vecino
(caja ya colocada) cuyo centroide XY cae dentro de un radio de 80 mm Y cuya
base (z_mm) está dentro de ±50 mm respecto a la z_base candidata.

El score resultante es continuo en [0, 1]:

    score = vecinos_coherentes / max(total_vecinos_en_radio, 1)

Un score de 1.0 indica que todos los vecinos cercanos están a la misma
altura; 0.0 indica que no hay vecinos coherentes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from ..domain.placement import Placement


@dataclass
class CoherenciaCapaScorer:
    """Puntúa candidatos según coherencia de vecinos a la misma altura.

    Parámetros
    ----------
    radio_mm:
        Radio de búsqueda de vecinos en el plano XY (mm).  Por defecto 80 mm.
    banda_z_mm:
        Semi-amplitud de la banda vertical para considerar un vecino
        "coherente".  Un vecino es coherente si ``|vecino.z_mm - z_base| <=
        banda_z_mm``.  Por defecto 50 mm.
    peso:
        Factor de escala que multiplica el score antes de devolverlo.
        Permite combinarlo con otras puntuaciones aditivas.
    """

    radio_mm: float = 80.0
    banda_z_mm: float = 50.0
    peso: float = 1.0

    # Cajas ya colocadas; se actualiza externamente o con `registrar`.
    _colocadas: list[Placement] = field(default_factory=list, init=False, repr=False)

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def registrar(self, placement: Placement) -> None:
        """Registra una colocación confirmada para futuras evaluaciones."""
        self._colocadas.append(placement)

    def limpiar(self) -> None:
        """Elimina todas las colocaciones registradas."""
        self._colocadas.clear()

    def score(
        self,
        cx: float,
        cy: float,
        z_base: float,
        colocadas: Sequence[Placement] | None = None,
    ) -> float:
        """Devuelve el score de coherencia para una colocación candidata.

        Parámetros
        ----------
        cx, cy:
            Centroide XY de la colocación candidata (mm).
        z_base:
            Altura de la base de la colocación candidata (mm).
        colocadas:
            Lista de cajas ya colocadas a usar como contexto.  Si es
            ``None`` se usa el estado interno gestionado con
            :meth:`registrar`.

        Devuelve
        --------
        float
            Score en ``[0, 1]`` multiplicado por :attr:`peso`.
            Retorna 0.0 si no hay ningún vecino en el radio.
        """
        pool = colocadas if colocadas is not None else self._colocadas
        if not pool:
            return 0.0

        total_en_radio = 0
        coherentes = 0

        for p in pool:
            vcx = p.x_mm + p.length_mm / 2.0
            vcy = p.y_mm + p.width_mm / 2.0
            dist = math.hypot(cx - vcx, cy - vcy)
            if dist > self.radio_mm:
                continue
            total_en_radio += 1
            if abs(p.z_mm - z_base) <= self.banda_z_mm:
                coherentes += 1

        if total_en_radio == 0:
            return 0.0

        return self.peso * coherentes / total_en_radio

    def score_placement(
        self,
        candidate: Placement,
        colocadas: Sequence[Placement] | None = None,
    ) -> float:
        """Conveniencia: calcula el score a partir de un objeto
        :class:`~palca.domain.placement.Placement` candidato."""
        cx = candidate.x_mm + candidate.length_mm / 2.0
        cy = candidate.y_mm + candidate.width_mm / 2.0
        return self.score(cx, cy, float(candidate.z_mm), colocadas)
