"""Geometry reuse must preserve previews, counters and isolation exactly."""
import copy
from dataclasses import replace

import pytest

from palca.domain.box import Box
from palca.domain.pallet_spec import PalletSpec
from palca.packer.controls import AccessibilityConfig, ControlConfig, StabilityConfig
from palca.packer.maxrects2d import MaxRects2D
from palca.packer.pallet_model import PalletModel


class UncachedProjection(MaxRects2D):
    """Identical projection algorithm; custom implementations bypass the cache."""


def pallets():
    pallet = PalletModel(PalletSpec(length_mm=600, width_mm=500, max_height_mm=900),
        stacking_mode='heightfield', orientation_mode='planar+stand_hw',
        stand_hw_height_margin_gate_mm=2400,
        control_config=ControlConfig(stability=StabilityConfig(mode='ratio+corners+settle',
                                     min_support_ratio=.85),
                                     accessibility=AccessibilityConfig(400)))
    base = Box('base', 300, 250, 500, 0, destination=1)
    pallet.commit_place(pallet.preview_place(base))
    uncached = copy.deepcopy(pallet)
    uncached.layers[0].bin.__class__ = UncachedProjection
    return pallet, uncached


@pytest.mark.parametrize('limits', [{}, {'max_candidates': 7}, {'max_tries_per_item': 35}])
def test_repeated_geometry_preserves_full_preview_and_statistics(limits):
    cached, uncached = pallets()
    for index, height in enumerate([100, 150, 100, 200, 100]):
        box = Box(f'new-{index}', 200, 150, height, 0, destination=1,
                  weight_kg=index + .25, priority=index, loadbear=index + 1)
        assert cached.preview_place(box, **limits) == uncached.preview_place(box, **limits)
        assert cached.stats == uncached.stats
    assert cached._heightfield_preview_cache.heights
    assert cached._heightfield_preview_cache.robot


def test_commit_and_external_geometry_changes_invalidate_snapshot():
    cached, uncached = pallets()
    box = Box('next', 200, 150, 100, 0, destination=1)
    for pallet in (cached, uncached):
        pallet.preview_place(box)
        # Even replacement with the same number of placements is detected.
        pallet.placements[0] = replace(pallet.placements[0], height_mm=700)
    assert cached.preview_place(box) == uncached.preview_place(box)
    old_cache = cached._heightfield_preview_cache
    preview = cached.preview_place(box)
    cached.commit_place(preview)
    uncached.commit_place(uncached.preview_place(box))
    assert cached._heightfield_preview_cache is None
    assert cached.preview_place(box) == uncached.preview_place(box)
    assert cached._heightfield_preview_cache is not old_cache
    assert cached.stats == uncached.stats


def test_beam_copy_discards_geometry_and_keeps_mutable_state_isolated():
    pallet, _ = pallets()
    box = Box('child', 200, 150, 100, 0, destination=1)
    preview = pallet.preview_place(box)
    cache = pallet._heightfield_preview_cache
    clone = copy.deepcopy(pallet)
    assert clone._heightfield_preview_cache is not cache
    assert not clone._heightfield_preview_cache.heights
    assert not clone._heightfield_preview_cache.robot
    assert clone.preview_place(box) == preview
    clone.commit_place(preview)
    assert len(clone.placements) == len(pallet.placements) + 1
    assert pallet._heightfield_preview_cache is cache
    assert pallet.layers[0].bin is not clone.layers[0].bin
    assert pallet.stats is not clone.stats


def test_robot_delta_is_part_of_cache_key():
    from palca.packer.controls import RobotAccessibilityControl
    cached, uncached = pallets()
    box = Box('next', 200, 150, 100, 0, destination=1)
    for pallet in (cached, uncached):
        pallet.preview_place(box)
    for pallet in (cached, uncached):
        controls = list(pallet.controls.placement_controls)
        controls[0] = RobotAccessibilityControl(AccessibilityConfig(20))
        pallet.controls = replace(pallet.controls, placement_controls=controls)
    assert cached.preview_place(box) == uncached.preview_place(box)
    assert cached.stats == uncached.stats


def test_vectorized_heights_match_scalar_touching_overlap_and_empty():
    from palca.domain.placement import Placement
    from palca.packer.pallet_model import _HeightfieldPreviewCache
    pallet = PalletModel(stacking_mode='heightfield')
    pallet.placements = [
        Placement(x_mm=x, y_mm=y, z_mm=z, length_mm=l, width_mm=w,
                  height_mm=h, rot90=False, layer_id=0)
        for x, y, z, l, w, h in [
            (-20, -20, 0, 100, 200, 300), (80, 40, 300, 200, 100, 400),
            (280, 140, 0, 150, 100, 500), (0, 0, -100, 10, 10, 50)]]
    xy = [(x, y) for x in [-30, -20, 0, 80, 100, 280, 430, 500]
          for y in [-30, -20, 0, 40, 140, 180, 240, 500]]
    for placements in [[], pallet.placements]:
        pallet.placements = placements
        cache = _HeightfieldPreviewCache(())
        for length, width in [(1, 1), (100, 200), (200, 100)]:
            cache.fill_heights(xy, length, width, -20, placements)
            for x, y in xy:
                key = (x - 20, y - 20, length, width)
                assert cache.heights[key] == pallet._height_under_footprint_mm(*key)


def test_vectorized_heights_fall_back_for_unbounded_python_integers():
    from palca.domain.placement import Placement
    from palca.packer.pallet_model import _HeightfieldPreviewCache
    placement = Placement(x_mm=0, y_mm=0, z_mm=0, rot90=False, layer_id=0,
                          length_mm=100, width_mm=100, height_mm=2**64)
    cache = _HeightfieldPreviewCache(())
    cache.fill_heights([(0, 0)], 10, 10, 0, [placement])
    assert cache.heights == {}
