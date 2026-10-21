"""G1 geometry-only value network and verified, portable checkpoints."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

from .state_encoding import ENCODING_VERSION

ARCHITECTURE = "g1-cnn5-deepsets-v1"
ENVELOPE_MM3 = 1240 * 840 * 2400


class ValueNetG1(nn.Module):
    def __init__(self):
        super().__init__()
        layers = []
        for incoming, outgoing, stride in [(3, 32, 1), (32, 32, 2),
                                            (32, 64, 1), (64, 64, 1), (64, 64, 1)]:
            layers.extend([nn.Conv2d(incoming, outgoing, 3, stride, 1), nn.ReLU()])
        self.cnn = nn.Sequential(*layers)
        self.box_mlp = nn.Sequential(nn.Linear(4, 64), nn.ReLU(), nn.Linear(64, 64), nn.ReLU())
        self.head = nn.Sequential(nn.Linear(260, 128), nn.ReLU(), nn.Linear(128, 64),
                                  nn.ReLU(), nn.Linear(64, 2))

    def forward(self, height, boxes, scalars):
        h = height.float().unsqueeze(1) / 2400
        dx = F.pad(h[:, :, 1:, :] - h[:, :, :-1, :], (0, 0, 0, 1))
        dy = F.pad(h[:, :, :, 1:] - h[:, :, :, :-1], (0, 1, 0, 0))
        geometry = self.cnn(torch.cat((h, dx, dy), dim=1))
        present = boxes[:, :, 3:4] > 0
        features = self.box_mlp(boxes.float())
        summed = (features * present).sum(dim=1)
        maximum = features.masked_fill(~present, -torch.inf).amax(dim=1)
        maximum = torch.where(present.any(dim=1), maximum, torch.zeros_like(maximum))
        result = self.head(torch.cat((geometry.mean((2, 3)), geometry.amax((2, 3)),
                                      summed, maximum, scalars.float()), dim=1))
        return result[:, 0].sigmoid(), result[:, 1]


def encoding_tensors(encodings, device):
    """Accept a batched mapping or a sequence of individual g1-v1 encodings."""
    if not isinstance(encodings, dict):
        encodings = {k: np.stack([e[k] for e in encodings])
                     for k in ("height", "boxes", "scalars")}
    expected = {"height": (62, 42), "boxes": (15, 4), "scalars": (4,)}
    tensors = []
    size = None
    for key, shape in expected.items():
        array = np.asarray(encodings[key])
        if array.ndim != len(shape) + 1 or array.shape[1:] != shape:
            raise ValueError(f"Invalid g1-v1 {key} shape: {array.shape}")
        if size is not None and len(array) != size:
            raise ValueError("Inconsistent batch sizes")
        size = len(array)
        tensors.append(torch.as_tensor(array.astype(np.float32, copy=False), device=device))
    return tuple(tensors)


@torch.inference_mode()
def predict_utilization_batch(model, encodings, device="cpu"):
    tensors = encoding_tensors(encodings, device)
    prediction, _ = model(*tensors)
    return torch.maximum(prediction.clamp(0, 1), tensors[2][:, 0].clamp(0, 1)).cpu().numpy()


def remaining_value_batch(model, encodings, device="cpu"):
    """Additional mm³ per encoded pallet (sum the result for a pallet collection)."""
    prediction = predict_utilization_batch(model, encodings, device)
    scalars = encodings["scalars"] if isinstance(encodings, dict) else np.stack(
        [e["scalars"] for e in encodings])
    return np.maximum(0, (prediction.astype(np.float64) - np.asarray(scalars)[:, 0]) * ENVELOPE_MM3)


def save_value_net(model, path, *, seeds, data, metrics):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), path)
    metadata = dict(schema_version=1, encoding_version=ENCODING_VERSION,
                    architecture=ARCHITECTURE, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    seeds=seeds, data=data, metrics=metrics)
    path.with_suffix(".json").write_text(json.dumps(metadata, indent=2, allow_nan=False) + "\n")
    return metadata


def load_value_net(path, device="cpu"):
    path = Path(path)
    metadata = json.loads(path.with_suffix(".json").read_text())
    if (metadata.get("schema_version") != 1 or metadata.get("encoding_version") != ENCODING_VERSION
            or metadata.get("architecture") != ARCHITECTURE
            or any(not isinstance(metadata.get(k), dict) for k in ("seeds", "data", "metrics"))
            or metadata.get("sha256") != hashlib.sha256(path.read_bytes()).hexdigest()):
        raise ValueError("Invalid G1 checkpoint schema or SHA256")
    model = ValueNetG1().to(device)
    model.load_state_dict(torch.load(path, map_location=device, weights_only=True), strict=True)
    model.eval()
    model.metadata = metadata
    return model
