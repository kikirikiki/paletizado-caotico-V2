#!/usr/bin/env python3
"""Train G1 on synthetic episodes. Split: SHA256(basename) integer modulo 100:
0..79 train, 80..89 validation, 90..99 test; identical names share a split
across sources. No state-level random split. Mixed priors remain labelled mix:
the generator does not record the latent prior per state.
"""
from __future__ import annotations

import argparse
from collections import OrderedDict
import copy
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import time

os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import torch
from torch.nn import functional as F
from torch.utils.data import Dataset, DataLoader
from palca.learning.value_net import (ValueNetG1, encoding_tensors, predict_utilization_batch,
                                     save_value_net)

KEYS = ("height", "boxes", "scalars", "util_final", "cajas_hasta_cierre")


def outside_repository(path):
    path = Path(path).expanduser().resolve()
    if path.is_relative_to(ROOT) or "out" in path.parts:
        raise ValueError(f"D-032: path must be outside repository and out/: {path}")
    return path


def validate_source(directory):
    directory = outside_repository(directory)
    manifest_path = directory / "manifest.json"
    if manifest_path.is_symlink():
        raise ValueError("D-032: symlink manifest is not allowed")
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else None
    if not isinstance(manifest, dict):
        raise ValueError("D-032: missing synthetic manifest.json")
    # gen_value_data.py predates an explicit synthetic flag; its signed provenance
    # and synthetic CLI declaration are the generator's existing schema.
    arguments = manifest.get("arguments", {})
    if (manifest.get("encoding_version") != "g1-v1"
            or manifest.get("policy") not in ("greedy", "official")
            or manifest.get("prior") not in ("generic", "families", "mix")
            or arguments.get("policy") != manifest["policy"]
            or arguments.get("prior") != manifest["prior"]
            or "scripts/gen_value_data.py" not in manifest.get("configuration_sha256", {})
            or "src/palca/learning/synthetic_flows.py" not in manifest.get("source_sha256", {})):
        raise ValueError("D-032: manifest does not declare the synthetic generator")
    episodes = []
    for path in sorted(directory.iterdir()):
        resolved = outside_repository(path)
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"D-032: unexpected entry {path}")
        if path.name == "manifest.json":
            continue
        if not path.match("ep_*.npz"):
            raise ValueError(f"D-032: foreign file {path}")
        episodes.append(resolved)
    if not episodes:
        raise ValueError("No synthetic episodes")
    return directory, manifest, episodes


def episode_split(name):
    bucket = int.from_bytes(hashlib.sha256(name.encode()).digest(), "big") % 100
    return "train" if bucket < 80 else "validation" if bucket < 90 else "test"


class Episodes(Dataset):
    """uint16 resident heights, or a bounded episode-block LRU if RAM is scarce."""
    def __init__(self, records, memory_bytes):
        self.records = records
        self.ends = np.cumsum([r["count"] for r in records])
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.budget = memory_bytes
        self.resident = sum(r["count"] * (62*42*2 + 15*4*4 + 4*4 + 8) for r in records) <= memory_bytes
        if self.resident:
            for i in range(len(records)):
                self._episode(i)

    def __len__(self):
        return int(self.ends[-1]) if len(self.ends) else 0

    def _episode(self, i):
        if i in self.cache:
            self.cache.move_to_end(i)
            return self.cache[i]
        with np.load(self.records[i]["path"], allow_pickle=False) as z:
            n = self.records[i]["count"]
            arrays = {k: np.asarray(z[k][:n], dtype=np.uint16 if k == "height" else np.float32)
                      for k in KEYS}
        size = sum(a.nbytes for a in arrays.values())
        while self.cache and not self.resident and self.cache_bytes + size > self.budget:
            _, old = self.cache.popitem(last=False)
            self.cache_bytes -= sum(a.nbytes for a in old.values())
        self.cache[i] = arrays
        self.cache_bytes += size
        return arrays

    def __getitem__(self, index):
        i = int(np.searchsorted(self.ends, index, side="right"))
        row = index - (int(self.ends[i-1]) if i else 0)
        arrays = self._episode(i)
        return (*(arrays[k][row] for k in KEYS), np.float32(self.records[i]["weight"]))


class BlockSampler(torch.utils.data.Sampler):
    """Shuffle episodes and their rows without thrashing the disk block cache."""
    def __init__(self, dataset, seed):
        self.dataset = dataset
        self.rng = np.random.default_rng(seed)

    def __len__(self):
        return len(self.dataset)

    def __iter__(self):
        for i in self.rng.permutation(len(self.dataset.records)):
            start = int(self.dataset.ends[i-1]) if i else 0
            yield from (start + self.rng.permutation(self.dataset.records[i]["count"])).tolist()


