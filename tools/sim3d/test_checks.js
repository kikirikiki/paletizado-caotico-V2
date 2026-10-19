#!/usr/bin/env node
/* Valida checks.js con el caso sintético del exportador: cada defecto a propósito
 * debe aparecer exactamente en su paso y las cajas sanas no deben dar avisos.
 * Uso: node tools/sim3d/test_checks.js out/sim3d/synthetic.json
 */
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const C = require(path.join(__dirname, 'checks.js'));

const file = process.argv[2];
if (!file) { console.error('uso: node test_checks.js <trace.json>'); process.exit(2); }
const trace = JSON.parse(fs.readFileSync(file, 'utf8'));
const run = trace.runs.find(r => r.config === 'synthetic');
assert(run, 'el JSON no contiene la run sintética (exporta con --synthetic)');

const res = C.computeChecks(run.steps, run.envelope, {});
const codes = step => (res.perStep[step] || []).map(w => w.code).sort();
const expected = {
  1: [], 2: [], 3: [], 4: ['overlap'], 5: ['floating'], 6: ['outside'], 7: [],
  8: ['access'], 9: ['tower'], 10: ['support'], 11: [], 12: ['zero_free_sides'],
};
for (const [step, want] of Object.entries(expected)) {
  assert.deepStrictEqual(codes(Number(step)), want.sort(), `paso ${step}: ${JSON.stringify(res.perStep[step])}`);
}
assert.strictEqual(res.perStep[4][0].other, 3, 'el solape debe señalar la caja del paso 3');

// Mismas métricas que el exportador (free_sides de palca y apoyo de validate.py).
const prev = [];
for (const s of run.steps) {
  const b = C.box(s);
  assert.strictEqual(C.freeSides(b, prev, 20), s.free_sides, `free_sides paso ${s.step}`);
  assert(Math.abs(100 * C.supportFraction(b, prev, 1) - s.support_pct) < 1e-6, `apoyo paso ${s.step}`);
  prev.push(b);
}

// Umbral configurable: con 40 % el apoyo del 50 % deja de avisar.
assert.deepStrictEqual(C.computeChecks(run.steps, run.envelope, { minSupportPct: 40 }).perStep[10], []);
// Bajada bajo una caja ya colocada.
const blocked = C.computeChecks([
  { step: 1, pallet: 1, box_id: 1, x: 0, y: 0, z: 300, dx: 400, dy: 400, dz: 200 },
  { step: 2, pallet: 1, box_id: 2, x: 0, y: 0, z: 0, dx: 400, dy: 400, dz: 300 },
], run.envelope, { towerRatio: 0 });
assert(blocked.perStep[2].some(w => w.code === 'descent_blocked'));
// Lado de aproximación: la caja 2 (contra la 1 por -X) debe entrar por +X o ±Y, nunca por -X.
const side = C.approachSide(C.box(run.steps[1]), [C.box(run.steps[0])], run.envelope, 20);
assert(side && side.name !== '-X', JSON.stringify(side));

console.log(`checks.js OK: ${res.list.length} avisos en el caso sintético`);
