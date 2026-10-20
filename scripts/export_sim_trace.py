#!/usr/bin/env python3
"""Exporta la secuencia real de colocaciones para el visor 3D (tools/sim3d).

Ejecuta la simulación exactamente como scripts/run_sweep.py (make_task + run_task,
mismas seeds, mismos parámetros y mismo KPI congelado) y, mientras corre, copia
los datos de cada decisión con envoltorios de solo lectura sobre
PolicyPackerScheduler.choose_action / commit_plan / on_changeover_start. Los
envoltorios llaman al método original y solo leen; no se modifica src/.

--verify ejecuta además la misma simulación sin envoltorios y exige que las
colocaciones y el KPI sean idénticos (bit a bit en el JSON del dump).

Convenciones (verificadas en pallet_model.py / validate.py / PalletSpec):
  mm enteros; origen en la esquina del palet nominal 1200x800; z=0 es la cara
  superior del palet y z es la vertical; x,y,z es la esquina mínima de la caja;
  dx=length_mm (eje x), dy=width_mm (eje y), dz=height_mm (vertical), ya
  orientadas. Envolvente x∈[-20,1220], y∈[-20,820], z∈[0,2400].

Ejemplos:
  scripts/export_sim_trace.py                                   # h6_sin_gate, seed 50021
  scripts/export_sim_trace.py --run h6_sin_gate --run arm6_4orient --seeds 50021,1 \
      --html out/sim3d/comparativa.html
  scripts/export_sim_trace.py --synthetic --html out/sim3d/sintetico.html
"""
from __future__ import annotations

import os

# Igual que run_sweep: hilos BLAS a 1 antes de importar numpy.
for _key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_key, "1")

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from math import prod
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402

from palca.domain.placement import Placement  # noqa: E402
from palca.integration.policy_packer_sched import PolicyPackerScheduler  # noqa: E402
from palca.scoring.free_sides import free_sides  # noqa: E402
from palca.study.metric import ENVELOPE_MM, split_pallets  # noqa: E402
from palca.study.params import PALLET_NOMINAL_MM  # noqa: E402
from palca.study.validate import _union_area  # noqa: E402
from palca.tuning.episodes import apply_shuffle  # noqa: E402
from scripts.kpi_from_run import original_dims_for_run  # noqa: E402
from scripts.run_sweep import load_configs, make_task, run_task  # noqa: E402
from sim.io import load_arrivals  # noqa: E402

SCHEMA = "palca-sim-trace/1"
OVERHANG_MM = 20
SUPPORT_Z_TOL_MM = 1.0
TEMPLATE = ROOT / "tools" / "sim3d" / "sim3d.html"
VENDOR = ROOT / "tools" / "sim3d" / "vendor"
DEFAULT_OUT = ROOT / "out" / "sim3d"


# --------------------------------------------------------------------------- captura

class TraceRecorder:
    """Copia datos de las llamadas del DES a la política; nunca escribe en ella."""

    def __init__(self) -> None:
        self.decisions: dict[str, dict] = {}
        self.commits: list[dict] = []
        self.changeovers: list[dict] = []

    @staticmethod
    def _box(item) -> dict:
        return dict(box_id=getattr(item, "box_id", None),
                    lwh=[getattr(item, "length_mm", None), getattr(item, "width_mm", None),
                         getattr(item, "height_mm", None)])

    def on_choose(self, policy, ramps, now) -> dict:
        k = max(1, int(policy.config.scheduler.lookahead_k))
        return {int(rid): [self._box(it) for it in list(getattr(ramp, "queue", []))[:k]]
                for rid, ramp in ramps.items()}

    def on_plan(self, plan, snapshot: dict, now) -> None:
        self.decisions[str(plan.box_id)] = dict(
            ramp_id=int(plan.ramp_id), chosen_index=int(plan.buffer_index),
            conveyor=snapshot.get(int(plan.ramp_id), []), decision_time=float(now),
            score=_finite_or_none(plan.score))

    def on_commit(self, entry: dict, time) -> None:
        self.commits.append(dict(entry, commit_time=None if time is None else float(time)))

    def on_changeover(self, destination, reason) -> None:
        self.changeovers.append(dict(dest=str(destination), reason=str(reason), after_commits=len(self.commits)))


