import json
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/autopilot/verify_offline_witness.py'
verifier = runpy.run_path(str(SCRIPT))
accessibility_violations = verifier['accessibility_violations']


def item(x=0, y=0, z=0, height=500):
    return dict(x_mm=x, y_mm=y, z_mm=z, length_mm=100, width_mm=100, height_mm=height)


@pytest.mark.parametrize('gx,gy,gap,expected', [
    (0, 0, 0, 1), (2, 0, 0, 0), (2, 0, 1, 0), (2, 0, 2, 1),
    (50, 50, 50, 1), (51, 0, 50, 0), (0, 51, 50, 0),
])
def test_adjacency_threshold_inclusive_on_both_axes(gx, gy, gap, expected):
    items = [item(), item(x=100 + gx, y=100 + gy, height=100)]
    assert accessibility_violations(items, 400, gap) == expected
    if gap == 0:
        assert accessibility_violations(items, 400) == expected


def test_local_height_uses_overlap_only_and_chronological_order():
    items = [item(), item(x=102, height=100)]
    assert accessibility_violations(items, 400, 50) == 1
    assert accessibility_violations(items[::-1], 400, 50) == 0
    assert accessibility_violations([item(), item(z=500)], 400, 50) == 0
    assert accessibility_violations([item(height=400), item(x=100)], 400, 50) == 0
    assert accessibility_violations(items, 0, 50) == 0
    with pytest.raises(ValueError, match='nonnegative'):
        accessibility_violations(items, 400, -1)


def test_historical_witness_production_and_d015_diagnostic():
    witness = json.loads((ROOT / 'docs/autopilot/evidence/offline_witness_columns_80p40.json').read_text())
    pallets = verifier['pallets_of'](witness)
    assert sum(accessibility_violations(items, 400) for items in pallets) == 0
    assert sum(accessibility_violations(items, 400, 50) for items in pallets) == 117


def test_cli_reports_diagnostic_and_failure_status():
    witness = ROOT / 'docs/autopilot/evidence/offline_witness_columns_80p40.json'
    for gap, expected in [(0, 0), (50, 117)]:
        result = subprocess.run([sys.executable, str(SCRIPT), str(witness),
                                 '--adjacency-gap', str(gap)], capture_output=True, text=True)
        assert result.returncode == (0 if gap == 0 else 1), result.stdout + result.stderr
        assert 'violaciones validador: 0' in result.stdout
        assert f'adjacency-gap {gap}): {expected}' in result.stdout
    result = subprocess.run([sys.executable, str(SCRIPT), str(witness), '--adjacency-gap', '-1'],
                            capture_output=True, text=True)
    assert result.returncode == 2
    assert 'nonnegative' in result.stderr
