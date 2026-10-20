import numpy as np
import pytest

from palca.robot.cell_study import Placement, RobotModel, evaluate_run
from palca.robot.gantry import GantryCell, GantryDynamics, GantryFrame, GantryModel, c_of, trapezoid_time
from palca.robot.gantry_study import (
    b_axis_cycle_s, conveyor_layout, final_tool, pick_plan, plan_cycles, queue_offsets, size_cell, structure_checks,
    tipper_rotation)
from palca.robot.r2000 import Cell

UP = np.array([0, 0, 1.0])


def _step(x, y, z, dx, dy, dz, orient="LWH", lwh=None, step=1, queue=None, idx=0):
    lwh = list(lwh or (dx, dy, dz))
    return dict(step=step, pallet=1, x=x, y=y, z=z, dx=dx, dy=dy, dz=dz, orientation=orient, orig_lwh=lwh,
                conveyor=queue or [dict(box_id=step, lwh=lwh)], chosen_index=idx)


def _cell(**frame):
    conv = conveyor_layout("N", "E", 900)
    return GantryCell(x_range=(-8000, 2000), y_range=(-200, 1600), z_range=(0, 3300),
                      frame=GantryFrame(**frame), conveyor=conv, tipper=conv["tipper"])


def test_trapezoid_time_triangular_and_cruise():
    assert trapezoid_time(0, 3000, 3000) == 0
    assert trapezoid_time(750, 3000, 3000) == pytest.approx(2 * np.sqrt(0.25))       # triangular
    assert trapezoid_time(6000, 3000, 3000) == pytest.approx(6000 / 3000 + 1.0)      # crucero
    dyn = GantryDynamics()
    # Ejes sincronizados: manda el más lento; la aproximación fina añade 50 mm a 0,3 m/s.
    assert dyn.move_time((0, 0, 1000, 0), (6000, 100, 1000, 0)) == pytest.approx(3.0)
    assert dyn.move_time((0, 0, 1000, 0), (0, 0, 500, 0), fine=True) == pytest.approx(
        trapezoid_time(450, *dyn.z) + 50 / 300)


def test_solve_aligns_gripper_and_reports_axis_limits():
    m = GantryModel(_cell())
    r = m.solve((500, 300, 800), (0, 0, 0, 600, 400, 300), (0, 0, 0, 170))
    assert r.reason is None and r.q[3] == pytest.approx(180)       # paso largo según x, cerca de 170°
    assert abs(m.solve((500, 300, 800), (0, 0, 0, 400, 600, 300), (0, 0, 0, 0)).q[3]) == pytest.approx(90)
    assert m.solve((500, 300, 4000), (0, 0, 0, 600, 400, 300), (0, 0, 0, 0)).reason == "limite_Z"


def test_head_wider_than_face_hits_taller_neighbours():
    # Caja de canto de 260 mm de ancho entre dos columnas pegadas 600 mm más altas.
    prev = [Placement(1, 1, (0, 0, 0, 400, 500, 900), "LWH", (400, 500, 900)),
            Placement(2, 1, (660, 0, 0, 400, 500, 900), "LWH", (400, 500, 900))]
    box = Placement(3, 1, (400, 0, 0, 260, 500, 300), "HWL", (300, 500, 260))
    narrow = evaluate_run(GantryModel(_cell(head_d_mm=250)), prev + [box])
    wide = evaluate_run(GantryModel(_cell(head_d_mm=300)), prev + [box])
    assert narrow[2].ok
    assert not wide[2].ok and "colision:cabezal_c" in wide[2].reason


@pytest.mark.parametrize("orient, lwh, placed", [
    ("LWH", (600, 400, 300), (600, 400, 300)), ("WLH", (600, 400, 300), (400, 600, 300)),
    ("HWL", (600, 400, 300), (300, 400, 600)), ("WHL", (600, 400, 300), (400, 300, 600)),
])
def test_pick_chain_keeps_tool_vertical_and_box_consistent(orient, lwh, placed):
    conv = conveyor_layout("N", "E", 900)
    queue = [dict(box_id=9, lwh=[500, 350, 300]), dict(box_id=1, lwh=list(lwh))]
    step = _step(100, 100, 0, *placed, orient=orient, lwh=lwh, queue=queue, idx=1)
    pk = pick_plan(step, conv, 0.0)
    assert pk["queue_offset"] == pytest.approx(queue_offsets(queue, conv)[1])
    assert np.allclose(pk["t_pick"][:3, 2], UP)
    t_fin, b_fin = final_tool(step, pk["rel"], 0.0)
    assert np.allclose(t_fin[:3, 2], UP)                     # herramienta vertical al colocar
    assert np.allclose(t_fin @ pk["rel"], b_fin)             # la caja va rígida con la garra
    assert np.allclose(b_fin[:3, 3], [100 + placed[0] / 2, 100 + placed[1] / 2, placed[2] / 2])
    assert np.allclose(np.abs(b_fin[:3, :3] @ np.array(lwh, float)), placed)
    if orient in ("HWL", "WHL"):
        # En la cuna la caja queda de pie: L vertical y la cara H×W arriba, a la altura de la cinta + L.
        assert np.allclose(np.abs(pk["b_up"][:3, 0]), UP)
        assert pk["t_pick2"][2, 3] == pytest.approx(conv["z_conveyor"] + lwh[0])
        assert np.allclose(tipper_rotation(conv["tipper"]) @ pk["b_tipper"], pk["b_up"])


def test_cycle_and_structure_on_small_run():
    queue = [dict(box_id=i, lwh=[600, 400, 300]) for i in range(3)]
    steps = [_step(-20, -20, 0, 600, 400, 300, step=1, queue=queue, idx=2),
             _step(580, -20, 0, 300, 400, 600, orient="HWL", lwh=(600, 400, 300), step=2, queue=queue, idx=0)]
    run = dict(config="t", seed=0, steps=steps)
    cell, picks = size_cell([run], conveyor_layout("N", "E", 900))
    from palca.robot.gantry_study import evaluate_cell
    (res, cyc), = evaluate_cell(cell, [run], picks)
    assert all(r.ok for r in res)
    kinds = [w["kind"] for w in cyc[1]["waypoints"]]
    assert "volteo" in kinds and kinds[-2:] == ["final", "retirada"]
    assert cyc[1]["cycle_s"] > cyc[0]["cycle_s"] > 0
    assert b_axis_cycle_s(cyc[1]) < cyc[1]["cycle_s"]
    for w in cyc[0]["waypoints"] + cyc[1]["waypoints"]:   # todo dentro de las carreras dimensionadas
        for v, (lo, hi) in zip(w["q"][:3], cell.limits[:3]):
            assert lo - 1 <= v <= hi + 1
    assert all(c["ok"] for c in structure_checks(cell))
    h = cell.heights()
    assert h["building_fixed"] > h["building_telescopic"] > h["bridge_top"] > 2544


def test_arm_model_hooks_are_neutral():
    m = RobotModel(Cell(base_x=700, base_y=1820, pedestal_mm=900, yaw_deg=-94.03), {"base": np.zeros((1, 3))})
    assert m.home_q == (0, 0, 0, 0, -90, 0)
    assert m.entry_xy(None) == (700, 1820) and m.entry_floor_z(None) == -np.inf
    assert c_of(np.eye(4)) == 0
