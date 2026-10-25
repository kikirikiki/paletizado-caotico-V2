#!/usr/bin/env python3
"""Train G3; fixed SHA256(basename) episode split as G1 (80/10/10)."""
from __future__ import annotations

import argparse
from collections import OrderedDict
import copy
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from train_value_g1 import (ROOT, outside_repository, episode_split, seed_everything,
                            available_memory, BlockSampler, sync)
import numpy as np
import torch
from torch.nn import functional as F
from torch.utils.data import Dataset, DataLoader
from palca.learning.policy_net import PolicyNetG3, save_policy_net, ENVELOPE_MM3

KEYS = ('height', 'boxes', 'scalars', 'cand_actions', 'cand_height_after',
        'cand_count', 'cand_values', 'cand_chosen')


def validate_source(directory):
    directory = outside_repository(directory)
    path = directory / 'manifest.json'
    if path.is_symlink():
        raise ValueError('D-032: symlink manifest')
    manifest = json.loads(path.read_text())
    arguments = manifest.get('arguments', {})
    if (manifest.get('encoding_version') != 'g1-v1' or manifest.get('policy') != 'official_b150'
            or manifest.get('with_after') is not True
            or manifest.get('prior') not in ('generic', 'families', 'mix')
            or arguments.get('policy') != 'official_b150' or arguments.get('with_after') is not True
            or arguments.get('prior') != manifest['prior']
            or 'scripts/gen_value_data.py' not in manifest.get('configuration_sha256', {})
            or 'src/palca/learning/synthetic_flows.py' not in manifest.get('source_sha256', {})):
        raise ValueError('D-032: expected synthetic official_b150 with_after provenance')
    paths = []
    for path in sorted(directory.iterdir()):
        resolved = outside_repository(path)
        if path.is_symlink() or not path.is_file():
            raise ValueError('D-032: unexpected entry')
        if path.name == 'manifest.json':
            continue
        if not path.match('ep_*.npz'):
            raise ValueError('D-032: foreign file')
        paths.append(resolved)
    if not paths:
        raise ValueError('No episodes')
    return directory, manifest, paths


def validate_episode(path):
    with np.load(path, allow_pickle=False) as z:
        n = len(z['height'])
        shapes = ((62,42), (15,4), (4,), (64,7), (64,62,42), (), (64,), ())
        for k, shape in zip(KEYS, shapes):
            a = z[k]
            if a.shape != (n, *shape) or (k != 'cand_values' and not np.isfinite(a).all()):
                raise ValueError(f'Invalid {k} in {path}')
        for k in ('height', 'cand_height_after'):
            if z[k].dtype != np.uint16 or np.any(z[k] > 2400):
                raise ValueError(f'Invalid uint16 map {k}')
        count, chosen, values = z['cand_count'], z['cand_chosen'], z['cand_values']
        if (not np.issubdtype(count.dtype, np.integer) or not np.issubdtype(chosen.dtype, np.integer)
                or np.any((count < 1) | (count > 64) | (chosen < 0) | (chosen >= count))):
            raise ValueError('Invalid candidate counts/chosen')
        valid = np.arange(64)[None] < count[:, None]
        if (np.isinf(values).any() or not np.isfinite(values[np.arange(n), chosen]).all()
                or np.any(np.isfinite(values) & ~valid)):
            raise ValueError('Invalid candidate values')
        if np.any((z['cand_actions'][..., 0][valid] < 0) | (z['cand_actions'][..., 0][valid] >= 15)):
            raise ValueError('Invalid belt index')
        return n


class Episodes(Dataset):
    """Bounded LRU of decompressed episodes, also used when the corpus fits RAM."""
    def __init__(self, records, budget):
        self.records, self.budget = records, budget
        self.ends = np.cumsum([r['count'] for r in records])
        self.cache, self.cache_bytes = OrderedDict(), 0

    def __len__(self):
        return int(self.ends[-1]) if len(self.ends) else 0

    def __getitem__(self, index):
        i = int(np.searchsorted(self.ends, index, side='right'))
        if i not in self.cache:
            with np.load(self.records[i]['path'], allow_pickle=False) as z:
                arrays = {k: z[k][:self.records[i]['count']] for k in KEYS}
            size = sum(a.nbytes for a in arrays.values())
            while self.cache and self.cache_bytes + size > self.budget:
                _, old = self.cache.popitem(last=False)
                self.cache_bytes -= sum(a.nbytes for a in old.values())
            self.cache[i] = arrays
            self.cache_bytes += size
        self.cache.move_to_end(i)
        row = index - (int(self.ends[i-1]) if i else 0)
        return {k: a[row] for k, a in self.cache[i].items()}


def tensors(batch, device):
    valid = torch.arange(batch['cand_actions'].shape[1], device=device)[None] < batch['cand_count'].to(device)[:, None]
    return (*(batch[k].to(device) for k in KEYS[:5]), valid)


def policy_loss(logits, values, chosen, valid, tau=.5, hard_weight=.5):
    finite = valid & torch.isfinite(values)
    pp = values.float() * (100 / ENVELOPE_MM3)
    target = (pp / tau).masked_fill(~finite, -torch.inf).softmax(-1)
    # Avoid 0 * -inf for padded candidates.
    logp = logits.masked_fill(~finite, -torch.inf).log_softmax(-1).masked_fill(~finite, 0)
    return -(target * logp).sum(-1).mean() + hard_weight * F.cross_entropy(logits, chosen.long())


