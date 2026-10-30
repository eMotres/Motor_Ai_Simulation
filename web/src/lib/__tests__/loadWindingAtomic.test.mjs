import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// A machine load applies geometry AND winding as ONE change (owner 2026-10-30:
// CILN28 3-phase 4S -> Erium six-phase 4S-2P flashed "winding connection '4S'
// is not valid for 48 slots - set to 8S").  No notice is ever shown for it, and
// the panel never changes a connection on its own.
let outcome = null;
try { outcome = await import('../geometryApplyOutcome.ts'); } catch { /* old Node */ }
const t = outcome ? test : test.skip;

const src = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
const dutyApply = src('../dutyApply.ts');
const panel = src('../../components/simulation/SimulationPanel.tsx');
const store = src('../../stores/motorStore.ts');
const local = src('../dutyLocalApply.ts');

test('the owner load sends the winding WITH the geometry, not as a later PATCH', () => {
  assert.match(dutyApply, /updateGeometryViaApi\(p\.geometry,\s*\{\s*winding:/);
  assert.doesNotMatch(dutyApply, /\/api\/winding\/config/);
  // the six-phase split and the phase count travel in the same body
  assert.match(dutyApply, /phases:\s*six \? 6 : 3/);
  assert.match(dutyApply, /set1_paths/);
  assert.match(dutyApply, /set_neutrals/);
});

test('the store writes a load to /api/geometry/load, an ordinary edit to /api/geometry', () => {
  assert.match(store, /\/api\/geometry\$\{opts\?\.winding \? '\/load' : ''\}/);
  assert.match(store, /\{ geometry: params, winding: opts\.winding \}/);
});

test('no auto-correction notice is shown or carried for a load', () => {
  for (const [name, text] of [['dutyApply', dutyApply], ['SimulationPanel', panel],
                              ['dutyLocalApply', local]]) {
    assert.doesNotMatch(text, /winding-note/, name);
    assert.doesNotMatch(text, /winding_note/, name);
    assert.doesNotMatch(text, /connection_note/, name);
  }
  assert.doesNotMatch(panel, /setWindNote|windNote/);
});

test('the panel never switches the connection on its own (no all-series self-heal)', () => {
  // the only PATCH sources are the user\'s own button / field edits
  assert.doesNotMatch(panel, /applyWinding\(\{ connection: windConns\[0\]\.label \}\)/);
  assert.match(panel, /connection_error/);
});

t('a winding refusal stops the load with the server\'s own one-line reason', () => {
  const reason = "connection '10P' (1 series x 10 parallel) is not possible on 48 slots: Valid: 8S, 4S-2P";
  const stop = outcome.loadStopReason('CIANO56 400_80 vadim 5', 'L100',
    { ok: false, refused: [{ field: 'winding', reason }] });
  assert.equal(stop, `winding of CIANO56 400_80 vadim 5 / L100 was refused - ${reason}`);
});

t('a geometry refusal keeps its geometry wording, and a written load stops nothing', () => {
  assert.match(outcome.loadStopReason('d', 'c', { ok: false, refused: [{ field: 'tooth_width', reason: 'locked' }] }),
    /geometry of d \/ c was refused - tooth_width: locked/);
  assert.equal(outcome.loadStopReason('d', 'c', { ok: true, refused: [] }), null);
});
