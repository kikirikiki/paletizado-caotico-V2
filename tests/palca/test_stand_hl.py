"""Tests para la orientacion stand_hl y no-regresion de modos existentes."""
from __future__ import annotations

import pytest
from palca.packer.pallet_model import (
    ORIENTATION_MODE_ALL,
    ORIENTATION_MODE_PLANAR,
    ORIENTATION_MODE_PLANAR_STAND_HW,
    PalletModel,
    _orientation_variants_for_mode,
    normalize_orientation_mode,
    should_allow_stand_hw,
)
from palca.domain.pallet_spec import PalletSpec


L, W, H = 605, 445, 355  # caja dominante del flujo


def _variants(mode: str, allow_stand_hw=None, allow_stand_hl=None):
    return _orientation_variants_for_mode(
        L, W, H,
        mode=mode,
        allow_rotate=True,
        allow_stand_hw=allow_stand_hw,
        allow_stand_hl=allow_stand_hl,
    )


def test_compute_variants_planar_only():
    v = _variants(ORIENTATION_MODE_PLANAR)
    assert len(v) == 2
    assert all(x.family == "planar" for x in v)
    assert not any(x.family == "stand_hl" for x in v)


def test_compute_variants_stand_hw():
    v = _variants(ORIENTATION_MODE_PLANAR_STAND_HW)
    assert len(v) == 4
    families = {x.family for x in v}
    assert "stand_hw" in families
    assert "stand_hl" not in families


def test_compute_variants_all():
    v = _variants(ORIENTATION_MODE_ALL)
    assert len(v) == 6
    assert {x.family for x in v} == {"planar", "stand_hw", "stand_hl"}


def test_stand_hl_dimensions():
    v = _variants(ORIENTATION_MODE_ALL)
    hl = [x for x in v if x.family == "stand_hl"]
    assert len(hl) == 2
    dims = sorted((x.length_mm, x.width_mm, x.height_mm) for x in hl)
    assert (H, W, L) in dims   # rot0: 355x445x605
    assert (W, H, L) in dims   # rot90: 445x355x605
    for v_ in hl:
        assert v_.height_mm == L  # dimension mayor siempre como altura


def test_stand_hl_gate_blocks():
    """Palet vacio -> margen=2400 > 400 -> gate cerrado -> 0 variantes stand_hl."""
    spec = PalletSpec(max_height_mm=2400)
    model = PalletModel(
        spec=spec,
        orientation_mode=ORIENTATION_MODE_ALL,
        stand_hw_height_margin_gate_mm=400,
        stacking_mode="heightfield",
    )
    variants = model._orientations(L, W, H)
    assert not any(v.family == "stand_hl" for v in variants)
    assert model.stats.stand_hl_gate_blocks_total == 1


def test_stand_hl_gate_allows():
    """Verificacion directa de la funcion de gate con distintos margenes."""
    assert should_allow_stand_hw(350, 400) is True
    assert should_allow_stand_hw(400, 400) is True
    assert should_allow_stand_hw(401, 400) is False
    assert should_allow_stand_hw(0, 400) is True


def test_normalize_mode_all():
    result = normalize_orientation_mode(ORIENTATION_MODE_ALL)
    assert result == "planar+stand_hw+stand_hl"


def test_existing_modes_unchanged():
    """No regresion: planar y planar+stand_hw producen exactamente las mismas variantes."""
    planar_v = _variants(ORIENTATION_MODE_PLANAR)
    assert len(planar_v) == 2
    planar_dims = {(v.length_mm, v.width_mm, v.height_mm) for v in planar_v}
    assert planar_dims == {(L, W, H), (W, L, H)}

    hw_v = _variants(ORIENTATION_MODE_PLANAR_STAND_HW)
    assert len(hw_v) == 4
    hw_dims = {(v.length_mm, v.width_mm, v.height_mm) for v in hw_v}
    assert hw_dims == {(L, W, H), (W, L, H), (H, W, L), (W, H, L)}