def seed_everything(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark = False
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False


def sync(device):
    if str(device).startswith("cuda"):
        torch.cuda.synchronize()


def available_memory():
    available = os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    try:
        limit = Path("/sys/fs/cgroup/memory.max").read_text().strip()
        if limit != "max":
            used = int(Path("/sys/fs/cgroup/memory.current").read_text())
            available = min(available, max(1, int(limit) - used))
    except (OSError, ValueError):
        pass
    return available


def metrics(y, p):
    if not len(y):
        return dict(states=0, mae_pp=None, rmse_pp=None, r2=None)
    residual = np.asarray(p, dtype=np.float64) - y
    variance = np.sum((y - np.mean(y)) ** 2)
    return dict(states=len(y), mae_pp=float(np.mean(np.abs(residual))*100),
                rmse_pp=float(np.sqrt(np.mean(residual**2))*100),
                r2=float(1-np.sum(residual**2)/variance) if variance > 0 else None)


def evaluate(model, dataset, device, batch):
    predictions, targets = [], []
    model.eval()
    for h, b, s, y, _, _ in DataLoader(dataset, batch_size=batch):
        enc = dict(height=h.numpy(), boxes=b.numpy(), scalars=s.numpy())
        predictions.append(predict_utilization_batch(model, enc, device))
        targets.append(y.numpy())
    return (np.concatenate(targets).astype(np.float64), np.concatenate(predictions)) if targets else (
        np.empty(0), np.empty(0))


def benchmark(model, sample, devices):
    result = {}
    old_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        for device in devices:
            model.to(device).eval()
            result[device] = {}
            for size in (1, 16, 64, 256):
                enc = {k: np.repeat(v[:1], size, axis=0) for k, v in sample.items()}
                for _ in range(5):
                    predict_utilization_batch(model, enc, device)
                sync(device)
                elapsed = []
                for _ in range(200):
                    start = time.perf_counter()
                    predict_utilization_batch(model, enc, device)
                    sync(device)
                    elapsed.append((time.perf_counter()-start)*1000)
                result[device][str(size)] = dict(mean_ms=float(np.mean(elapsed)),
                                               p95_ms=float(np.percentile(elapsed, 95)), repeats=200)
    finally:
        torch.set_num_threads(old_threads)
    return result


def train(args):
    started = time.perf_counter()
    output = outside_repository(args.out)
    if args.device == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA unavailable: cannot perform the requested GPU training")
    if args.epochs < 1 or args.batch < 1 or args.lr <= 0 or args.aux_weight < 0:
        raise ValueError("Invalid training hyperparameters")
    if args.max_states is not None and args.max_states < 1:
        raise ValueError("max-states must be positive")
    weights = args.weights or [1.] * len(args.data)
    if len(weights) != len(args.data) or any(not np.isfinite(w) or w <= 0 for w in weights):
        raise ValueError("--weights requires one positive finite weight per directory")
    groups = {k: [] for k in ("train", "validation", "test")}
    provenance, remaining, seen = [], args.max_states, set()
    # Validate every directory before opening any episode.
    sources = [validate_source(d) for d in args.data]
    for (directory, manifest, paths), weight in zip(sources, weights):
        if output.is_relative_to(directory):
            raise ValueError("Output must not be inside a training source")
        provenance.append(dict(directory=str(directory), weight=weight, manifest=manifest))
        for path in paths:
            if path in seen:
                raise ValueError("Duplicate episode path")
            seen.add(path)
            with np.load(path, allow_pickle=False) as z:
                n = len(z["util_final"])
                prior = str(path.stem.split("_")[1])
                for key, shape in [("height", (n, 62, 42)), ("boxes", (n, 15, 4)),
                                   ("scalars", (n, 4)), ("cajas_hasta_cierre", (n,))]:
                    if z[key].shape != shape or not np.isfinite(z[key]).all():
                        raise ValueError(f"Invalid episode array {path}: {key}")
                if not np.isfinite(z["util_final"]).all() or np.any(
                        (z["util_final"] < 0) | (z["util_final"] > 1)):
                    raise ValueError(f"Invalid utilization in {path}")
                if z["height"].dtype != np.uint16 or np.any(z["height"] > 2400):
                    raise ValueError(f"Invalid uint16 height in {path}")
            count = n if remaining is None else min(n, remaining)
            if remaining is not None:
                remaining -= count
            if count:
                groups[episode_split(path.name)].append(dict(path=path, count=count, weight=weight,
                    policy=manifest["policy"], prior=prior,
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    # Share half the currently available memory across all groups; fallback is
    # bounded episode blocks, never a full decompression of the corpus at once.
    budget = available_memory() // 2 // 3
    datasets = {k: Episodes(v, budget) for k, v in groups.items()}
    if not len(datasets["train"]):
        raise ValueError("No training states in fixed episode split")
    seed_everything(args.seed)
    torch.set_num_threads(1)
    model = ValueNetG1().to(args.device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, args.epochs)
    blocked = not datasets["train"].resident
    loader = DataLoader(datasets["train"], batch_size=args.batch, shuffle=not blocked,
                        sampler=BlockSampler(datasets["train"], args.seed) if blocked else None,
                        generator=torch.Generator().manual_seed(args.seed))
    curve, best, best_state = [], float("inf"), None
    selection_group = "validation" if len(datasets["validation"]) else "train"
    # Two-episode smoke fixtures may have no validation/test. Never move episodes
    # between splits; explicitly report training-only selection in this case.
    for epoch in range(args.epochs):
        sync(args.device)
        tick = time.perf_counter()
        model.train()
        total, states = 0., 0
        for h, b, s, y, auxiliary, weight in loader:
            h, b, s, y, auxiliary, weight = [t.to(args.device) for t in (h, b, s, y, auxiliary, weight)]
            optimizer.zero_grad(set_to_none=True)
            prediction, aux = model(h, b, s)
            loss_row = F.huber_loss(prediction, y, reduction="none") + args.aux_weight * F.huber_loss(
                aux, auxiliary/100, reduction="none")
            loss = (loss_row * weight).mean()
            loss.backward()
            optimizer.step()
            total += loss.item() * len(y)
            states += len(y)
        scheduler.step()
        y, p = evaluate(model, datasets[selection_group], args.device, args.batch)
        score = metrics(y, p)["mae_pp"]
        sync(args.device)
        curve.append(dict(epoch=epoch+1, loss=total/states, selection_mae_pp=score,
                          seconds=time.perf_counter()-tick, device=args.device))
        if score < best:
            best, best_state = score, copy.deepcopy(model.state_dict())
    model.load_state_dict(best_state)
    # Only now evaluate the held-out test, with the selected checkpoint.
    evaluated = {k: evaluate(model, ds, args.device, args.batch) for k, ds in datasets.items()}
    train_mean = float(evaluated["train"][0].mean())
    report = dict(selection_group=selection_group, best_mae_pp=best, curve=curve,
                  parameters=sum(p.numel() for p in model.parameters()),
                  split_rule=__doc__, seeds=dict(training=args.seed), constant_mean=train_mean,
                  groups={}, latency={}, cpu_gpu_max_abs=None,
                  storage={k: "resident" if ds.resident else "episode_blocks" for k, ds in datasets.items()},
                  episodes={k: [dict(r, path=str(r["path"])) for r in rows] for k, rows in groups.items()})
    for group, (y, p) in evaluated.items():
        sources_report = {}
        labels = []
        for r in groups[group]:
            labels.extend([(r["policy"], r["prior"])] * r["count"])
        for dimension in (0, 1):
            for label in sorted({v[dimension] for v in labels}):
                mask = np.array([v[dimension] == label for v in labels])
                sources_report[label] = dict(model=metrics(y[mask], p[mask]),
                                            constant=metrics(y[mask], np.full(mask.sum(), train_mean)))
        report["groups"][group] = dict(model=metrics(y, p),
            constant=metrics(y, np.full(len(y), train_mean)), sources=sources_report)
    h, b, s, *_ = datasets["train"][0]
    sample = dict(height=h[None], boxes=b[None], scalars=s[None])
    devices = ["cpu"] + (["cuda"] if torch.cuda.is_available() else [])
    if "cuda" in devices and len(datasets["test"]):
        predictions = []
        for device in devices:
            model.to(device)
            predictions.append(evaluate(model, datasets["test"], device, args.batch)[1])
        report["cpu_gpu_max_abs"] = float(np.max(np.abs(predictions[0]-predictions[1])))
    report["latency"] = benchmark(model, sample, devices)
    report["seconds"] = time.perf_counter()-started
    report["pending"] = ([] if "cuda" in devices else ["CUDA training, latency and CPU/GPU equivalence unavailable"])
    if not len(datasets["test"]):
        report["pending"].append("No test episodes in fixed split: CPU/GPU comparison requires more episodes")
    output.mkdir(parents=True, exist_ok=True)
    save_value_net(model.cpu(), output / "value_g1.pt", seeds=dict(training=args.seed),
                   data=dict(sources=provenance, split_rule=__doc__), metrics=report)
    (output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    lines = ["# G1 training", "", f"Selection: {selection_group}; parameters: {report['parameters']}",
             "", "MAE/RMSE in percentage points; R² dimensionless. Constant fitted on training only.",
             "", "```json", json.dumps(report, indent=2), "```", ""]
    (output / "report.md").write_text("\n".join(lines))
    if report["cpu_gpu_max_abs"] is not None and report["cpu_gpu_max_abs"] >= 1e-5:
        raise ValueError("CPU/GPU predictions differ by >= 1e-5; see report.json")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", nargs="+", required=True)
    parser.add_argument("--weights", nargs="+", type=float)
    parser.add_argument("--out", required=True)
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch", type=int, default=1024)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--seed", type=int, default=39)
    parser.add_argument("--max-states", type=int)
    parser.add_argument("--aux-weight", type=float, default=.1)
    args = parser.parse_args(argv)
    try:
        report = train(args)
    except (ValueError, OSError, KeyError) as exc:
        parser.error(str(exc))
    print(json.dumps(dict(parameters=report["parameters"], seconds=report["seconds"],
                          latency=report["latency"], cpu_gpu_max_abs=report["cpu_gpu_max_abs"])))


if __name__ == "__main__":
    main()
