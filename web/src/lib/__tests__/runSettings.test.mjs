// node --test - the run-settings record of the Sweep / Optimization / Descent panels (owner 2026-11-24)
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { DEFAULT_RECORD, validRecord, choosePreset, withOverride, withoutOverride, answerKey, runSettingsBody } from '../runSettings.ts';

test('the default record is the Optimization preset with no overrides', () => {
  assert.deepEqual(DEFAULT_RECORD, { preset: 'optimization', overrides: {} });
});

test('a preset button sets the preset and clears every override; an edit adds one', () => {
  const edited = withOverride(withOverride(DEFAULT_RECORD, 'steps_per_period', 108), 'gap_layers', 2);
  assert.deepEqual(edited.overrides, { steps_per_period: 108, gap_layers: 2 });
  assert.deepEqual(choosePreset('simulation'), { preset: 'simulation', overrides: {} });
  assert.deepEqual(withoutOverride(edited, 'gap_layers').overrides, { steps_per_period: 108 });
});

test('a stored record is validated: unknown presets and non-finite overrides are dropped', () => {
  assert.equal(validRecord({ preset: 'turbo', overrides: {} }), null);
  assert.equal(validRecord('x'), null);
  assert.deepEqual(validRecord({ preset: 'simulation', overrides: { steps_per_period: 96, bad: 'x', n: NaN } }),
    { preset: 'simulation', overrides: { steps_per_period: 96 } });
});

test('an answer is current only for the same machine, duty, mode and record', () => {
  const k = answerKey('d/c/S1', 'sweep', DEFAULT_RECORD);
  assert.notEqual(k, answerKey('d/c/S2', 'sweep', DEFAULT_RECORD));
  assert.notEqual(k, answerKey('d/c/S1', 'descent', DEFAULT_RECORD));
  assert.notEqual(k, answerKey('d/c/S1', 'sweep', choosePreset('simulation')));
});

test('the request carries the record and the digest that was shown', () => {
  assert.deepEqual(runSettingsBody(choosePreset('simulation'), { sig: 7 }), { preset: 'simulation', overrides: {}, expect_sig: 7 });
});

test('opening a panel never writes the ordinary-run settings: no preset apply, no config PATCH, no localStorage write', () => {
  const root = new URL('../../', import.meta.url);
  for (const f of ['lib/useRunSettings.ts', 'lib/runSettings.ts', 'components/sweep/RunSettingsBar.tsx']) {
    const src = readFileSync(new URL(f, root), 'utf8');
    assert.ok(!/applyPreset|localStorage\.setItem|method:\s*'PATCH'|api\/mesh\/config/.test(src), f);
  }
  for (const f of ['components/sweep/SweepStudyPanel.tsx', 'components/sweep/DescentPanel.tsx', 'components/sweep/AutoOptimizePanel.tsx']) {
    const src = readFileSync(new URL(f, root), 'utf8');
    assert.ok(!/applyPreset|useRunPreset|MeshPresetSelect/.test(src), f);
  }
});

test('the run request of each panel carries run_settings and is gated on a held answer', () => {
  const root = new URL('../../', import.meta.url);
  const sweep = readFileSync(new URL('components/sweep/SweepStudyPanel.tsx', root), 'utf8');
  assert.ok(sweep.includes('run_settings: runSettingsBody(') && sweep.includes('!rs.ready'));
  const desc = readFileSync(new URL('components/sweep/DescentPanel.tsx', root), 'utf8');
  assert.ok(desc.includes('runSettings: runSettingsBody(') && desc.includes('!rs.ready'));
  const auto = readFileSync(new URL('components/sweep/AutoOptimizePanel.tsx', root), 'utf8');
  assert.ok(auto.includes('run_settings:') && auto.includes('!rs.ready'));
});

test('applying a descent / auto result leaves the ordinary-run fidelity alone: no steps, gap rows, sectors or mesh sizes are restored', () => {
  const src = readFileSync(new URL('../../stores/motorStore.ts', import.meta.url), 'utf8');
  const a = src.indexOf('function restoreDescentEvalParams');
  const body = src.slice(a, src.indexOf('\n}\n', a));
  for (const key of ['sim.stepsPP', 'mesh.gapLayers', 'mesh.meshSize', 'mesh.minSize', 'mesh.nSectors']) {
    assert.ok(!body.includes(key), key);
  }
  assert.ok(body.includes("'descent-eval-params'") && body.includes('steps_per_period: _s'));   // the live event is stripped too
});

test('no UI string or comment claims a complete-settings identity or a stale-display guard for more than steps and gap rows', () => {
  const root = new URL('../../', import.meta.url);
  for (const f of ['locales/en/simulation.json', 'lib/sweepSettings.ts', 'lib/runSettings.ts', 'components/sweep/RunSettingsBar.tsx']) {
    assert.ok(!/full settings|complete settings|COMPLETE settings/i.test(readFileSync(new URL(f, root), 'utf8')), f);
  }
});

test('the Compare delta row and its column maxima follow the same sweep exclusion as the ranking', () => {
  const src = readFileSync(new URL('../../components/compare/ComparePanel.tsx', import.meta.url), 'utf8');
  const a = src.indexOf('const comparisonPercentValue');
  assert.ok(src.slice(a, a + 400).includes('selSweepMixed && isSweepRow'));
  assert.ok(/extentVals\(r, selSweepMixed \? selected\.filter\(s => !isSweepRow/.test(src));
});

test('the gap-rows field takes whole numbers only', () => {
  const src = readFileSync(new URL('../../components/sweep/RunSettingsBar.tsx', import.meta.url), 'utf8');
  assert.ok(src.includes('Number.isInteger'));
});