def _finite_or_none(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value == value and abs(value) != float("inf") else None


@contextmanager
def recording(recorder: TraceRecorder):
    """Envuelve los métodos de clase y los restaura siempre al salir."""
    cls = PolicyPackerScheduler
    original = {name: cls.__dict__[name] for name in ("choose_action", "commit_plan", "on_changeover_start")}

    def choose_action(self, *, ramps, destinations, now):
        snapshot = recorder.on_choose(self, ramps, now)
        plan = original["choose_action"](self, ramps=ramps, destinations=destinations, now=now)
        if plan is not None:
            recorder.on_plan(plan, snapshot, now)
        return plan

    def commit_plan(self, plan, time=None):
        seq = self._committed_placements.get(getattr(plan, "pallet_id", None), [])
        before = len(seq)
        result = original["commit_plan"](self, plan, time)
        seq = self._committed_placements.get(getattr(plan, "pallet_id", None), [])
        if len(seq) > before:
            recorder.on_commit(dict(seq[-1]), time)
        return result

    def on_changeover_start(self, destination, reason, *args, **kwargs):
        recorder.on_changeover(destination, reason)
        return original["on_changeover_start"](self, destination, reason, *args, **kwargs)

    cls.choose_action, cls.commit_plan, cls.on_changeover_start = choose_action, commit_plan, on_changeover_start
    try:
        yield recorder
    finally:
        for name, fn in original.items():
            setattr(cls, name, fn)


# --------------------------------------------------------------------------- métricas

def support_fraction(box: tuple, previous: list[tuple]) -> float:
    """Igual que validate_pallet: unión de intersecciones con caras superiores a z±1 mm."""
    x, y, z, dx, dy, _ = box
    if abs(z) <= 0.5:
        return 1.0
    rects = []
    for ox, oy, oz, odx, ody, odz in previous:
        if oz >= z or abs(oz + odz - z) > SUPPORT_Z_TOL_MM:
            continue
        left, right = max(x, ox), min(x + dx, ox + odx)
        bottom, top = max(y, oy), min(y + dy, oy + ody)
        if right > left and top > bottom:
            rects.append((left, bottom, right, top))
    return _union_area(rects) / (dx * dy)


def _placement(b: tuple) -> Placement:
    x, y, z, dx, dy, dz = b
    return Placement(x_mm=x, y_mm=y, z_mm=z, rot90=False, layer_id=0, length_mm=dx, width_mm=dy, height_mm=dz)


def add_step_metrics(steps: list[dict]) -> None:
    """Métricas del momento, reiniciando vecinos en cada palet físico."""
    envelope_volume = prod(ENVELOPE_MM)
    previous: list[tuple] = []
    volume = 0.0
    z_max = 0
    current = None
    for step in steps:
        if step["pallet"] != current:
            current, previous, volume, z_max = step["pallet"], [], 0.0, 0
        box = tuple(step[k] for k in ("x", "y", "z", "dx", "dy", "dz"))
        step["free_sides"] = free_sides(_placement(box), [_placement(b) for b in previous])
        step["support_pct"] = round(100.0 * support_fraction(box, previous), 3)
        volume += box[3] * box[4] * box[5]
        z_max = max(z_max, box[2] + box[5])
        step["height_after"] = z_max
        step["util_cum"] = volume / envelope_volume
        previous.append(box)


# --------------------------------------------------------------------------- identidad Excel

def excel_rows_for_run(payload: dict, excel_path: str) -> dict[int, int]:
    """box_id DES -> fila Excel (1 = cabecera). Mismo orden que original_dims_for_run."""
    params = payload["params"]
    arrivals = load_arrivals(excel_path, weight_col=params.get("weight_col"))
    window, strength = params.get("shuffle_window", 0), params.get("shuffle_strength", 0.0)
    if int(window) > 1 and float(strength) > 0:
        arrivals = apply_shuffle(sorted(arrivals, key=lambda a: a.row_idx),
                                 seed=params.get("episode_seed", 0), window=window, strength=strength)
        indexed = list(enumerate(arrivals, start=1))
    else:
        indexed = [(a.row_idx, a) for a in arrivals]
    if params.get("arrival_mode", "excel").strip().lower() == "excel":
        indexed.sort(key=lambda pair: (pair[1].time, pair[0]))
    else:
        indexed.sort(key=lambda pair: pair[0])
    return {box_id: int(arrival.row_idx) + 2 for box_id, (_, arrival) in enumerate(indexed, start=1)}


# --------------------------------------------------------------------------- ejecución

def git(*args: str) -> str:
    return subprocess.check_output(["git", "--no-pager", *args], cwd=ROOT, text=True).strip()


def resolve_config(spec: str) -> tuple[dict, Path]:
    """'nombre' (busca configs/sweeps/nombre.yaml y luego el resto) o 'fichero.yaml:nombre'."""
    if ":" in spec:
        file, name = spec.rsplit(":", 1)
        candidates = [Path(file) if Path(file).is_absolute() else ROOT / file]
    else:
        name = spec
        folder = ROOT / "configs" / "sweeps"
        preferred = folder / f"{name}.yaml"
        candidates = ([preferred] if preferred.exists() else []) + sorted(
            p for p in folder.glob("*.yaml") if p != preferred)
    for path in candidates:
        try:
            configs = load_configs(path)
        except (OSError, ValueError, yaml.YAMLError):
            continue
        for config in configs:
            if config["name"] == name:
                return config, path
    raise SystemExit(f"Config {spec!r} no encontrada en configs/sweeps")


def simulate(config: dict, seed: int, folder: Path, head: str, record: bool) -> tuple[dict, dict, dict, TraceRecorder | None]:
    task = make_task(config, seed, folder, head, "sim3d-export")
    recorder = TraceRecorder() if record else None
    if record:
        with recording(recorder):
            result = run_task(task)
    else:
        result = run_task(task)
    directory = Path(task["directory"])
    payload = json.loads((directory / "run.json").read_text())
    dump = json.loads((directory / "dump.json").read_text())
    return task, result, dict(payload=payload, dump=dump), recorder


def build_run(config: dict, config_path: Path, seed: int, folder: Path, verify: bool,
              reference: Path | None) -> dict:
    head = git("rev-parse", "HEAD")
    dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
    print(f"[sim3d] {config['name']} seed={seed}: simulando con captura…", flush=True)
    task, result, files, recorder = simulate(config, seed, folder / "trace", head, record=True)
    payload, dump = files["payload"], files["dump"]
    records = split_pallets(payload, dump)
    originals = original_dims_for_run(payload, task["params"]["excel_path"])
    rows = excel_rows_for_run(payload, task["params"]["excel_path"])

    placed = [item for rec in records for item in rec.items]
    if len(recorder.commits) != len(placed):
        raise SystemExit(f"Captura incompleta: {len(recorder.commits)} commits vs {len(placed)} en dump")
    for got, want in zip(recorder.commits, placed):
        if {k: got[k] for k in want} != want:
            raise SystemExit(f"La captura difiere del dump en box_id={want.get('box_id')}")

    steps, pallets = [], []
    step_no = 0
    for p_index, rec in enumerate(records, start=1):
        for i, item in enumerate(rec.items):
            step_no += 1
            box_id = item["box_id"]
            decision = recorder.decisions.get(str(box_id), {})
            conveyor = decision.get("conveyor", [])
            chosen = decision.get("chosen_index")
            if chosen is not None and not (0 <= chosen < len(conveyor) and str(conveyor[chosen]["box_id"]) == str(box_id)):
                raise SystemExit(f"Cinta incoherente para box_id={box_id}")
            events = []
            if i == 0:
                events.append(dict(type="open_pallet", pallet=p_index))
            if i == len(rec.items) - 1:
                events.append(dict(type="close_pallet", pallet=p_index, reason=rec.closure_reason,
                                   closed=rec.closed))
            steps.append(dict(
                step=step_no, pallet=p_index, dest=rec.dest, idx_in_pallet=i + 1, box_id=box_id,
                excel_row=rows.get(int(box_id)), orig_lwh=list(originals[int(box_id)]),
                orientation=item.get("orientation_name"), family=item.get("orientation_family"),
                x=item["x_mm"], y=item["y_mm"], z=item["z_mm"],
                dx=item["length_mm"], dy=item["width_mm"], dz=item["height_mm"],
                conveyor=conveyor, chosen_index=chosen,
                decision_time=decision.get("decision_time"), commit_time=recorder.commits[step_no - 1]["commit_time"],
                events=events))
        pallets.append(dict(index=p_index, dest=rec.dest, n_boxes=rec.n_boxes, closure_reason=rec.closure_reason,
                            closed=rec.closed, util=rec.box_volume_mm3 / prod(ENVELOPE_MM), z_max=rec.z_max_mm))
    add_step_metrics(steps)

    verification: dict = dict(capture_matches_dump=True)
    if verify:
        print(f"[sim3d] {config['name']} seed={seed}: verificando sin captura…", flush=True)
        _, plain_result, plain_files, _ = simulate(config, seed, folder / "plain", head, record=False)
        same_dump = plain_files["dump"]["pallets"] == dump["pallets"]
        same_kpi = plain_result["kpi"] == result["kpi"]
        verification.update(bit_identical=bool(same_dump and same_kpi), kpi_without_export=plain_result["kpi"],
                            kpi_with_export=result["kpi"])
        if not (same_dump and same_kpi):
            raise SystemExit(f"La exportación altera el resultado: KPI {plain_result['kpi']} vs {result['kpi']}")
    if reference is not None:
        ref = json.loads(Path(reference).read_text())
        verification.update(reference_path=str(reference), reference_kpi=ref["kpi"],
                            reference_git_head=ref.get("git_head"),
                            matches_reference=ref["kpi"] == result["kpi"]
                            and ref.get("n_pallets_total") == result["n_pallets_total"])

    params = task["params"]
    return dict(
        label=f"{config['name']} · seed {seed}", config=config["name"], config_file=str(config_path.relative_to(ROOT)),
        preset=config["preset"], overrides=config.get("overrides", {}), seed=seed, git_head=head, git_dirty=dirty,
        envelope=dict(x0=-OVERHANG_MM, y0=-OVERHANG_MM, x1=PALLET_NOMINAL_MM[0] + OVERHANG_MM,
                      y1=PALLET_NOMINAL_MM[1] + OVERHANG_MM, z0=0, z1=ENVELOPE_MM[2]),
        pallet_nominal=dict(length=PALLET_NOMINAL_MM[0], width=PALLET_NOMINAL_MM[1]),
        params={k: params.get(k) for k in ("min_support", "accessibility_delta_mm", "lookahead_k", "ramp_cap",
                                            "orientation_mode", "stand_hw_height_margin_gate_mm", "overhang_mm",
                                            "time_budget_ms", "micro_plan", "micro_plan_node_budget")},
        kpi={k: result[k] for k in ("metric_version", "kpi", "kpi_nominal", "n_pallets_total", "n_closed",
                                    "boxes_placed", "eff_real", "h_fill", "pallet_utils")},
        n_violations=result["n_violations"], violations=result["violations"],
        deadline_cutoffs_count=result["deadline_cutoffs_count"], hard_cap_hits=result["hard_cap_hits"],
        n_unplaced=result["n_unplaced"], verify=verification, pallets=pallets, steps=steps,
        changeovers=recorder.changeovers)


# --------------------------------------------------------------------------- sintético

def synthetic_run(stress: int = 0) -> dict:
    """Caso con defectos a propósito para validar las comprobaciones del visor."""
    boxes = []
    if stress:
        # Rejilla compacta y válida de cajas pequeñas para medir fluidez (>100 cajas).
        n = 0
        for layer in range(12):
            for i in range(4):
                for j in range(4):
                    if n >= stress:
                        break
                    boxes.append(((-20 + i * 310, -20 + j * 210, layer * 200), (310, 210, 200), "LWH", "sano"))
                    n += 1
    else:
        boxes = [
            ((-20, -20, 0), (600, 400, 300), "LWH", "sano"),
            ((580, -20, 0), (600, 400, 300), "LWH", "sano"),
            ((-20, 380, 0), (600, 440, 300), "LWH", "sano"),
            ((500, 380, 0), (400, 300, 250), "LWH", "SOLAPE con la caja 3 (80 mm en x)"),
            ((150, 400, 800), (250, 200, 200), "LWH", "FLOTANDO: nada debajo a z=800"),
            ((1000, 380, 0), (300, 300, 200), "LWH", "FUERA de la envolvente (x hasta 1300)"),
            ((-20, -20, 300), (600, 400, 900), "HWL", "sano (de canto); deja una vecina alta"),
            ((580, -20, 300), (300, 400, 300), "WLH", "ACCESIBILIDAD: la vecina 7 sube a 1200 > 300 + 400"),
            ((-20, 670, 300), (150, 150, 1200), "HWL", "TORRE exenta: 1200 / 150 = 8"),
            ((430, 600, 300), (300, 200, 200), "LWH", "APOYO 50 % (solo la mitad sobre la 3)"),
            ((900, 680, 0), (300, 140, 200), "LWH", "sano"),
            ((900, 380, 0), (100, 300, 200), "LWH", "0 LADOS LIBRES: hueco cerrado por 2, 4, 6 y 11"),
        ]
    steps = []
    for i, ((x, y, z), (dx, dy, dz), orient, note) in enumerate(boxes, start=1):
        lwh = {"LWH": (dx, dy, dz), "WLH": (dy, dx, dz), "HWL": (dz, dy, dx), "WHL": (dz, dx, dy)}[orient]
        steps.append(dict(step=i, pallet=1, dest="1", idx_in_pallet=i, box_id=1000 + i, excel_row=None,
                          orig_lwh=list(lwh), orientation=orient,
                          family="planar" if orient in ("LWH", "WLH") else "stand_hw",
                          x=x, y=y, z=z, dx=dx, dy=dy, dz=dz, note=note,
                          conveyor=[dict(box_id=1000 + i, lwh=list(lwh))], chosen_index=0, events=[]))
    steps[0]["events"].append(dict(type="open_pallet", pallet=1))
    steps[-1]["events"].append(dict(type="close_pallet", pallet=1, reason="SINTETICO", closed=True))
    add_step_metrics(steps)
    volume = sum(s["dx"] * s["dy"] * s["dz"] for s in steps)
    util = volume / prod(ENVELOPE_MM)
    return dict(
        label="sintético · estrés" if stress else "sintético · defectos a propósito",
        config="synthetic-stress" if stress else "synthetic", preset=None, seed=None, git_head=None, git_dirty=None,
        envelope=dict(x0=-20, y0=-20, x1=1220, y1=820, z0=0, z1=2400), pallet_nominal=dict(length=1200, width=800),
        params=dict(min_support=0.85, accessibility_delta_mm=400),
        kpi=dict(kpi=util, n_pallets_total=1, n_closed=1, boxes_placed=len(steps)),
        pallets=[dict(index=1, dest="1", n_boxes=len(steps), closure_reason="SINTETICO", closed=True, util=util,
                      z_max=max(s["z"] + s["dz"] for s in steps))],
        steps=steps, verify={}, n_violations=None, violations=[])


# --------------------------------------------------------------------------- salida

def attach_robot(runs: list[dict], study_path: Path) -> int:
    """Añade a cada run el resultado del estudio del robot (robot_reach_study.py) de su seed."""
    study = json.loads(Path(study_path).read_text(encoding="utf-8"))
    by_key = {(r["config"], r["seed"]): r for r in study["runs"]}
    attached = 0
    for run in runs:
        res = by_key.get((run.get("config"), run.get("seed")))
        if res is None:
            continue
        steps = {}
        for r in res["results"]:
            steps[r["step"]] = {k: r[k] for k in ("ok", "reason", "approach", "entry_lift_mm", "min_margin_deg",
                                                  "gripper_fits", "poses", "cycle")}
        n_ok = sum(1 for r in res["results"] if r["ok"])
        run["robot"] = dict(model=study["robot"], cell=study["cell"], gripper=study["gripper"],
                            conveyor=study["conveyor"], n_ok=n_ok, n=len(res["results"]),
                            pick_index_hist=study.get("pick_index_hist"), steps=steps)
        attached += 1
    return attached


def render_html(trace: dict, out: Path, robot_mesh: Path | None = None) -> None:
    html = TEMPLATE.read_text(encoding="utf-8")

    def inline(match: re.Match) -> str:
        path = TEMPLATE.parent / match.group(1)
        code = path.read_text(encoding="utf-8").replace("</script", "<\\/script")
        return f"<script>/* {match.group(1)} (incrustado) */\n{code}\n</script>"

    html = re.sub(r'<script src="([\w./-]+\.js)"></script>', inline, html)
    data = json.dumps(trace, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    marker = '<script id="sim-data" type="application/json">null</script>'
    if marker not in html:
        raise SystemExit("La plantilla no contiene el marcador de datos")
    html = html.replace(marker, f'<script id="sim-data" type="application/json">{data}</script>')
    if robot_mesh is not None:
        mesh = Path(robot_mesh).read_text(encoding="utf-8").replace("</", "<\\/")
        mesh_marker = '<script id="robot-mesh" type="application/json">null</script>'
        html = html.replace(mesh_marker, f'<script id="robot-mesh" type="application/json">{mesh}</script>')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", action="append", metavar="CONFIG",
                        help="Config de configs/sweeps (nombre o fichero.yaml:nombre). Repetible. Por defecto h6_sin_gate")
    parser.add_argument("--seeds", default="50021", help="Seeds separadas por comas (por defecto 50021)")
    parser.add_argument("--verify", action="store_true",
                        help="Repite cada simulación sin captura y exige resultado idéntico")
    parser.add_argument("--reference", type=Path, help="kpi.json oficial con el que comparar (una sola run)")
    parser.add_argument("--synthetic", action="store_true", help="Añade el caso sintético con defectos")
    parser.add_argument("--stress", type=int, default=0, help="Añade un palet sintético sano de N cajas")
    parser.add_argument("--no-sim", action="store_true", help="No simula (solo sintéticos / --from-json)")
    parser.add_argument("--from-json", type=Path, action="append", default=[],
                        help="Reutiliza las runs de un JSON ya exportado (repetible; implica --no-sim)")
    parser.add_argument("--out", type=Path, help="JSON de salida (por defecto out/sim3d/<nombre>.json)")
    parser.add_argument("--html", type=Path, help="Genera además el visor autocontenido con el JSON incrustado")
    parser.add_argument("--workdir", type=Path, help="Carpeta para run.json/dump.json (por defecto temporal)")
    parser.add_argument("--robot-study", type=Path, help="study.json de robot_reach_study.py: añade robot y ciclos")
    parser.add_argument("--robot-mesh", type=Path, help="Mallas del robot (step_to_robot_mesh.py) para el HTML")
    return parser


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    seeds = [int(s) for s in str(args.seeds).split(",") if s.strip()]
    specs = [] if (args.no_sim or (args.from_json and not args.run)) else (args.run or ["h6_sin_gate"])
    if args.reference and len(specs) * len(seeds) != 1:
        raise SystemExit("--reference solo con una config y una seed")
    runs = []
    for path in args.from_json:
        previous = json.loads(Path(path).read_text(encoding="utf-8"))
        if previous.get("schema") != SCHEMA:
            raise SystemExit(f"{path}: esquema {previous.get('schema')!r}, se esperaba {SCHEMA}")
        runs.extend(previous["runs"])
    with tempfile.TemporaryDirectory(prefix="sim3d-") as tmp:
        base = args.workdir or Path(tmp)
        for spec in specs:
            config, path = resolve_config(spec)
            for seed in seeds:
                runs.append(build_run(config, path, seed, base, args.verify, args.reference))
    if args.synthetic:
        runs.append(synthetic_run())
    if args.stress:
        runs.append(synthetic_run(stress=args.stress))
    if not runs:
        raise SystemExit("Nada que exportar")
    if args.robot_study:
        print(f"[sim3d] robot añadido a {attach_robot(runs, args.robot_study)} ejecuciones", flush=True)
    trace = dict(schema=SCHEMA, generated_at=datetime.now(timezone.utc).isoformat(),
                 conventions=("mm; origen esquina del palet nominal; z vertical desde la cara superior del palet; "
                              "x,y,z = esquina mínima; dx,dy,dz = dimensiones colocadas (x,y,z)"),
                 runs=runs)
    name = "_".join(sorted({r["config"] for r in runs})) + ("_" + "-".join(map(str, seeds)) if specs else "")
    out = args.out or DEFAULT_OUT / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(trace, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[sim3d] JSON: {out}", flush=True)
    if args.html:
        render_html(trace, args.html, args.robot_mesh)
        print(f"[sim3d] HTML: {args.html}", flush=True)
    for run in runs:
        kpi = run["kpi"].get("kpi")
        print(f"[sim3d] {run['label']}: KPI={kpi} palets={run['kpi'].get('n_pallets_total')} "
              f"cajas={len(run['steps'])} verify={run.get('verify')}", flush=True)


if __name__ == "__main__":
    main()
