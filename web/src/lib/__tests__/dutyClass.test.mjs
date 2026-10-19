// node --test - SHORT-TIME OR CONTINUOUS (owner 2026-10-19: «помечай как кратковременный»).
// Imports lib/dutyClass.ts, lib/catalogRows.ts and lib/configuratorModes.ts
// (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';

let D = null, R = null, M = null;
try {
  D = await import('../dutyClass.ts');
  R = await import('../catalogRows.ts');
  M = await import('../configuratorModes.ts');
} catch { /* old Node */ }
const t = D && R && M ? test : test.skip;

const L35 = { duty_class: 'short_time',
              short_time: { time_to_limit_s: 370.7, part: 'winding', limit_c: 200,
                            words: 'short-time: 6 min 11 s from cold (winding 200 °C)' } };

t('the L35 rated duty is named short-time with its time, the server label first', () => {
  assert.equal(D.dutyLabel('rated', L35),
    'rated — short-time: 6 min 11 s from cold (winding 200 °C)');
  assert.equal(D.dutyLabel('rated', { ...L35, duty_label: 'from the server' }), 'from the server');
  assert.equal(D.dutyLabel('rated', { duty_class: 'continuous' }), 'rated');
  assert.equal(D.shortTimeChip(L35), 'short-time 6 min 11 s');
  assert.equal(D.shortTimeChip(null), null);
  assert.equal(D.fmtDuration(45.2), '45 s');
});

t('the save plan takes the short-time name and renames the source, else adds _continuous', () => {
  assert.deepEqual(D.continuousSavePlan('rated', L35), { name: 'rated', renameSourceTo: 'rated_short_time' });
  assert.deepEqual(D.continuousSavePlan('peak', null), { name: 'peak_continuous', renameSourceTo: null });
  assert.deepEqual(D.continuousSavePlan('rated', L35, 'S1 2600'), { name: 'S1 2600', renameSourceTo: null });
});

t('the catalog card says when its default mode is short-time', () => {
  const die = { name: 'CIANO28 150_35 new', configs: [{ name: 'L35', default_duty: 'rated', duties: [
    { name: 'rated', power_kw: 13, torque_nm: 31, rpm: 4000, result: { mass_kg: 3 },
      duty_class: 'short_time', short_time: { time_to_limit_s: 370.7 } }] }] };
  const [b] = R.configButtons(die);
  assert.equal(b.short_time, true);
  assert.equal(b.short_time_s, 370.7);
  die.configs[0].duties[0].duty_class = 'continuous';
  assert.equal(R.configButtons(die)[0].short_time, false);
});

t('a Configure mode that is short-time says so on its button', () => {
  const presets = [{ config: 'L35', die: 'X', knobs: {}, battery: null, pwm_variants: [], drive_variant: null,
    duty_points: [{ name: 'rated', current_A: 82.3, rpm: 4000, duty_class: 'short_time' },
                  { name: 'rated_cont', current_A: 24, rpm: 2643, duty_class: 'continuous' }] }];
  const labels = M.buildModePresets(presets).map((m) => m.label);
  assert.deepEqual(labels, ['L35 · rated (short-time)', 'L35 · rated_cont']);
});
