"""Complete official H6 golden episodes; fixtures predate the optimization."""
import copy
import json
from pathlib import Path

import pytest

from scripts.perf_h6 import assert_equivalent, official_config
from scripts.run_sweep import make_task, run_task, sweep_lock

FIXTURES = Path(__file__).parent / 'fixtures/h6_perf'


@pytest.mark.slow
@pytest.mark.parametrize('seed', [1, 42])
def test_official_h6_complete_dump_and_kpi(seed, tmp_path):
    manifest = json.loads((FIXTURES / 'manifest.json').read_text())
    config = official_config()
    assert config == manifest['config']
    task = make_task(config, seed, tmp_path, manifest['git_head'], manifest['source_hash'])
    assert task['input_hash'] == manifest['input_hash']
    with sweep_lock():
        result = run_task(task)
    dump = json.loads((Path(task['directory']) / 'dump.json').read_text())
    assert_equivalent(dump, result,
                      json.loads((FIXTURES / f'{seed}_dump.json').read_text()),
                      json.loads((FIXTURES / f'{seed}_kpi.json').read_text()))
    assert result['hard_cap_hits'] == result['deadline_cutoffs_count'] == 0


def test_equivalence_checks_every_nested_field():
    expected_dump = json.loads((FIXTURES / '42_dump.json').read_text())
    expected_kpi = json.loads((FIXTURES / '42_kpi.json').read_text())
    actual_dump = copy.deepcopy(expected_dump)
    actual_dump['git_head'] = 'different-provenance'
    assert_equivalent(actual_dump, dict(expected_kpi, wall_time_s=123), expected_dump, expected_kpi)
    actual_dump['pallets']['unexpected'] = []
    with pytest.raises(AssertionError):
        assert_equivalent(actual_dump, expected_kpi, expected_dump, expected_kpi)
    actual_kpi = copy.deepcopy(expected_kpi)
    actual_kpi['pallet_utils'][0] += 1e-12
    with pytest.raises(AssertionError):
        assert_equivalent(expected_dump, actual_kpi, expected_dump, expected_kpi)
