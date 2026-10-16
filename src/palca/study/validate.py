"""Validador geométrico independiente: no llama al packer ni a sus previews.

original_dims_by_box_id debe proceder de la entrada, nunca del dump colocado.
El CLI reconstruye IDs DES (1-based, después del shuffle y orden de llegada).
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Mapping, Sequence

from .orientation_faces import FORBIDDEN_FACES_ARM6, RestingFace, resting_face
from .params import PALLET_NOMINAL_MM, pallet_envelope_mm

_OVERHANG_MM = 20
_ENVELOPE_MM = pallet_envelope_mm(_OVERHANG_MM)
_GEOMETRY_TOL_MM = 0.5
_SUPPORT_Z_TOL_MM = 1.0


@dataclass(frozen=True)
class Violation:
    box_id: int | str | None
    code: str
    message: str
    other_box_id: int | str | None = None


def _face(original: tuple, placed: tuple, item: dict) -> RestingFace:
    """Comprueba metadatos contra dimensiones, incluidos los empates L/W/H."""
    axis = dict(zip("LWH", original))
    name = item.get("orientation_name")
    family = item.get("orientation_family")
    family_faces = {"planar": RestingFace.LXW, "stand_hw": RestingFace.HXW,
                    "stand_hl": RestingFace.HXL}
    vertical_faces = {"H": RestingFace.LXW, "L": RestingFace.HXW, "W": RestingFace.HXL}
    named_face = None
    if name is not None:
        if not isinstance(name, str) or sorted(name) != sorted("LWH"):
            raise ValueError(f"orientation_name desconocido: {name}")
        if tuple(axis[key] for key in name) != placed:
            raise ValueError(f"orientation_name {name} contradice dimensiones {placed}")
        named_face = vertical_faces[name[2]]
    if family is not None:
        if family not in family_faces:
            raise ValueError(f"orientation_family desconocida: {family}")
        family_face = family_faces[family]
        vertical_key = {RestingFace.LXW: "H", RestingFace.HXW: "L", RestingFace.HXL: "W"}[family_face]
        if placed[2] != axis[vertical_key]:
            raise ValueError(f"orientation_family {family} contradice la vertical")
        if named_face is not None and named_face != family_face:
            raise ValueError("orientation_name y orientation_family contradictorios")
        named_face = family_face
    if named_face is not None:
        return named_face
    if len(set(original)) < 3:
        raise ValueError("Dimensiones ambiguas sin orientation_family/orientation_name")
    return resting_face(original, placed)


def _union_area(rectangles: list[tuple[float, float, float, float]]) -> float:
    """Unión exacta de rectángulos: barrido x e intervalos y, sin doble conteo."""
    xs = sorted({x for x1, _, x2, _ in rectangles for x in (x1, x2)})
    area = 0.0
    for left, right in zip(xs, xs[1:]):
        intervals = sorted((y1, y2) for x1, y1, x2, y2 in rectangles
                           if x1 < right and x2 > left)
        covered = 0.0
        end = float("-inf")
        for low, high in intervals:
            covered += max(0.0, high - max(low, end))
            end = max(end, high)
        area += (right - left) * covered
    return area


def validate_pallet(items: list[dict], original_dims_by_box_id: Mapping,
                    min_support: float = 0.85) -> list[Violation]:
    """Comprueba estado final: envolvente, dimensiones/cara, solape y soporte.

    Tolerancia de envolvente/solape: 0,5 mm; de contacto vertical: ±1 mm.
    Suelo: |z| <= 0,5 mm. Soporte: unión de intersecciones con caras superiores.
    No verifica accesibilidad, cargas, estabilidad dinámica ni trayectoria.
    """
    if not isfinite(min_support) or not 0 <= min_support <= 1:
        raise ValueError("min_support debe estar en [0, 1]")
    originals = {str(key): dims for key, dims in original_dims_by_box_id.items()}
    violations = []
    geometry = []
    seen = set()
    for item in items:
        box_id = item.get("box_id")

        def add(code, message):
            violations.append(Violation(box_id, code, message))

        if str(box_id) in seen:
            add("duplicate_box_id", "box_id repetido en el palet")
        seen.add(str(box_id))
        try:
            placed = tuple(float(item[key]) for key in ("length_mm", "width_mm", "height_mm"))
            position = tuple(float(item[key]) for key in ("x_mm", "y_mm", "z_mm"))
            if any(not isfinite(v) for v in (*placed, *position)) or min(placed) <= 0:
                raise ValueError("Dimensiones no positivas o coordenadas no finitas")
        except (KeyError, ValueError, TypeError) as exc:
            add("invalid_geometry", str(exc))
            continue
        x, y, z = position
        length, width, height = placed
        geometry.append((box_id, x, y, z, length, width, height))
        lower = (-_OVERHANG_MM, -_OVERHANG_MM, 0)
        upper = (PALLET_NOMINAL_MM[0] + _OVERHANG_MM,
                 PALLET_NOMINAL_MM[1] + _OVERHANG_MM, _ENVELOPE_MM[2])
        if any(pos < lo - _GEOMETRY_TOL_MM or pos + dim > hi + _GEOMETRY_TOL_MM
               for pos, dim, lo, hi in zip(position, placed, lower, upper)):
            add("outside_envelope", f"posición {position}, dimensiones {placed}")
        if str(box_id) not in originals:
            add("unknown_box_id", "No hay dimensiones de entrada para box_id")
            continue
        try:
            original = tuple(float(v) for v in originals[str(box_id)])
            if (len(original) != 3 or any(not isfinite(v) or v <= 0 for v in original)
                    or sorted(original) != sorted(placed)):
                raise ValueError(f"Dimensiones {placed} no son permutación de {original}")
        except (TypeError, ValueError) as exc:
            add("dimensions", str(exc))
            continue
        try:
            face = _face(original, placed, item)
        except ValueError as exc:
            add("orientation", str(exc))
            continue
        if face in FORBIDDEN_FACES_ARM6:
            add("forbidden_face", f"Cara apoyada prohibida para arm6: {face.value}")

    for i, (box_id, x, y, z, length, width, height) in enumerate(geometry):
        for other_id, ox, oy, oz, ol, ow, oh in geometry[i + 1:]:
            overlaps = (min(x + length, ox + ol) - max(x, ox),
                        min(y + width, oy + ow) - max(y, oy),
                        min(z + height, oz + oh) - max(z, oz))
            if all(delta > _GEOMETRY_TOL_MM for delta in overlaps):
                violations.append(Violation(box_id, "overlap", f"Solape 3D {overlaps} mm", other_id))
        if abs(z) <= _GEOMETRY_TOL_MM:
            continue
        supporting = []
        for j, (_, ox, oy, oz, ol, ow, oh) in enumerate(geometry):
            if i == j or oz >= z or abs(oz + oh - z) > _SUPPORT_Z_TOL_MM:
                continue
            left, right = max(x, ox), min(x + length, ox + ol)
            bottom, top = max(y, oy), min(y + width, oy + ow)
            if right > left and top > bottom:
                supporting.append((left, bottom, right, top))
        support = _union_area(supporting) / (length * width)
        if support + 1e-12 < min_support:
            violations.append(Violation(box_id, "support", f"support_fraction={support:.9f} < {min_support}"))
    return violations
