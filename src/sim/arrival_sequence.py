"""Palanca operativa: el almacén secuencia antes del buffer online de 15.

Se aplica después del shuffle por seed; no amplía la información de la política.
Los horarios pertenecen a posiciones del flujo, no a las cajas reordenadas.
"""
from dataclasses import replace

from .des import Arrival


ARRIVAL_SORT_KEYS = ("family_height", "height_desc", "volume_desc")


def sort_arrival_windows(
    arrivals: list[Arrival], *, window: int = 0, key: str = "family_height",
    arrival_mode: str = "immediate",
) -> list[Arrival]:
    """Orden estable en bloques consecutivos; 0 conserva incluso los IDs.

    family_height: primero L=600..610, W=440..450, H=345..395 mm;
    dentro de familia/resto, altura descendente. Empates conservan el shuffle.
    Se preservan los slots temporales y se renumeran filas para que el DES
    reciba realmente el nuevo orden tanto en immediate como en excel.
    """
    if type(window) is not int or window < 0:
        raise ValueError("arrival_sort_window debe ser un entero >= 0")
    if key not in ARRIVAL_SORT_KEYS:
        raise ValueError(f"arrival_sort_key desconocida: {key}")
    if window <= 1:
        return arrivals
    mode = arrival_mode.strip().lower()
    if mode not in {"immediate", "excel"}:
        raise ValueError(f"arrival_mode desconocido: {mode}")
    ordered = sorted(arrivals, key=(lambda a: a.row_idx) if mode == "immediate"
                     else (lambda a: (a.time, a.row_idx)))

    def sort_key(a: Arrival):
        dims = (a.length_mm, a.width_mm, a.height_mm)
        if any(dim is None or dim <= 0 for dim in dims):
            raise ValueError("Secuenciación requiere dimensiones originales positivas")
        length, width, height = dims
        if key == "volume_desc":
            return (-length * width * height,)
        if key == "height_desc":
            return (-height,)
        dominant = 600 <= length <= 610 and 440 <= width <= 450 and 345 <= height <= 395
        return (not dominant, -height)

    sequenced = []
    for start in range(0, len(ordered), window):
        sequenced.extend(sorted(ordered[start:start + window], key=sort_key))
    return [replace(a, row_idx=i, time=slot.time)
            for i, (a, slot) in enumerate(zip(sequenced, ordered), start=1)]
