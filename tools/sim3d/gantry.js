/* Gantry XYZC para el visor sim3d: pórtico de 4 patas (o más), carriles X, puente Y,
 * carro y columna Z, cabezal C y la misma garra de 4 ventosas que el brazo. Volteador de
 * 90° en línea con la cinta. Misma interfaz que Sim3DRobot.Robot (setQ, toolWorld,
 * setVisible, dispose, conveyor, home); q = [x, y, z, c] = TCP en el mundo y giro C (°).
 * Geometría y ciclos precalculados por scripts/gantry_reach_study.py (palca.robot.gantry).
 */
(function (root) {
  'use strict';
  var T = root.THREE;
  var D2R = Math.PI / 180;

  function boxMesh(sx, sy, sz, mat) {
    var m = new T.Mesh(new T.BoxGeometry(sx, sy, sz), mat);
    m.castShadow = true; m.receiveShadow = true;
    return m;
  }
  function std(color, metal) { return new T.MeshStandardMaterial({ color: color, metalness: metal || 0.3, roughness: 0.55 }); }

  function Gantry(scene, robotData) {
    this.kind = 'gantry';
    this.data = robotData;
    this.cell = robotData.cell;
    var g = this.g = robotData.gantry, f = g.frame, fz = g.floor_z, self = this;
    this.tcp = robotData.gripper.flange_to_tcp_mm;
    this.group = new T.Group();
    scene.add(this.group);
    var steel = std(0x3f6fb5, 0.35), dark = std(0x2b2f36, 0.4), yellow = std(0xf2c500, 0.15);
    var H = function (k) { return g.heights[k] + fz; };   // alturas desde el suelo -> z de la célula
    // Patas.
    var postTop = H('post');
    g.posts.forEach(function (p) {
      var m = boxMesh(f.post_mm, f.post_mm, postTop - fz, steel);
      m.position.set(p[0], p[1], fz + (postTop - fz) / 2); self.group.add(m);
    });
    // Carriles X sobre las patas.
    var railLen = g.rail_x[1] - g.rail_x[0] + f.post_mm;
    g.rails_y.forEach(function (y) {
      var m = boxMesh(railLen, f.rail_w_mm, f.rail_h_mm, steel);
      m.position.set((g.rail_x[0] + g.rail_x[1]) / 2, y, postTop + f.rail_h_mm / 2); self.group.add(m);
    });
    // Puente (se mueve en X), carro (X, Y), columna Z, cabezal C.
    var span = g.rails_y[1] - g.rails_y[0] + f.rail_w_mm;
    this.bridge = boxMesh(f.bridge_w_mm, span, f.bridge_h_mm, steel);
    this.group.add(this.bridge);
    this.carriage = boxMesh(f.carriage_mm, f.carriage_mm, f.bridge_h_mm + 200, dark);
    this.group.add(this.carriage);
    this.ram = boxMesh(f.ram_mm, f.ram_mm, g.ram_length_mm, yellow);
    this.ram.userData.baseColor = yellow.color.clone();
    this.group.add(this.ram);
    var headMat = std(0x2e2e2e, 0.4);
    this.head = new T.Mesh(new T.CylinderGeometry(f.head_d_mm / 2, f.head_d_mm / 2, f.head_h_mm, 28), headMat);
    this.head.rotation.x = Math.PI / 2; this.head.castShadow = true;
    this.head.userData.baseColor = headMat.color.clone();
    this.headGroup = new T.Group(); this.headGroup.add(this.head); this.group.add(this.headGroup);
    this.gripper = root.Sim3DRobot.buildGripper(robotData.gripper);
    this.gripper.matrixAutoUpdate = false;
    this.gripper.traverse(function (o) {  // materiales propios para poder pintarla de rojo
      if (o.isMesh) { o.material = o.material.clone(); o.userData.baseColor = o.material.color.clone(); }
    });
    this.group.add(this.gripper);
    this.underZ = g.bridge_underside_z;
    // Cinta y volteador.
    this.conveyor = robotData.conveyor ? root.Sim3DRobot.buildConveyor(scene, robotData.conveyor) : null;
    this.tipper = robotData.conveyor && robotData.conveyor.tipper ? buildTipper(scene, robotData.conveyor) : null;
    var cv = robotData.conveyor;
    this.home = cv ? [cv.point[0], cv.point[1], cv.z_conveyor + 1200, 0] : [this.cell.base_x, this.cell.base_y, g.z_range[1], 0];
    this.setQ(this.home);
  }

  Gantry.prototype.setQ = function (q, badLink) {
    this.q = q.slice();
    var f = this.g.frame, x = q[0], y = q[1], z = q[2];
    this.bridge.position.set(x, (this.g.rails_y[0] + this.g.rails_y[1]) / 2, this.underZ + f.bridge_h_mm / 2);
    this.carriage.position.set(x, y, this.underZ + f.bridge_h_mm / 2);
    var ramBottom = z + this.tcp + f.head_h_mm;
    this.ram.position.set(x, y, ramBottom + this.g.ram_length_mm / 2);
    this.headGroup.position.set(x, y, z + this.tcp + f.head_h_mm / 2);
    this.headGroup.rotation.z = q[3] * D2R;
    this.gripper.matrix.copy(this.toolWorld(q));
    var red = new T.Color(0xff2a2a);
    this.ram.material.color.copy(badLink === 'columna_z' ? red : this.ram.userData.baseColor);
    this.head.material.color.copy(badLink === 'cabezal_c' ? red : this.head.userData.baseColor);
    var gr = badLink === 'garra';
    this.gripper.traverse(function (o) { if (o.isMesh) o.material.color.copy(gr ? red : o.userData.baseColor); });
  };
  Gantry.prototype.toolWorld = function (q) {
    return new T.Matrix4().makeTranslation(q[0], q[1], q[2]).multiply(new T.Matrix4().makeRotationZ(q[3] * D2R));
  };
  /* Marco local de la cuna (d, w, z) girado frac·90° sobre w: la caja tumbada pasa a estar de pie. */
  Gantry.prototype.tipperMatrix = function (frac) {
    return this.tipper ? this.tipper.base.clone().multiply(new T.Matrix4().makeRotationY(-Math.PI / 2 * frac)) : new T.Matrix4();
  };
  Gantry.prototype.setTip = function (frac) {
    if (!this.tipper) return;
    this.tipper.rot.matrix.copy(this.tipperMatrix(frac));
  };
  Gantry.prototype.setVisible = function (v) {
    this.group.visible = v;
    if (this.conveyor) this.conveyor.group.visible = v;
    if (this.tipper) this.tipper.group.visible = v;
  };
  Gantry.prototype.dispose = function (scene) {
    scene.remove(this.group);
    if (this.conveyor) scene.remove(this.conveyor.group);
    if (this.tipper) scene.remove(this.tipper.group);
  };

  function buildTipper(scene, cv) {
    var tp = cv.tipper, grp = new T.Group();
    var d = new T.Vector3().fromArray(tp.d), w = new T.Vector3().fromArray(tp.w), up = new T.Vector3(0, 0, 1);
    var base = new T.Matrix4().makeBasis(d, w, up).setPosition(new T.Vector3().fromArray(tp.pivot));
    var rot = new T.Group(); rot.matrixAutoUpdate = false; rot.matrix.copy(base); grp.add(rot);
    var mat = std(0x1f8a70, 0.35), th = tp.thickness_mm;
    var plate = boxMesh(tp.plate_len_mm, tp.plate_w_mm, th, mat);
    plate.position.set(tp.plate_len_mm / 2, 0, -th / 2); rot.add(plate);
    var stop = boxMesh(th, tp.plate_w_mm, tp.backstop_mm + th, mat);
    stop.position.set(-th / 2, 0, (tp.backstop_mm - th) / 2); rot.add(stop);
    // Soporte fijo bajo el pivote.
    var hz = cv.z_conveyor + 144 - th;
    var foot = boxMesh(160, tp.plate_w_mm, hz, std(0x3b4148, 0.3));
    foot.position.set(tp.pivot[0], tp.pivot[1], tp.pivot[2] - th - hz / 2); grp.add(foot);
    scene.add(grp);
    return { group: grp, rot: rot, base: base };
  }

  root.Sim3DGantry = { Gantry: Gantry };
})(typeof window !== 'undefined' ? window : globalThis);
