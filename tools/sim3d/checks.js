/* Comprobaciones de viabilidad del visor sim3d. Funciones puras, sin Three.js.
 * Se calculan solo a partir de la geometría exportada (mm, esquina mínima, z vertical).
 * Cada paso se evalúa contra las cajas anteriores del mismo palet físico, es decir,
 * con el estado que el robot encuentra al colocar esa caja.
 * Se usa en el navegador (window.Sim3DChecks) y en node (module.exports) para los tests.
 */
(function (root) {
  'use strict';

  var DEFAULTS = {
    tolMm: 1,              // tolerancia de solape / envolvente
    supportZTolMm: 1,      // contacto vertical para el apoyo (como validate.py)
    minSupportPct: 70,     // apoyo insuficiente por debajo de este %
    accessDeltaMm: 400,    // RobotAccessibilityControl, delta 400
    towerRatio: 3,         // altura exenta / lado menor de la base
    towerBraceGapMm: 50,   // una vecina a menos de esto arriostra la columna (holgura entre columnas)
    freeGapMm: 20,         // D-024: vecina a < 20 mm ocupa el lateral
    approachMm: 20         // separación lateral de la bajada con aproximación
  };

  var SEVERITY = {
    overlap: 'error', outside: 'error', floating: 'error', descent_blocked: 'error',
    support: 'warn', access: 'warn', tower: 'warn', zero_free_sides: 'warn'
  };

  var LABEL = {
    overlap: 'Solape', outside: 'Fuera de envolvente', floating: 'Flotando',
    descent_blocked: 'Bajada bloqueada', support: 'Apoyo insuficiente',
    access: 'Accesibilidad', tower: 'Torre', zero_free_sides: '0 lados libres'
  };

  function box(s) { return { x: s.x, y: s.y, z: s.z, dx: s.dx, dy: s.dy, dz: s.dz, step: s.step, box_id: s.box_id }; }
  function top(b) { return b.z + b.dz; }
  function ov(a0, a1, b0, b1) { return Math.min(a1, b1) - Math.max(a0, b0); }

  /* Unión exacta de rectángulos [x1,y1,x2,y2] (barrido en x), igual que validate._union_area. */
  function unionArea(rects) {
    var xs = [];
    rects.forEach(function (r) { xs.push(r[0], r[2]); });
    xs = Array.from(new Set(xs)).sort(function (a, b) { return a - b; });
    var area = 0;
    for (var i = 0; i + 1 < xs.length; i++) {
      var left = xs[i], right = xs[i + 1];
      var iv = rects.filter(function (r) { return r[0] < right && r[2] > left; })
        .map(function (r) { return [r[1], r[3]]; })
        .sort(function (a, b) { return a[0] - b[0] || a[1] - b[1]; });
      var covered = 0, end = -Infinity;
      iv.forEach(function (p) { covered += Math.max(0, p[1] - Math.max(p[0], end)); end = Math.max(end, p[1]); });
      area += (right - left) * covered;
    }
    return area;
  }

  function supporters(b, prev, zTol) {
    return prev.filter(function (o) {
      return o.z < b.z && Math.abs(top(o) - b.z) <= zTol &&
        ov(b.x, b.x + b.dx, o.x, o.x + o.dx) > 0 && ov(b.y, b.y + b.dy, o.y, o.y + o.dy) > 0;
    });
  }

  function supportFraction(b, prev, zTol) {
    if (Math.abs(b.z) <= 0.5) return 1;
    var rects = supporters(b, prev, zTol).map(function (o) {
      return [Math.max(b.x, o.x), Math.max(b.y, o.y), Math.min(b.x + b.dx, o.x + o.dx), Math.min(b.y + b.dy, o.y + o.dy)];
    });
    return unionArea(rects) / (b.dx * b.dy);
  }

  /* Holgura por lado (-x,+x,-y,+y) frente a vecinas con solape tangencial y vertical
   * positivo. Infinity = nada en ese lado (el borde del palet cuenta como libre). */
  function sideClearances(b, prev) {
    var c = [Infinity, Infinity, Infinity, Infinity];
    prev.forEach(function (p) {
      if (p.z >= top(b) || top(p) <= b.z) return;
      if (p.y < b.y + b.dy && p.y + p.dy > b.y) {
        if (p.x + p.dx <= b.x) c[0] = Math.min(c[0], b.x - (p.x + p.dx));
        if (p.x >= b.x + b.dx) c[1] = Math.min(c[1], p.x - (b.x + b.dx));
      }
      if (p.x < b.x + b.dx && p.x + p.dx > b.x) {
        if (p.y + p.dy <= b.y) c[2] = Math.min(c[2], b.y - (p.y + p.dy));
        if (p.y >= b.y + b.dy) c[3] = Math.min(c[3], p.y - (b.y + b.dy));
      }
    });
    return c;
  }

  /* Igual que palca.scoring.free_sides: hueco de exactamente gap es libre; contacto ocupa. */
  function freeSides(b, prev, gap) {
    return sideClearances(b, prev).filter(function (d) { return d >= gap && !(d === 0); }).length;
  }

  var DIRS = [[-1, 0], [1, 0], [0, -1], [0, 1]];
  var DIR_NAME = ['-X', '+X', '-Y', '+Y'];

  /* Lado de aproximación: el libre con más holgura; empate -> el más cercano al borde. */
  function approachSide(b, prev, env, gap) {
    var c = sideClearances(b, prev);
    var edge = [b.x - env.x0, env.x1 - (b.x + b.dx), b.y - env.y0, env.y1 - (b.y + b.dy)];
    var best = -1;
    for (var i = 0; i < 4; i++) {
      if (!(c[i] >= gap) || c[i] === 0) continue;
      if (best < 0 || c[i] > c[best] || (c[i] === c[best] && edge[i] < edge[best])) best = i;
    }
    if (best < 0) return null;
    return { side: best, name: DIR_NAME[best], dir: DIRS[best], clearance: c[best] };
  }

  /* Altura exenta de la columna de la caja: baja por sus apoyos hasta el suelo y mira
   * hasta qué altura la arriostran vecinas laterales (hueco < towerBraceGapMm). */
  function towerInfo(b, state, opts) {
    var zTol = opts.supportZTolMm, gap = opts.towerBraceGapMm;
    var column = [b], seen = new Set([b.step]), queue = [b];
    while (queue.length) {
      var cur = queue.pop();
      supporters(cur, state, zTol).forEach(function (s) {
        if (!seen.has(s.step)) { seen.add(s.step); column.push(s); queue.push(s); }
      });
    }
    var braced = 0;
    state.forEach(function (q) {
      if (seen.has(q.step)) return;
      column.forEach(function (c) {
        var vz = ov(c.z, top(c), q.z, top(q));
        if (vz <= 0) return;
        var tx = ov(c.x, c.x + c.dx, q.x, q.x + q.dx), ty = ov(c.y, c.y + c.dy, q.y, q.y + q.dy);
        var gx = Math.max(c.x, q.x) - Math.min(c.x + c.dx, q.x + q.dx);
        var gy = Math.max(c.y, q.y) - Math.min(c.y + c.dy, q.y + q.dy);
        var touching = (ty > 0 && gx >= 0 && gx < gap) || (tx > 0 && gy >= 0 && gy < gap);
        if (touching) braced = Math.max(braced, Math.min(top(c), top(q)));
      });
    });
    var free = top(b) - braced;
    var base = Infinity;
    column.forEach(function (c) { if (top(c) > braced) base = Math.min(base, c.dx, c.dy); });
    return { freeHeight: free, base: base, ratio: free > 0 && base < Infinity ? free / base : 0, braced: braced };
  }

  function computeChecks(steps, env, options) {
    var opts = Object.assign({}, DEFAULTS, options || {});
    var tol = opts.tolMm, out = [], perStep = {};
    var byPallet = {};
    steps.forEach(function (s) { (byPallet[s.pallet] = byPallet[s.pallet] || []).push(s); });
    Object.keys(byPallet).forEach(function (pid) {
      var prev = [];
      byPallet[pid].forEach(function (s) {
        var b = box(s), found = [];
        function add(code, msg, other) {
          found.push({ step: s.step, pallet: s.pallet, box_id: s.box_id, code: code, severity: SEVERITY[code],
                       label: LABEL[code], msg: msg, other: other == null ? null : other });
        }
        prev.forEach(function (o) {
          var ox = ov(b.x, b.x + b.dx, o.x, o.x + o.dx), oy = ov(b.y, b.y + b.dy, o.y, o.y + o.dy);
          var oz = ov(b.z, top(b), o.z, top(o));
          if (ox > tol && oy > tol && oz > tol)
            add('overlap', 'Solapa ' + [ox, oy, oz].map(Math.round).join('×') + ' mm con el paso ' + o.step, o.step);
          else if (ox > tol && oy > tol && o.z >= top(b) - tol)
            add('descent_blocked', 'Queda debajo de la caja del paso ' + o.step + ': no se puede bajar en vertical', o.step);
        });
        if (b.x < env.x0 - tol || b.y < env.y0 - tol || b.z < env.z0 - tol ||
            b.x + b.dx > env.x1 + tol || b.y + b.dy > env.y1 + tol || top(b) > env.z1 + tol)
          add('outside', 'x[' + b.x + ',' + (b.x + b.dx) + '] y[' + b.y + ',' + (b.y + b.dy) + '] z[' + b.z + ',' + top(b) +
              '] fuera de x[' + env.x0 + ',' + env.x1 + '] y[' + env.y0 + ',' + env.y1 + '] z[' + env.z0 + ',' + env.z1 + ']');
        var sup = supportFraction(b, prev, opts.supportZTolMm);
        var floating = b.z > tol && sup <= 0;
        if (floating) add('floating', 'Ninguna cara superior a z=' + b.z + ' mm debajo');
        else if (100 * sup + 1e-9 < opts.minSupportPct)
          add('support', 'Apoyo ' + (100 * sup).toFixed(1) + ' % < ' + opts.minSupportPct + ' %');
        if (opts.accessDeltaMm > 0) {
          var localZ = 0;
          prev.forEach(function (p) {
            if (ov(b.x, b.x + b.dx, p.x, p.x + p.dx) > 0 && ov(b.y, b.y + b.dy, p.y, p.y + p.dy) > 0) localZ = Math.max(localZ, top(p));
          });
          prev.forEach(function (p) {
            var gx = Math.max(p.x, b.x) - Math.min(p.x + p.dx, b.x + b.dx);
            var gy = Math.max(p.y, b.y) - Math.min(p.y + p.dy, b.y + b.dy);
            if (gx <= 0 && gy <= 0 && top(p) > localZ + opts.accessDeltaMm)
              add('access', 'Vecina del paso ' + p.step + ' llega a ' + top(p) + ' mm > ' + localZ + ' + ' + opts.accessDeltaMm, p.step);
          });
        }
        if (freeSides(b, prev, opts.freeGapMm) === 0)
          add('zero_free_sides', 'Hueco cerrado por los 4 lados: bajada 100 % cenital');
        var t = floating ? { ratio: 0 } : towerInfo(b, prev.concat([b]), opts);
        if (opts.towerRatio > 0 && t.ratio > opts.towerRatio)
          add('tower', 'Pila exenta de ' + Math.round(t.freeHeight) + ' mm sobre base de ' + Math.round(t.base) +
              ' mm (relación ' + t.ratio.toFixed(1) + ' > ' + opts.towerRatio + ')');
        perStep[s.step] = found;
        out.push.apply(out, found);
        prev.push(b);
      });
    });
    out.sort(function (a, b) { return a.step - b.step; });
    return { list: out, perStep: perStep, options: opts };
  }

  var api = { DEFAULTS: DEFAULTS, SEVERITY: SEVERITY, LABEL: LABEL, computeChecks: computeChecks,
              supportFraction: supportFraction, freeSides: freeSides, sideClearances: sideClearances,
              approachSide: approachSide, towerInfo: towerInfo, unionArea: unionArea, box: box };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.Sim3DChecks = api;
})(typeof window !== 'undefined' ? window : globalThis);
