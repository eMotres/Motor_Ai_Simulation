import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { atomicDutyRunBody, dutyDefinitionOnly } from '../../../lib/dutyRunSave.ts';

const run = { die: 'SYNTH', config: 'L40', duty: 'rated', drive: 'current',
  summary: { shaft_convention: 'fe_on_shaft_k3d_v1', P_mech_W: 870 },
  result: { efficiency_pct: 850 / 970 * 100 }, settings: { 'sim.drive': 'current' } };

test('a save without waveforms still carries summary and result together', () => {
  assert.deepEqual(atomicDutyRunBody(run), run);
  assert.deepEqual(atomicDutyRunBody(run, null), run);
});

test('waveforms ride the same request and only unused field arrays are dropped', () => {
  const transient = { time_s: [0, 1], summary: run.summary,
    frames: [1], field: [2], demag_field: [3], demag_coef_per_tri: [4] };
  assert.deepEqual(atomicDutyRunBody(run, transient), {
    ...run, payload: { time_s: [0, 1], summary: run.summary } });
  assert.deepEqual(transient.frames, [1]);
});

test('preliminary definition save cannot publish either half of the computed snapshot', () => {
  const definition = { name: 'rated', drive: 'current', materials: { magnet: 'synthetic' } };
  assert.deepEqual(dutyDefinitionOnly({ ...definition, summary: run.summary, mesh: run.settings }), definition);
});

test('the component routes matching runs through the atomic save, with no client marker witness', () => {
  const source = fs.readFileSync(new URL('../ActiveFamilyStrip.tsx', import.meta.url), 'utf8');
  assert.ok(source.includes('duty: dutyDefinitionOnly(dutyBody)'));
  assert.ok(source.includes('JSON.stringify(atomicDutyRunBody('));
  assert.ok(!source.includes('/api/family/duty_result'));
  assert.ok(!source.includes('shaft_conv: 1') && !source.includes('shaft_convention: s'));

});
