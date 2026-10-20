/* Robot FANUC R-2000iC/165F para el visor sim3d: cinemática directa (idéntica a
 * palca.robot.r2000.link_frames), mallas por eslabón (scripts/step_to_robot_mesh.py),
 * garra de 4 ventosas, pedestal y cinta. Las poses vienen precalculadas por
 * scripts/robot_reach_study.py; aquí solo se interpolan articulaciones.
 */
(function (root) {
  'use strict';
  var T = root.THREE;
  var ARM = { a1: 312, d1: 670, a2: 1075, a3: 225, d4: 1280, d6: 215 };
  var JOINT_ORIGINS = {
    base: [0, 0, 0], j1: [0, 0, 0], j2: [312, 0, 670], j3: [312, 0, 1745], j4: [312, 0, 1970],
    j5: [1592, 0, 1970], j6: [1592, 0, 1970], balancer_case: [-470, 189, 621], balancer_rod: [98, 189, 652]
  };
  var FRAME_OF = { base: 0, j1: 1, j2: 2, j3: 3, j4: 4, j5: 5, j6: 6 };
  var FRAME_ORIGIN = [[0, 0, 0], [0, 0, 0], [312, 0, 670], [312, 0, 1745], [312, 0, 1970], [1592, 0, 1970], [1592, 0, 1970]];
  var D2R = Math.PI / 180;

  function tr(x, y, z) { return new T.Matrix4().makeTranslation(x, y, z); }
  function rx(a) { return new T.Matrix4().makeRotationX(a); }
  function ry(a) { return new T.Matrix4().makeRotationY(a); }
  function rz(a) { return new T.Matrix4().makeRotationZ(a); }
  function mul() { var m = new T.Matrix4(); for (var i = 0; i < arguments.length; i++) m.multiply(arguments[i]); return m; }

  /* Marcos en la base del robot: base, J1..J6 y brida (q en grados, convenio FANUC). */
  function frames(q) {
    var q1 = q[0] * D2R, q2 = q[1] * D2R, q3 = q[2] * D2R, q4 = q[3] * D2R, q5 = q[4] * D2R, q6 = q[5] * D2R;
    var f0 = new T.Matrix4();
    var f1 = rz(q1);
    var f2 = mul(f1, tr(ARM.a1, 0, ARM.d1), ry(q2));
    var f3 = mul(f2, tr(0, 0, ARM.a2), ry(-(q3 + q2)));
    var f4 = mul(f3, tr(0, 0, ARM.a3), rx(q4));
    var f5 = mul(f4, tr(ARM.d4, 0, 0), ry(q5));
    var f6 = mul(f5, rx(q6));
    var fl = mul(f6, tr(ARM.d6, 0, 0));
    return [f0, f1, f2, f3, f4, f5, f6, fl];
  }
  var TOOL_ROT = new T.Matrix4().set(0, 0, -1, 0, 1, 0, 0, 0, 0, -1, 0, 0, 0, 0, 0, 1);
  function toolLocal(q, tcpMm) { return mul(frames(q)[7], tr(tcpMm, 0, 0), TOOL_ROT); }

  function cellMatrix(cell) {
    return mul(tr(cell.base_x, cell.base_y, cell.base_z), rz(cell.yaw_deg * D2R));
  }

  function decodeMesh(link) {
    var pos = Uint8Array.from(atob(link.positions), function (c) { return c.charCodeAt(0); });
    var idx = Uint8Array.from(atob(link.indices), function (c) { return c.charCodeAt(0); });
    var g = new T.BufferGeometry();
    g.setAttribute('position', new T.BufferAttribute(new Float32Array(pos.buffer), 3));
    g.setIndex(new T.BufferAttribute(new Uint32Array(idx.buffer), 1));
    g.computeVertexNormals();
    return g;
  }
  /* Respaldo sin mallas: cajas de cada pieza en pose cero (de palca.robot.cell_study.STEP_AABB). */
  var AABB = {
    base: [[-423, -307, 0], [348, 305, 336]], j1: [[-355, -328, 215], [519, 419, 849]], j2: [[41, -6, 483], [509, 418, 1909]],
    j3: [[142, -214, 1563], [1361, 417, 2113]], j4: [[1361, -93, 1877], [1685, 93, 2063]], j5: [[1479, -150, 1857], [1796, 115, 2083]],
    j6: [[1770, -80, 1892], [1807, 80, 2050]]
  };
  function boxGeo(lo, hi, origin) {
    var g = new T.BoxGeometry(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]);
    g.translate((lo[0] + hi[0]) / 2 - origin[0], (lo[1] + hi[1]) / 2 - origin[1], (lo[2] + hi[2]) / 2 - origin[2]);
    return g;
  }

  function buildGripper(g) {
    var grp = new T.Group();
    var steel = new T.MeshStandardMaterial({ color: 0x9aa0a6, metalness: 0.6, roughness: 0.4 });
    var orange = new T.MeshStandardMaterial({ color: 0xf26a1b, roughness: 0.7 });
    var hx = g.pitch_x_mm / 2, hy = g.pitch_y_mm / 2, r = g.cup_diameter_mm / 2, ch = g.cup_height_mm, fh = g.frame_height_mm;
    [[hx, hy], [-hx, hy], [hx, -hy], [-hx, -hy]].forEach(function (p) {
      var cup = new T.Mesh(new T.CylinderGeometry(r, r * 0.55, ch, 28), orange);
      cup.rotation.x = Math.PI / 2; cup.position.set(p[0], p[1], ch / 2); grp.add(cup);
    });
    function tube(len, x, y, z, alongX) {
      var t = new T.Mesh(new T.CylinderGeometry(16, 16, len, 14), steel);
      if (alongX) t.rotation.z = Math.PI / 2; t.position.set(x, y, z); grp.add(t);
    }
    var zf = ch + fh / 2;
    tube(2 * hx + 40, 0, hy, zf, true); tube(2 * hx + 40, 0, -hy, zf, true);
    tube(2 * hy + 40, hx, 0, zf, false); tube(2 * hy + 40, -hx, 0, zf, false);
    tube(2 * hy, 0, 0, zf, false);
    var col = new T.Mesh(new T.CylinderGeometry(45, 45, g.flange_to_tcp_mm - ch - fh, 20), steel);
    col.rotation.x = Math.PI / 2; col.position.set(0, 0, (g.flange_to_tcp_mm + ch + fh) / 2); grp.add(col);
    var plate = new T.Mesh(new T.CylinderGeometry(110, 110, 14, 32), steel);
    plate.rotation.x = Math.PI / 2; plate.position.set(0, 0, g.flange_to_tcp_mm - 7); grp.add(plate);
    grp.traverse(function (o) { if (o.isMesh) { o.castShadow = true; } });
    return grp;
  }

  /* robotData = run.robot (cell, gripper, conveyor); meshData = palca-robot-mesh/1 o null. */
  function Robot(scene, robotData, meshData) {
    this.data = robotData;
    this.cell = robotData.cell;
    this.tcp = robotData.gripper.flange_to_tcp_mm;
    this.cellM = cellMatrix(this.cell);
    this.group = new T.Group();
    this.group.matrixAutoUpdate = false;
    this.group.matrix.copy(this.cellM);
    scene.add(this.group);
    this.links = {};
    var self = this;
    var names = ['base', 'j1', 'j2', 'j3', 'j4', 'j5', 'j6', 'balancer_case', 'balancer_rod'];
    names.forEach(function (n) {
      var geo = null, color = n === 'base' || n === 'j6' || n === 'balancer_case' ? 0x2e2e2e : n === 'balancer_rod' ? 0xb0b0b0 : 0xf2c500;
      if (meshData && meshData.links[n]) { geo = decodeMesh(meshData.links[n]); color = new T.Color(meshData.links[n].color); }
      else if (AABB[n]) geo = boxGeo(AABB[n][0], AABB[n][1], JOINT_ORIGINS[n]);
      if (!geo) return;
      var mat = new T.MeshStandardMaterial({ color: color, metalness: 0.15, roughness: 0.55 });
      var m = new T.Mesh(geo, mat);
      m.matrixAutoUpdate = false; m.castShadow = true; m.receiveShadow = true;
      m.userData.baseColor = mat.color.clone();
      self.group.add(m); self.links[n] = m;
    });
    this.gripper = buildGripper(robotData.gripper);
    this.gripper.matrixAutoUpdate = false;
    this.group.add(this.gripper);
    // Pedestal.
    var ped = this.cell.pedestal_mm;
    if (ped > 1) {
      var pm = new T.Mesh(new T.BoxGeometry(900, 900, ped), new T.MeshStandardMaterial({ color: 0x59606a, roughness: 0.8 }));
      pm.position.set(this.cell.base_x, this.cell.base_y, this.cell.base_z - ped / 2);
      pm.castShadow = pm.receiveShadow = true; scene.add(pm); this.pedestal = pm;
    }
    this.conveyor = robotData.conveyor ? buildConveyor(scene, robotData.conveyor) : null;
    this.setQ([0, 0, 0, 0, -90, 0]);
  }
  Robot.prototype.setQ = function (q, badLink) {
    this.q = q.slice();
    var f = frames(q), self = this;
    Object.keys(this.links).forEach(function (n) {
      var m = self.links[n];
      if (n === 'balancer_case' || n === 'balancer_rod') return;
      var fi = FRAME_OF[n], o = JOINT_ORIGINS[n], fo = FRAME_ORIGIN[fi];
      m.matrix.copy(f[fi]).multiply(tr(o[0] - fo[0], o[1] - fo[1], o[2] - fo[2]));
      m.material.color.copy(n === badLink ? new T.Color(0xff2a2a) : m.userData.baseColor);
    });
    // Compensador: la camisa pivota en el carro y apunta al bulón del brazo.
    if (this.links.balancer_case && this.links.balancer_rod) {
      var pin = new T.Vector3(98 - 312, 189, 652 - 670).applyMatrix4(f[2]);
      var inv1 = new T.Matrix4().copy(f[1]).invert();
      var ql = pin.clone().applyMatrix4(inv1), p0 = JOINT_ORIGINS.balancer_case;
      var a0 = Math.atan2(652 - 621, 98 + 470), a = Math.atan2(ql.z - p0[2], ql.x - p0[0]);
      var rot = ry(-(a - a0));
      this.links.balancer_case.matrix.copy(f[1]).multiply(tr(p0[0], p0[1], p0[2])).multiply(rot);
      this.links.balancer_rod.matrix.copy(f[1]).multiply(tr(ql.x, ql.y, ql.z)).multiply(rot);
    }
    this.gripper.matrix.copy(toolLocal(q, this.tcp));
  };
  Robot.prototype.toolWorld = function (q) { return mul(this.cellM, toolLocal(q, this.tcp)); };
  Robot.prototype.setVisible = function (v) {
    this.group.visible = v; if (this.pedestal) this.pedestal.visible = v; if (this.conveyor) this.conveyor.group.visible = v;
  };
  Robot.prototype.dispose = function (scene) {
    scene.remove(this.group); if (this.pedestal) scene.remove(this.pedestal); if (this.conveyor) scene.remove(this.conveyor.group);
  };

  function buildConveyor(scene, c) {
    var g = new T.Group();
    var u = new T.Vector3().fromArray(c.direction), t = new T.Vector3().fromArray(c.tangent);
    var p = new T.Vector3().fromArray(c.point);
    // Longitud, ancho y tramo aguas abajo del punto de recogida: por defecto, los de la estación del brazo.
    var len = c.length_mm || 8500, width = c.width_mm || 800, h = c.z_conveyor + 144;
    var down = c.downstream_mm == null ? 600 : c.downstream_mm;
    var basis = new T.Matrix4().makeBasis(t, u, new T.Vector3(0, 0, 1));
    var frame = new T.Mesh(new T.BoxGeometry(len, width, 60), new T.MeshStandardMaterial({ color: 0x4a5560, roughness: 0.7 }));
    frame.applyMatrix4(basis); frame.position.copy(p).addScaledVector(t, -len / 2 + down).add(new T.Vector3(0, 0, -30));
    frame.receiveShadow = true; g.add(frame);
    var rollerMat = new T.MeshStandardMaterial({ color: 0xc8ccd2, metalness: 0.7, roughness: 0.3 });
    for (var s = -len + down; s <= down; s += 120) {
      var r = new T.Mesh(new T.CylinderGeometry(25, 25, width - 40, 10), rollerMat);
      r.applyMatrix4(basis); r.position.copy(p).addScaledVector(t, s).add(new T.Vector3(0, 0, -25)); g.add(r);
    }
    var legMat = new T.MeshStandardMaterial({ color: 0x3b4148 });
    for (var s2 = -len + down + 200; s2 <= down; s2 += 1500) {
      [-1, 1].forEach(function (k) {
        var leg = new T.Mesh(new T.BoxGeometry(60, 60, h - 60), legMat);
        leg.position.copy(p).addScaledVector(t, s2).addScaledVector(u, k * (width / 2 - 40)).add(new T.Vector3(0, 0, -60 - (h - 60) / 2));
        g.add(leg);
      });
    }
    // Marca del punto de recogida.
    var mark = new T.Mesh(new T.RingGeometry(60, 80, 32), new T.MeshBasicMaterial({ color: 0x00c8ff, side: T.DoubleSide }));
    mark.position.copy(p).add(new T.Vector3(0, 0, 2)); g.add(mark);
    scene.add(g);
    return { group: g, point: p, u: u, t: t, queue: [],
             headOffset: c.queue_head_offset_mm == null ? 450 : c.queue_head_offset_mm,
             gap: c.queue_gap_mm == null ? 15 : c.queue_gap_mm };
  }

  Robot.prototype.kind = 'arm';
  Robot.prototype.home = [0, 0, 0, 0, -90, 0];

  root.Sim3DRobot = { frames: frames, toolLocal: toolLocal, cellMatrix: cellMatrix, Robot: Robot,
                      buildGripper: buildGripper, buildConveyor: buildConveyor };
})(typeof window !== 'undefined' ? window : globalThis);
