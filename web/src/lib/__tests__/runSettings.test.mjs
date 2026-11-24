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
