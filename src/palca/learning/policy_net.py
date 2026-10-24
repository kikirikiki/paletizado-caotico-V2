"""G3 candidate policy. Inputs contain observable geometry only."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

import numpy as np
import torch
from torch import nn

from .state_encoding import ENCODING_VERSION

ARCHITECTURE = 'g3-cnn-deepsets-v1'
ENVELOPE_MM3 = 1240 * 840 * 2400


class PolicyNetG3(nn.Module):
    """Score B nodes with C candidates in one forward call; return masked logits.

    actions are raw [belt_index,x,y,z,l,w,h]. Boxes use g1 encoding.
    One matching box is removed by dimensions, so belt storage order is irrelevant.
    """
    def __init__(self):
        super().__init__()
        self.cnn = nn.Sequential(nn.Conv2d(3, 16, 5, 2, 2), nn.ReLU(),
                                 nn.Conv2d(16, 32, 3, 2, 1), nn.ReLU(),
                                 nn.Conv2d(32, 32, 3, 2, 1), nn.ReLU())
        self.box_mlp = nn.Sequential(nn.Linear(4, 32), nn.ReLU(), nn.Linear(32, 32), nn.ReLU())
        self.head = nn.Sequential(nn.Linear(139, 64), nn.ReLU(), nn.Linear(64, 1))

    def forward(self, height, boxes, scalars, actions, after, valid):
        b, c = actions.shape[:2]
        before = height.float()[:, None].expand(-1, c, -1, -1) / 2400
        after = after.float() / 2400
        geometry = self.cnn(torch.stack((before, after, after-before), 2).reshape(b*c, 3, 62, 42))
        # Canonical order also makes floating point reductions permutation invariant.
        key = boxes[..., 0]*1000000 + boxes[..., 1]*1000 + boxes[..., 2]
        ordered = boxes.gather(1, key.argsort(1)[..., None].expand(-1, -1, 4)).float()
        dimensions = actions[..., 4:7].float().sort(-1, descending=True).values / 1000
        matches = ((ordered[:, None, :, :3] - dimensions[:, :, None]).abs() < 1e-6).all(-1)
        present = ordered[:, None, :, 3] > 0
        matches = matches & present
        remove = matches & (matches.long().cumsum(-1) == 1)
        present = present & ~remove
        features = self.box_mlp(ordered)[:, None].expand(-1, c, -1, -1)
        summed = (features * present[..., None]).sum(2)
        maximum = features.masked_fill(~present[..., None], -torch.inf).amax(2)
        maximum = torch.where(present.any(2)[..., None], maximum, torch.zeros_like(maximum))
        scale = actions.new_tensor([15, 1240, 840, 2400, 1240, 840, 2400])
        vector = actions.float() / scale
        geometry = torch.cat((geometry.mean((2, 3)), geometry.amax((2, 3))), 1).reshape(b, c, 64)
        logits = self.head(torch.cat((geometry, summed, maximum, vector,
                          scalars.float()[:, None].expand(-1, c, -1)), -1)).squeeze(-1)
        return logits.masked_fill(~valid.bool(), -torch.inf)


def policy_tensors(batch, device='cpu'):
    return tuple(torch.as_tensor(np.asarray(batch[k]).astype(np.float32, copy=False), device=device)
                 for k in ('height', 'boxes', 'scalars', 'cand_actions', 'cand_height_after')) + (
        torch.as_tensor(np.arange(batch['cand_actions'].shape[1])[None] <
                        np.asarray(batch['cand_count'])[:, None], device=device),)


@torch.inference_mode()
def predict_policy_batch(model, batch, device='cpu'):
    return model(*policy_tensors(batch, device)).softmax(-1).cpu().numpy()


def save_policy_net(model, path, *, seeds, data, metrics):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), path)
    metadata = dict(schema_version=1, encoding_version=ENCODING_VERSION, architecture=ARCHITECTURE,
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(), seeds=seeds, data=data, metrics=metrics)
    path.with_suffix('.json').write_text(json.dumps(metadata, indent=2, allow_nan=False)+'\n')
    return metadata


def load_policy_net(path, device='cpu'):
    path = Path(path).expanduser().resolve()
    metadata = json.loads(path.with_suffix('.json').read_text())
    if (metadata.get('schema_version') != 1 or metadata.get('encoding_version') != ENCODING_VERSION
            or metadata.get('architecture') != ARCHITECTURE
            or any(not isinstance(metadata.get(k), dict) for k in ('seeds', 'data', 'metrics'))
            or metadata.get('sha256') != hashlib.sha256(path.read_bytes()).hexdigest()):
        raise ValueError('Invalid G3 checkpoint schema or SHA256')
    model = PolicyNetG3().to(device)
    model.load_state_dict(torch.load(path, map_location=device, weights_only=True), strict=True)
    model.eval()
    model.metadata = metadata
    return model


_MODEL_CACHE = {}
_PATH_CACHE = {}
_LOCK = threading.Lock()


def cached_policy_net(path, device='cpu'):
    """Only open checkpoint and adjacent JSON; invalidate on either artifact changing."""
    path = Path(path).expanduser().resolve()
    with _LOCK:
        try:
            signature = tuple((s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
                              for s in (path.stat(), path.with_suffix('.json').stat()))
            previous = _PATH_CACHE.get((str(path), device))
            if previous and previous[0] == signature:
                return _MODEL_CACHE[previous[1]]
            if device == 'cpu':
                torch.set_num_threads(1)
            else:
                os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')
            torch.use_deterministic_algorithms(True)
            model = load_policy_net(path, device)
            key = (str(path), model.metadata['sha256'], device)
            _MODEL_CACHE[key] = model
            _PATH_CACHE[str(path), device] = (signature, key)
            return model
        except (OSError, ValueError, RuntimeError) as exc:
            raise ValueError(f'Cannot load G3 checkpoint {path}: {exc}') from exc
