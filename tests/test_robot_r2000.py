import numpy as np
import pytest

from palca.robot.r2000 import ArmGeometry, Cell, Gripper, ik, ik_world, link_frames, tool_frame

ARM, GRIP = ArmGeometry(), Gripper()


def test_zero_pose_matches_step_axes():
    frames = link_frames(ARM, (0, 0, 0, 0, 0, 0))
    assert np.allclose(frames[2][:3, 3], [312, 0, 670])     # J2
    assert np.allclose(frames[3][:3, 3], [312, 0, 1745])    # J3
    assert np.allclose(frames[5][:3, 3], [1592, 0, 1970])   # centro de muñeca (J5)
    assert np.allclose(frames[7][:3, 3], [1807, 0, 1970])   # brida
    tcp = tool_frame(ARM, GRIP, (0, 0, 0, 0, 0, 0))
    assert np.allclose(tcp[:3, 3], [2007, 0, 1970])
    assert np.allclose(tcp[:3, 2], [-1, 0, 0])               # z herramienta hacia la brida


@pytest.mark.parametrize("q", [
    (10, 20, -10, 0, -80, 30), (-60, 40, -30, 5, -60, -100), (120, -20, 10, -15, -95, 200),
    (0, 0, 0, 30, -90, 0), (-150, 60, -60, 0, -30, 45),
])
def test_ik_inverts_fk(q):
    tcp = tool_frame(ARM, GRIP, q)
    approach = -tcp[:3, 2]
    res = ik(ARM, GRIP, tcp[:3, 3], approach, tcp[:3, 0], prefer=q)
    assert res.q is not None
    back = tool_frame(ARM, GRIP, res.q)
    assert np.allclose(back[:3, 3], tcp[:3, 3], atol=1e-6)
    assert np.allclose(back[:3, :3], tcp[:3, :3], atol=1e-6)


def test_tool_down_needs_forearm_below_35_deg():
    # Herramienta vertical: J5 = 90 + J3, así que J3 > 35 rompe el límite de J5 (±125).
    cell = Cell(base_x=600, base_y=-1200, pedestal_mm=0, yaw_deg=90)
    near = ik_world(cell, (600, 400, 1500), (0, 0, -1), (1, 0, 0))
    assert near.reason is None and abs(abs(near.q[4]) - (90 + near.q[2])) < 1e-6
    far = ik_world(cell, (600, 1600, 2600), (0, 0, -1), (1, 0, 0))
    assert far.reason is not None