@torch.inference_mode()
def evaluate(model, dataset, device, batch):
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark = False
    model.eval()
    hits = {k: 0 for k in (1,2,4,6,8)}
    regret, regret_states, states = 0., 0, 0
    for row in DataLoader(dataset, batch_size=batch):
        args = tensors(row, device)
        logits = model(*args)
        chosen, values = row['cand_chosen'].to(device).long(), row['cand_values'].to(device)
        order = logits.argsort(dim=-1, descending=True, stable=True)
        for k in hits:
            hits[k] += (order[:, :k] == chosen[:, None]).any(-1).sum().item()
        selected = values.gather(1, order[:, :1]).squeeze(1)
        known = torch.isfinite(selected)
        regret += ((values.gather(1, chosen[:, None]).squeeze(1)[known]-selected[known])*100/ENVELOPE_MM3).sum().item()
        regret_states += known.sum().item()  # exclude predictions with no teacher value
        states += len(chosen)
    return dict(states=states, **{f'top_{k}_recall': hits[k]/states if states else None for k in hits},
                regret_pp=regret/regret_states if regret_states else None, regret_states=regret_states)


def train(args):
    started = time.perf_counter()
    output = outside_repository(args.out)
    if (args.epochs < 1 or args.batch < 1 or not 0 < args.lr < float('inf')
            or not 0 < args.tau < float('inf') or not 0 <= args.hard_weight < float('inf')
            or (args.max_states is not None and args.max_states < 1)):
        raise ValueError('Invalid training hyperparameters')
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise ValueError('CUDA unavailable')
    sources = [validate_source(d) for d in args.data]
    groups = {k: [] for k in ('train', 'validation', 'test')}
    provenance, seen, remaining = [], set(), args.max_states
    for directory, manifest, paths in sources:
        if output.is_relative_to(directory):
            raise ValueError('Output inside data source')
        provenance.append(dict(directory=str(directory), manifest=manifest))
        for path in paths:
            if path in seen:
                raise ValueError('Duplicate episode')
            seen.add(path)
            n = validate_episode(path)
            count = n if remaining is None else min(n, remaining)
            if remaining is not None:
                remaining -= count
            if count:
                groups[episode_split(path.name)].append(dict(path=path, count=count,
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    datasets = {k: Episodes(rows, available_memory()//6) for k, rows in groups.items()}
    if not len(datasets['train']):
        raise ValueError('No training states in fixed episode split')
    seed_everything(args.seed, deterministic=not args.fast)
    torch.set_num_threads(1)
    model = PolicyNetG3().to(args.device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, args.epochs)
    amp = args.fast and args.device == 'cuda'
    scaler = torch.amp.GradScaler('cuda', enabled=amp)
    loader = DataLoader(datasets['train'], batch_size=args.batch,
                        sampler=BlockSampler(datasets['train'], args.seed),
                        generator=torch.Generator().manual_seed(args.seed))
    selection = 'validation' if len(datasets['validation']) else 'train'
    best, best_state, curve = -1., None, []
    for epoch in range(args.epochs):
        tick = time.perf_counter()
        torch.use_deterministic_algorithms(not args.fast)
        torch.backends.cudnn.benchmark = args.fast
        model.train()
        total, count = 0., 0
        for row in loader:
            inputs = tensors(row, args.device)
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast('cuda', dtype=torch.float16, enabled=amp):
                logits = model(*inputs)
            loss = policy_loss(logits.float(), row['cand_values'].to(args.device),
                               row['cand_chosen'].to(args.device), inputs[-1], args.tau, args.hard_weight)
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
            total += loss.item()*len(logits)
            count += len(logits)
        scheduler.step()
        metrics = evaluate(model, datasets[selection], args.device, args.batch)
        score = metrics['top_1_recall']
        if score > best:
            best, best_state = score, copy.deepcopy(model.state_dict())
        sync(args.device)
        curve.append(dict(epoch=epoch+1, loss=total/count, selection=metrics,
                          seconds=time.perf_counter()-tick, fast=args.fast))
        print(json.dumps(curve[-1]), flush=True)
    model.load_state_dict(best_state)
    report = dict(parameters=sum(p.numel() for p in model.parameters()), curve=curve,
                  selection_group=selection, tau_pp=args.tau, hard_weight=args.hard_weight,
                  split_rule=__doc__, groups={k: evaluate(model, ds, args.device, args.batch)
                                              for k, ds in datasets.items()},
                  episodes={k: [dict(r, path=str(r['path'])) for r in rows] for k, rows in groups.items()},
                  seconds=time.perf_counter()-started)
    save_policy_net(model.cpu(), output/'policy_g3.pt', seeds=dict(training=args.seed),
                    data=dict(sources=provenance, split_rule=__doc__), metrics=report)
    (output/'report.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', nargs='+', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--epochs', type=int, default=50)
    parser.add_argument('--batch', type=int, default=16)
    parser.add_argument('--lr', type=float, default=1e-3)
    parser.add_argument('--device', choices=('cpu','cuda'), default='cuda')
    parser.add_argument('--seed', type=int, default=39)
    parser.add_argument('--max-states', type=int)
    parser.add_argument('--tau', '--tau-pp', type=float, default=.5)
    parser.add_argument('--hard-weight', type=float, default=.5)
    parser.add_argument('--fast', action='store_true')
    args = parser.parse_args(argv)
    try:
        return train(args)
    except (ValueError, OSError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
