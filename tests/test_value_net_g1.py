"""Fast CPU contracts for G1, including real synthetic-generator provenance."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
import torch

from palca.learning.value_net import (ValueNetG1, load_value_net, save_value_net,
    predict_utilization_batch, remaining_value_batch)

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("train_g1", ROOT / "scripts/train_value_g1.py")
trainer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trainer)
torch.set_num_threads(1)


def encodings(n=4):
    rng = np.random.default_rng(9)
    boxes = rng.random((n, 15, 4), dtype=np.float32)
    boxes[:, :, 3] = 1
    boxes[:, 8:, :] = 0
    return dict(height=rng.integers(0, 2400, (n, 62, 42), dtype=np.uint16),
                boxes=boxes, scalars=rng.random((n, 4), dtype=np.float32))


def test_network_contract():
    model = ValueNetG1().eval()
    assert sum(p.numel() for p in model.parameters()) <= 300000
    enc = encodings()
    p = predict_utilization_batch(model, enc)
    assert p.shape == (4,) and np.all((p >= 0) & (p <= 1))
    shuffled = dict(enc, boxes=enc['boxes'][:, ::-1].copy())
    np.testing.assert_allclose(p, predict_utilization_batch(model, shuffled), atol=1e-6, rtol=0)
    # Raw forward too: clipping cannot hide an inference inconsistency.
    raw, aux = model(*trainer.encoding_tensors(enc, "cpu"))
    permuted_raw, _ = model(*trainer.encoding_tensors(shuffled, "cpu"))
    torch.testing.assert_close(raw, permuted_raw, atol=1e-6, rtol=0)
    one, _ = model(*trainer.encoding_tensors({k: v[:1] for k, v in enc.items()}, "cpu"))
    assert aux.shape == (4,)
    assert abs(raw[0].item()-one.item()) < 1e-6
    assert np.all(remaining_value_batch(model, enc) >= 0)
    enc['scalars'][:, 0] = 1
    assert np.all(remaining_value_batch(model, enc) == 0)
    enc['boxes'][:] = 0
    assert np.isfinite(predict_utilization_batch(model, enc)).all()


def test_checkpoint(tmp_path):
    model = ValueNetG1().eval()
    enc = encodings()
    path = tmp_path / 'weights.pt'
    meta = save_value_net(model, path, seeds={'training': 9}, data={}, metrics={})
    loaded = load_value_net(path)
    assert loaded.metadata['sha256'] == meta['sha256']
    np.testing.assert_array_equal(predict_utilization_batch(model, enc),
                                  predict_utilization_batch(loaded, enc))
    metadata_path = path.with_suffix('.json')
    original = metadata_path.read_text()
    for key, value in [('architecture', 'bad'), ('encoding_version', 'bad'), ('seeds', [])]:
        altered = json.loads(original)
        altered[key] = value
        metadata_path.write_text(json.dumps(altered))
        with pytest.raises(ValueError):
            load_value_net(path)
    metadata_path.write_text(original)
    with path.open('ab') as f:
        f.write(b'corrupt')
    with pytest.raises(ValueError, match='SHA256'):
        load_value_net(path)


def test_symlink_manifest(tmp_path):
    (tmp_path / 'manifest.json').symlink_to(ROOT / 'AGENTS.md')
    with pytest.raises(ValueError, match='symlink'):
        trainer.validate_source(tmp_path)


@pytest.fixture(scope='module')
def generated(tmp_path_factory):
    directory = tmp_path_factory.mktemp('g1_synthetic')
    subprocess.run([sys.executable, str(ROOT / 'scripts/gen_value_data.py'),
                    '--policy', 'greedy', '--prior', 'generic', '--episodes', '2',
                    '--workers', '1', '--out', str(directory)], check=True,
                   capture_output=True, text=True, timeout=180)
    return directory


def test_guards(tmp_path, generated):
    with pytest.raises(ValueError, match='D-032'):
        trainer.validate_source(ROOT)
    with pytest.raises(ValueError, match='manifest'):
        trainer.validate_source(tmp_path)
    with pytest.raises(ValueError, match='D-032'):
        trainer.validate_source(tmp_path / 'out')
    foreign = generated / 'foreign.csv'
    foreign.write_text('not synthetic')
    try:
        with pytest.raises(ValueError, match='foreign'):
            trainer.validate_source(generated)
    finally:
        foreign.unlink()
    directory, manifest, paths = trainer.validate_source(generated)
    assert len(paths) == 2
    altered = tmp_path / 'fake'
    altered.mkdir()
    manifest['source_sha256'].pop('src/palca/learning/synthetic_flows.py')
    (altered / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='synthetic'):
        trainer.validate_source(altered)


def test_deterministic_training(tmp_path, generated, monkeypatch):
    # Latency is measured by the standalone run, not repeated in unit tests.
    monkeypatch.setattr(trainer, 'benchmark', lambda *args: {})
    states = []
    for run in range(2):
        output = tmp_path / str(run)
        args = trainer.argparse.Namespace(data=[str(generated)], weights=[1.], out=str(output),
            epochs=2, batch=64, lr=.001, device='cpu', seed=39, max_states=None, aux_weight=.1)
        report = trainer.train(args)
        assert len(report['curve']) == 2
        states.append(load_value_net(output / 'value_g1.pt').state_dict())
    assert all(torch.equal(states[0][key], states[1][key]) for key in states[0])


def test_split_and_block_loading(generated):
    _, _, paths = trainer.validate_source(generated)
    records = []
    for path in paths:
        with np.load(path) as z:
            records.append(dict(path=path, count=min(3, len(z['height'])), weight=1.))
    resident = trainer.Episodes(records, 10**8)
    blocked = trainer.Episodes(records, 1)
    assert not blocked.resident
    for i in [0, 3, 1, 4]:
        for a, b in zip(resident[i], blocked[i]):
            np.testing.assert_array_equal(a, b)
    assignments = [trainer.episode_split(f'ep_generic_{i}.npz') for i in range(1000)]
    assert set(assignments) == {'train', 'validation', 'test'}
    assert 750 < assignments.count('train') < 850
    assert list(trainer.BlockSampler(blocked, 39)) == list(trainer.BlockSampler(blocked, 39))
    assert sorted(trainer.BlockSampler(blocked, 39)) == list(range(len(blocked)))
