// node --test - what an applied summary on the Electromagnetic card says about the waveforms under it.
// Owner 2026-11-11: after switching motors in one session the charts read "⚠ stale (computed before the Sweep design was
// applied)" until a reload - every catalog load was taken for a Sweep design (lib/appliedSummary).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

let A = null;
try { A = await import('../appliedSummary.ts'); } catch { /* old Node */ }
const t = A ? test : test.skip;

t('the source of an applied summary; an older sender that says nothing is a Sweep design', () => {
  assert.equal(A.appliedSourceOf('stored-run'), 'stored-run');
  assert.equal(A.appliedSourceOf('stored-summary'), 'stored-summary');
  assert.equal(A.appliedSourceOf(undefined), 'sweep');
  assert.equal(A.appliedSourceOf('anything'), 'sweep');
});

t('the bug: a catalog point\'s stored run (summary + its own waveforms) flags nothing', () => {
  assert.equal(A.appliedChartsNote(true, 'stored-run'), null);
  assert.equal(A.appliedChartsNote(false, 'sweep'), null);
});

t('a Sweep design and a summary without stored waveforms still flag the charts', () => {
  assert.match(A.appliedChartsNote(true, 'sweep'), /Sweep design/);
  assert.match(A.appliedChartsNote(true, 'stored-summary'), /no stored waveforms/);
});

t('the senders name their source, and the dashboard drops an applied summary when the point changes', () => {
  const runs = readFileSync(new URL('../dutyRuns.ts', import.meta.url), 'utf8');
  assert.match(runs, /source: run\.payload \? 'stored-run' : 'stored-summary'/);
  const apply = readFileSync(new URL('../dutyLocalApply.ts', import.meta.url), 'utf8');
  assert.equal((apply.match(/source: 'stored-summary'/g) || []).length, 2);
  const dash = readFileSync(new URL('../../components/simulation/PhysicsDashboard.tsx', import.meta.url), 'utf8');
  assert.match(dash, /watchLoadedPoint\(/);
  assert.match(dash, /appliedNote=\{appliedChartsNote\(/);
  assert.doesNotMatch(dash, /appliedFromSweep=/);
});
