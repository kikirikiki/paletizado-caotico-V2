"""Exportador del visor 3D: captura de solo lectura, métricas y HTML autocontenido.

La comprobación de bit-identidad con una simulación real (seed 50021) es lenta y se
hace con `scripts/export_sim_trace.py --verify`; aquí solo se prueba lo rápido.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from palca.integration.policy_packer_sched import PolicyPackerScheduler
from scripts import export_sim_trace as exporter


def test_recording_restores_policy_methods():
    names = ("choose_action", "commit_plan", "on_changeover_start")
    before = {name: PolicyPackerScheduler.__dict__[name] for name in names}
    with exporter.recording(exporter.TraceRecorder()):
        assert all(PolicyPackerScheduler.__dict__[name] is not before[name] for name in names)
    assert {name: PolicyPackerScheduler.__dict__[name] for name in names} == before


def test_synthetic_metrics_match_definitions():
    run = exporter.synthetic_run()
    by_step = {s["step"]: s for s in run["steps"]}
    assert by_step[1]["free_sides"] == 4 and by_step[1]["support_pct"] == 100.0
    assert by_step[5]["support_pct"] == 0.0          # flotando
    assert by_step[10]["support_pct"] == 50.0        # apoyo parcial
    assert by_step[12]["free_sides"] == 0            # hueco cerrado
    assert by_step[12]["height_after"] == 1500
    assert run["steps"][0]["events"][0]["type"] == "open_pallet"
    assert run["steps"][-1]["events"][-1]["type"] == "close_pallet"


def test_render_html_is_self_contained(tmp_path):
    trace = dict(schema=exporter.SCHEMA, runs=[exporter.synthetic_run()])
    out = tmp_path / "sim3d.html"
    exporter.render_html(trace, out)
    html = out.read_text(encoding="utf-8")
    assert not re.search(r'<script src="', html), "quedan scripts externos sin incrustar"
    assert "THREE" in html and "Sim3DChecks" in html
    data = re.search(r'<script id="sim-data" type="application/json">(.*?)</script>', html, re.S).group(1)
    assert json.loads(data.replace("<\\/", "</"))["runs"][0]["config"] == "synthetic"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible")
def test_viewer_checks_flag_synthetic_defects(tmp_path):
    trace = tmp_path / "synthetic.json"
    trace.write_text(json.dumps(dict(schema=exporter.SCHEMA, runs=[exporter.synthetic_run()])), encoding="utf-8")
    script = Path(exporter.ROOT) / "tools" / "sim3d" / "test_checks.js"
    result = subprocess.run(["node", str(script), str(trace)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
