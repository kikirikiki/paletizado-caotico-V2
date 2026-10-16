from types import SimpleNamespace

import pytest

from palca.packer import pallet_model
from palca.study.orientation_faces import (
    FORBIDDEN_FACES_ARM6,
    RestingFace,
    assert_mode_allowed,
    resting_face,
)
from palca.study.params import preset_params


def test_four_variants_and_faces():
    original = (600, 400, 300)
    variants = pallet_model._orientation_variants_for_mode(
        *original, mode="planar+stand_hw", allow_rotate=True
    )
    assert len(variants) == 4
    assert {
        (v.name, (v.length_mm, v.width_mm, v.height_mm), resting_face(
            original, (v.length_mm, v.width_mm, v.height_mm)
        )) for v in variants
    } == {
        ("LWH", (600, 400, 300), RestingFace.LXW),
        ("WLH", (400, 600, 300), RestingFace.LXW),
        ("HWL", (300, 400, 600), RestingFace.HXW),
        ("WHL", (400, 300, 600), RestingFace.HXW),
    }


@pytest.mark.parametrize("mode", pallet_model.ALLOWED_ORIENTATION_MODES)
@pytest.mark.parametrize("allow_rotate", [False, True])
def test_no_existing_mode_rests_on_hxl(mode, allow_rotate):
    original = (600, 400, 300)
    variants = pallet_model._orientation_variants_for_mode(
        *original, mode=mode, allow_rotate=allow_rotate
    )
    assert variants
    for v in variants:
        assert resting_face(original, (v.length_mm, v.width_mm, v.height_mm)) not in FORBIDDEN_FACES_ARM6
    assert_mode_allowed(mode)


def test_synthetic_edge_is_forbidden_in_mode_and_arm_presets(monkeypatch):
    assert resting_face((600, 400, 300), (600, 300, 400)) == RestingFace.HXL

    def edge_variants(length, width, height, **kwargs):
        return [SimpleNamespace(
            length_mm=length, width_mm=height, height_mm=width, name="LHW"
        )]

    monkeypatch.setattr(pallet_model, "_orientation_variants_for_mode", edge_variants)
    with pytest.raises(ValueError, match="HxL"):
        assert_mode_allowed("planar+stand_hw")
    with pytest.raises(ValueError, match="HxL"):
        preset_params("arm6_4orient")
    assert_mode_allowed("planar+stand_hw", forbidden=set())


@pytest.mark.parametrize("original,placed,face", [
    ((600, 400, 400), (600, 400, 400), RestingFace.HXL),
    ((600, 600, 300), (300, 600, 600), RestingFace.HXL),
    ((400, 400, 400), (400, 400, 400), RestingFace.HXL),
    ((600, 400, 600), (400, 600, 600), RestingFace.HXW),
])
def test_equal_dimensions_are_conservative(original, placed, face):
    assert resting_face(original, placed) == face


@pytest.mark.parametrize("placed", [(600, 400, 200), (600, 400)])
def test_invalid_dimensions(placed):
    with pytest.raises(ValueError):
        resting_face((600, 400, 300), placed)
