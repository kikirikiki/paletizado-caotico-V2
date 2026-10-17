"""Fuente de verdad del estudio; no modifica los defaults del simulador."""

from copy import deepcopy
from inspect import signature

from palca.domain.pallet_spec import PalletSpec

from .orientation_faces import assert_mode_allowed

REFERENCE_EXCEL = "data/Flujo rampas - Editado.xlsx"
REFERENCE_SEEDS = [50021, 1, 42, 100, 777, 5678, 9999, 12345, 99999, 314159]

# Copia de study/gantry-orientations:studies/gantry_orientations/benchmark.py
# (incluye out_path, obligatorio en run_simulation, y el Excel del worker).
BASE_PARAMS: dict = dict(
    model="M1",
    n_per_pallet=999999,
    t_pick_place=14.0,
    staging_cap=0,
    out_path=None,
    ramp_cap=15,
    policy="palca",
    lookahead_k=15,
    arrival_mode="immediate",
    shuffle_window=15,
    shuffle_strength=1.0,
    overhang_mm=20,
    heuristic="bssf",
    stacking_mode="heightfield",
    stability_mode="ratio+corners+settle",
    min_support=0.85,
    score_mode="min_height_slack_then_gain",
    height_slack_mm=120,
    stand_hw_height_margin_gate_mm=400,
    time_budget_ms=900,
    micro_plan=False,
    force_destination=1,
    continuous_pallets=True,
    max_pallets=0,
    excel_path=REFERENCE_EXCEL,
)

PRESETS: dict[str, dict] = {
    "arm6_4orient": dict(
        deepcopy(BASE_PARAMS), orientation_mode="planar+stand_hw", accessibility_delta_mm=400
    ),
    "gantry_4orient": dict(
        deepcopy(BASE_PARAMS), orientation_mode="planar+stand_hw", accessibility_delta_mm=0
    ),
    "arm6_planar": dict(
        deepcopy(BASE_PARAMS), orientation_mode="planar", accessibility_delta_mm=400
    ),
    # PALANCA / PLAN_B: garra alternativa que permite apoyar HxL; no oficial arm6.
    "lever_arm_canto": dict(
        deepcopy(BASE_PARAMS), orientation_mode="planar+stand_hw+canto", accessibility_delta_mm=400
    ),
    # CONTRASTE informativo: gantry con seis orientaciones geométricas reales.
    "gantry_6orient": dict(
        deepcopy(BASE_PARAMS), orientation_mode="planar+stand_hw+canto", accessibility_delta_mm=0
    ),
}

_PALLET = PalletSpec()
PALLET_NOMINAL_MM = (_PALLET.length_mm, _PALLET.width_mm)
MAX_LOAD_HEIGHT_MM = _PALLET.max_height_mm


def pallet_envelope_mm(overhang_mm: int) -> tuple[int, int, int]:
    """Envolvente efectiva: el overhang se aplica a ambos lados de cada eje."""
    spec = PalletSpec(overhang_mm=overhang_mm)
    return spec.bin_length_mm, spec.bin_width_mm, spec.max_height_mm


def preset_params(name: str, **overrides) -> dict:
    """Devuelve una copia profunda y valida overrides contra la API real."""
    from sim.run import run_simulation

    if name not in PRESETS:
        raise ValueError(f"Preset desconocido: {name}")
    invalid = overrides.keys() - signature(run_simulation).parameters.keys()
    if invalid:
        raise ValueError(f"Parámetros de run_simulation desconocidos: {', '.join(sorted(invalid))}")
    params = deepcopy(PRESETS[name])
    params.update(deepcopy(overrides))
    if name.startswith("arm6_"):
        assert_mode_allowed(params["orientation_mode"])
    return params
