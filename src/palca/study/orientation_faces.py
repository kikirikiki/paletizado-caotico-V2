"""Caras definidas sobre L/W/H originales del Excel, antes de orientar."""

from enum import Enum
from typing import Iterable, Sequence

from palca.packer import pallet_model


class RestingFace(str, Enum):
    LXW = "LxW"
    HXW = "HxW"
    HXL = "HxL"


FORBIDDEN_FACES_ARM6 = {"HxL"}


def resting_face(original_lwh: Sequence[int], placed_lwh: Sequence[int]) -> RestingFace:
    """Identifica la vertical; en empates prioriza HxL, después HxW y LxW.

    Las dimensiones iguales no permiten recuperar la rotación física. La
    clasificación conservadora puede rechazar incluso una colocación plana.
    """
    original = tuple(original_lwh)
    placed = tuple(placed_lwh)
    if len(original) != 3 or len(placed) != 3 or sorted(original) != sorted(placed):
        raise ValueError("Las dimensiones colocadas deben ser una permutación de L/W/H")
    if any(dim <= 0 for dim in original):
        raise ValueError("Las dimensiones deben ser positivas")
    length, width, _height = original
    vertical = placed[2]
    if vertical == width:
        return RestingFace.HXL
    if vertical == length:
        return RestingFace.HXW
    return RestingFace.LXW


def assert_mode_allowed(
    orientation_mode: str, forbidden: Iterable[str] = FORBIDDEN_FACES_ARM6
) -> None:
    """Comprueba las variantes geométricas, sin aplicar el gate de altura."""
    forbidden = set(forbidden)
    for original in ((600, 400, 300), (700, 450, 250)):
        variants = pallet_model._orientation_variants_for_mode(
            *original, mode=orientation_mode, allow_rotate=True
        )
        for variant in variants:
            placed = (variant.length_mm, variant.width_mm, variant.height_mm)
            face = resting_face(original, placed)
            if face in forbidden:
                raise ValueError(
                    f"Modo {orientation_mode}: variante {variant.name} apoya cara prohibida {face.value}"
                )
