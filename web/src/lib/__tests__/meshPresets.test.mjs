// node --test — the pure rule of lib/meshPresets.ts (matchPreset), restated verbatim (repo convention: `node --test`
// cannot load the TS modules).  Owner 2026-11-17: choosing a preset fills the fields; a manual change reads "custom";
// the Simulation preset is "96 or more", the Optimization preset is exactly its step count.
import test from 'node:test';
import assert from 'node:assert/strict';

/* ── copied verbatim from lib/meshPresets.ts ─────────────────────────────── */
const REL = 1e-3;
const close = (a, b) => a !== undefined && Number.isFinite(a) && Math.abs(a - b) <= REL * Math.max(1, Math.abs(b));
const wireCell = (m) => (typeof m.coil_rel === 'number' && m.coil_rel > 0 ? m.coil_rel : 1);
function matchPreset(cur, set) {
  if (!set) return 'custom';
  for (const p of set.presets) {
    const v = p.values;
    if (!v) continue;
    const stepsOk = p.name === 'simulation'
      ? cur.steps !== undefined && cur.steps >= v.n_steps_per_period - 1e-9
      : close(cur.steps, v.n_steps_per_period);
    if (!stepsOk) continue;
    if (!close(cur.gap_layers, v.gap_layers) || !close(cur.mesh_size_mm, v.mesh_size_mm)
        || !close(cur.min_size_mm, v.min_size_mm) || !close(cur.outer_air_factor, v.outer_air_factor)
        || cur.n_sectors !== v.n_sectors) continue;
    if (cur.demag !== undefined && cur.demag !== v.demag) continue;
    const pm = v.component_mesh;
    const keys = ['stator', 'rotor', 'magnet', 'outer'];
    if (!keys.every(k => close(cur.component_mesh[k], pm[k]))) continue;
    if (!close(wireCell(cur.component_mesh), wireCell(pm))) continue;
    return p.name;
  }
  return 'custom';
}
/* ───────────────────────────────────────────────────────────────────────── */

const comp = { stator: 1.2, rotor: 1.2, magnet: 1.2, outer: 2.4 };
const set = { version: 1, presets: [
  { name: 'optimization', values: { n_steps_per_period: 48, gap_layers: 1, mesh_size_mm: 1.2, min_size_mm: 0.12,
    outer_air_factor: 1.2, n_sectors: 2, demag: true, component_mesh: { ...comp, coil_rel: 2 } } },
  { name: 'simulation', values: { n_steps_per_period: 96, gap_layers: 2, mesh_size_mm: 1.0, min_size_mm: 0.1,
    outer_air_factor: 1.2, n_sectors: 2, demag: true, component_mesh: { stator: 1, rotor: 1, magnet: 1, outer: 2, coil_rel: 1 } } },
] };
const optCur = () => ({ steps: 48, gap_layers: 1, mesh_size_mm: 1.2, min_size_mm: 0.12, outer_air_factor: 1.2,
  n_sectors: 2, demag: true, component_mesh: { ...comp, coil_rel: 2 } });
const simCur = () => ({ steps: 96, gap_layers: 2, mesh_size_mm: 1.0, min_size_mm: 0.1, outer_air_factor: 1.2,
  n_sectors: 2, demag: true, component_mesh: { stator: 1, rotor: 1, magnet: 1, outer: 2 } });   // wire cell 1 = no key

test('the stored values of each preset read as that preset', () => {
  assert.equal(matchPreset(optCur(), set), 'optimization');
  assert.equal(matchPreset(simCur(), set), 'simulation');
});
test('a manual change reads custom', () => {
  assert.equal(matchPreset({ ...optCur(), gap_layers: 2 }, set), 'custom');
  assert.equal(matchPreset({ ...simCur(), mesh_size_mm: 0.8 }, set), 'custom');
  assert.equal(matchPreset({ ...optCur(), n_sectors: 1 }, set), 'custom');
  assert.equal(matchPreset({ ...simCur(), component_mesh: { ...simCur().component_mesh, coil_rel: 2 } }, set), 'custom');
});
test('Optimization is exactly 48 steps; Simulation is 96 or more', () => {
  assert.equal(matchPreset({ ...optCur(), steps: 72 }, set), 'custom');
  assert.equal(matchPreset({ ...simCur(), steps: 120 }, set), 'simulation');
  assert.equal(matchPreset({ ...simCur(), steps: 72 }, set), 'custom');
});
test('no presets or an unresolvable preset never match', () => {
  assert.equal(matchPreset(optCur(), null), 'custom');
  assert.equal(matchPreset(optCur(), { version: 1, presets: [{ name: 'optimization', error: 'x' }] }), 'custom');
});
