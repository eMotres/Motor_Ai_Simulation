/**
 * node --test — how the Thermal tab NAMES the Electromagnetic run it uses
 * (2026-09-30).
 *
 * Owner rule: the Thermal tab always takes the LATEST Electromagnetic run of
 * the loaded machine and adopts that run's point and settings; it keeps no
 * copy of its own.  The panel says which run in ONE line, with every setting
 * in the tooltip.  This imports the shipped module (dependency-free, so node's
 * type stripping loads it).
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  emRunClock, emRunLine, emRunPoint, emRunTooltip,
} from '../emRun.ts';

/* The owner's case, as `latest_em_run(...)["summary"]` sends it. */
const RUN = {
  run_id: '2026-09-30T09:50:45',
  computed_at: '2026-09-30T09:50:45+00:00',
  I_phase_rms: 80.61, gamma_deg: 10, rpm: 25000, coil_temp_c: 200,
  magnet_temp_c: 45.2, n_steps_per_period: 36, n_periods: 1,
  mesh_size_mm: 1, min_size_mm: 0.3, n_sectors: 2, op_mode: 'motor',
  demag: true, drive: 'current', inputs_recorded: true,
};

const localClock = (iso) => {
  const d = new Date(Date.parse(iso));
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
};

test('one line: current, angle, speed, steps and the clock of the run', () => {
  assert.equal(emRunLine(RUN),
    `EM: 81 A · γ 10° · 25 000 rpm · 36 steps · ${localClock(RUN.computed_at)}`);
});

test('the angle keeps one decimal only when it has one', () => {
  assert.match(emRunLine({ ...RUN, gamma_deg: 12.5 }), /γ 12\.5°/);
  assert.match(emRunLine({ ...RUN, gamma_deg: -15 }), /γ -15°/);
});

test('a stamp that does not parse drops the clock, never prints garbage', () => {
  assert.equal(emRunClock('not a date'), null);
  assert.equal(emRunLine({ ...RUN, computed_at: null }),
    'EM: 81 A · γ 10° · 25 000 rpm · 36 steps');
});

test('no run, no line', () => {
  assert.equal(emRunLine(null), null);
  assert.equal(emRunLine(undefined), null);
  assert.equal(emRunTooltip(null), '');
  assert.equal(emRunPoint(null), null);
});

test('the tooltip names every setting the map was solved with', () => {
  const t = emRunTooltip(RUN);
  for (const s of ['80.61 A', 'γ 10°', '25 000 rpm', 'Coil 200 °C',
                   'magnets 45.2 °C', '36 steps/period', 'mesh 1 mm',
                   'min 0.3 mm', '1/2 sector', 'demag on']) {
    assert.ok(t.includes(s), `tooltip lacks "${s}": ${t}`);
  }
  assert.match(emRunTooltip({ ...RUN, magnet_temp_c: null }),
    /magnets at the card temperature/);
  assert.match(emRunTooltip({ ...RUN, n_sectors: 1 }), /full ring/);
});

test('the comparison row reads the RUN\'s point, not a panel copy', () => {
  assert.deepEqual(emRunPoint(RUN), {
    rpm: 25000, gamma_deg: 10, I_phase_rms: 80.61, coil_temp_c: 200,
    n_steps_per_period: 36,
  });
});
