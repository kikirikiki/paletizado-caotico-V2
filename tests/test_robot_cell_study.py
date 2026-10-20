import numpy as np
import pytest

from palca.robot.cell_study import (
    Placement, RobotModel, approach_dir, box_conveyor_matrix, box_final_matrix, collision_points, corridor_top,
    cycle, evaluate_run)
from palca.robot.r2000 import Cell, tool_frame, ik_world


def _cell():
    return Cell(base_x=700, base_y=1820, pedestal_mm=900, yaw_deg=-94.03)


def _step(x, y, z, dx, dy, dz, orient="LWH", lwh=None, step=1):
    return dict(step=step, pallet=1, x=x, y=y, z=z, dx=dx, dy=dy, dz=dz, orientation=orient,
                orig_lwh=list(lwh or (dx, dy, dz)))


@pytest.mark.parametrize("orient, lwh, placed", [
    ("LWH", (600, 400, 300), (600, 400, 300)), ("WLH", (600, 400, 300), (400, 600, 300)),
    ("HWL", (600, 400, 300), (300, 400, 600)), ("WHL", (600, 400, 300), (400, 300, 600)),
])
def test_box_frames_are_proper_rotations_matching_dims(orient, lwh, placed):
    m = box_final_matrix(_step(0, 0, 0, *placed, orient=orient, lwh=lwh))
    r = m[:3, :3]
    assert np.isclose(np.linalg.det(r), 1.0)
    assert np.allclose(np.abs(r @ np.array(lwh, float)), placed)
    # La cara cogida queda arriba: +H (plana) o +L (de canto).
    up = r.T @ np.array([0, 0, 1.0])
    assert np.allclose(up, [0, 0, 1] if orient in ("LWH", "WLH") else [1, 0, 0])
    conv = box_conveyor_matrix(_step(0, 0, 0, *placed, orient=orient, lwh=lwh), (2000, 0, 500), (1, 0, 0))
    assert np.isclose(np.linalg.det(conv[:3, :3]), 1.0)


def test_approach_dir_prefers_largest_free_clearance():
    box = (400, 200, 0, 400, 400, 300)
    # Vecina pegada en -x y otra a 50 mm en -y: gana +x (holgura infinita) frente a +y por el borde más cercano.
    assert approach_dir(box, [(0, 200, 0, 400, 400, 300)]) in [(1, 0), (0, 1), (0, -1)]
    assert approach_dir(box, [(0, 200, 0, 400, 400, 300), (400, -250, 0, 400, 400, 300),
                              (400, 600, 0, 400, 400, 300)]) == (1, 0)
    enclosed = [(-400, 0, 0, 400, 400, 300), (400, 0, 0, 400, 400, 300), (0, -400, 0, 400, 400, 300), (0, 400, 0, 400, 400, 300)]
    assert approach_dir((0, 0, 0, 400, 400, 300), enclosed) is None


def test_corridor_counts_only_boxes_between_target_and_robot():
    far_tower = (0, -20, 0, 400, 300, 2000)       # al fondo (y pequeña), robot en y=1820
    near_tower = (0, 500, 0, 400, 300, 1500)
    assert corridor_top((200, 450), (150, 150), (700, 1820), [far_tower]) == 0
    assert corridor_top((200, 450), (150, 150), (700, 1820), [near_tower]) == 1500


def test_evaluate_run_detects_forearm_in_canyon():
    model = RobotModel(_cell(), collision_points(None))  # cajas del STEP como respaldo
    open_box = Placement(1, 1, (400, 200, 0, 400, 400, 300), "LWH", (400, 400, 300))
    ok = evaluate_run(model, [open_box])
    assert ok[0].ok, ok[0].reason
    # Caja al suelo entre dos columnas de 2,2 m, una de ellas entre la caja y el robot.
    walls = [Placement(1, 1, (300, 600, 0, 600, 220, 2200), "LWH", (600, 220, 2200)),
             Placement(2, 1, (300, -20, 0, 600, 220, 2200), "LWH", (600, 220, 2200)),
             Placement(3, 1, (450, 210, 0, 300, 380, 300), "LWH", (300, 380, 300))]
    res = evaluate_run(model, walls)
    assert not res[2].ok and ("colision" in res[2].reason or "alcance" in res[2].reason)


def test_cycle_box_follows_tool_and_lands_exactly():
    cell = _cell()
    model = RobotModel(cell, collision_points(None))
    step = _step(400, 200, 0, 300, 400, 600, orient="HWL", lwh=(600, 400, 300))
    res = evaluate_run(model, [Placement(1, 1, (400, 200, 0, 300, 400, 600), "HWL", (600, 400, 300))], keep_poses=True)[0]
    assert res.ok, res.reason
    conveyor = dict(point=[2286.0, 3197.0, 956.0], direction=[0.6894, 0.7244, 0.0], z_conveyor=956.0)
    cyc = cycle(model, step, res, conveyor, (0, 0, 0, 0, -90, 0))
    kinds = [w["kind"] for w in cyc["waypoints"]]
    assert kinds[:4] == ["sobre_recogida", "recogida", "levantar", "reorientar"] and kinds[-1] == "retirada"
    rel = np.array(cyc["box_rel"])
    world = cell.world_matrix()
    q_final = next(w["q"] for w in cyc["waypoints"] if w["kind"] == "final")
    q_pick = next(w["q"] for w in cyc["waypoints"] if w["kind"] == "recogida")
    assert np.allclose(world @ tool_frame(cell.arm, cell.gripper, q_final) @ rel, box_final_matrix(step), atol=1e-3)
    assert np.allclose(world @ tool_frame(cell.arm, cell.gripper, q_pick) @ rel, np.array(cyc["box_conveyor"]), atol=0.05)
