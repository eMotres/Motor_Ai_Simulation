/**
 * `dutyEfficiencyPct`, tested without a browser.
 *
 * Bug found 2026-09-30: the ONE caller (the duty-save handler in
 * `ActiveFamilyStrip.tsx`) used to pass this function the SHAFT power
 * (rotor T·ω already adjusted for bearings + windage), while the function
 * itself applies bearings + windage to whatever it is given to arrive at the
 * shaft power.  Bearings + windage were therefore taken out twice, and the
 * saved `efficiency_pct` under-reported η by roughly loss_mech / P_elec (on
 * the live L155 duty, about 0.15 pp).  The contract is: `pMechW` is the RAW
 * ROTOR mechanical power (`SummaryTable`'s `pMechAbs`, i.e. `|P_mech_W|`,
 * k3d-scaled where that applies) — never an already shaft-corrected value.
 *
 * Owner rule (`one-efficiency-at-the-shaft`): there is ONE efficiency, at the
 * shaft, with bearings and windage included exactly once.
 *
 * The test imports the production helper so its assertions cannot drift from
 * the implementation used by the duty-save handler.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { dutyEfficiencyPct } from '../../../lib/dutyEfficiency.ts';

test('a duty without computed total loss has no efficiency instead of a zero-loss result', () => {
  assert.equal(dutyEfficiencyPct({ efficiency: 0.92 }, 1000), null);
  assert.equal(dutyEfficiencyPct({ P_loss_total_W: null }, 1000), null);
});

/* ── worked example: motoring, rotor 100 kW, EM loss 3 kW, mech loss 300 W ── */
/* pElec = pm + loss = 103000; pShaft = pm - extra = 99700
 * eta_shaft = pShaft / pElec = 99700 / 103000 = 96.7961165... % */

test('worked motoring example: P_elec, P_em_losses, P_mech -> eta_shaft', () => {
  const s = { P_loss_total_W: 3000, P_bearings_W: 200, P_windage_W: 100 };
  const rotorW = 100000; // raw rotor T.omega — the ONLY correct argument
  const eta = dutyEfficiencyPct(s, rotorW);
  assert.ok(Math.abs(eta - 96.7961165) < 1e-4,
    `expected ~96.7961 %, got ${eta}`);
});

test('feeding the ALREADY shaft-corrected power double-counts the mechanical '
   + 'loss and under-reports eta (the bug this test guards against)', () => {
  const s = { P_loss_total_W: 3000, P_bearings_W: 200, P_windage_W: 100 };
  const rotorW = 100000;
  const shaftW = rotorW - 300; // what the caller used to pass, by mistake
  const correct = dutyEfficiencyPct(s, rotorW);
  const buggy = dutyEfficiencyPct(s, shaftW);
  assert.ok(buggy < correct,
    'double-applying bearings + windage must read LOWER than the true shaft eta');
  assert.ok(Math.abs(correct - buggy) > 1e-3, 'the two must actually differ');
});

test('generator direction: losses come OFF the input, mech loss ADDS to the '
   + 'shaft power the prime mover must supply', () => {
  const s = { op_mode: 'generator', P_loss_total_W: 3000,
              P_bearings_W: 200, P_windage_W: 100 };
  const rotorW = 100000;
  // pElec = pm - loss = 97000; pShaft = pm + extra = 100300
  // eta = pElec / pShaft = 97000 / 100300
  const eta = dutyEfficiencyPct(s, rotorW);
  assert.ok(Math.abs(eta - (97000 / 100300) * 100) < 1e-6);
});

test('unknown mechanical loss (no bearings named) falls back to the '
   + 'electromagnetic efficiency, not a fabricated shaft number', () => {
  const s = { P_loss_total_W: 3000 }; // no P_bearings_W / P_windage_W at all
  const rotorW = 100000;
  const eta = dutyEfficiencyPct(s, rotorW);
  assert.ok(Math.abs(eta - (100000 / 103000) * 100) < 1e-6);
});

test('zero (or unusable) rotor power falls back to the stored efficiency field', () => {
  const s = { efficiency: 0.9846, P_loss_total_W: 10 };
  assert.ok(Math.abs(dutyEfficiencyPct(s, 0) - 98.46) < 1e-9);
  assert.ok(Math.abs(dutyEfficiencyPct(s, NaN) - 98.46) < 1e-9);
});

/* ── one efficiency per point: a MARKED point is saved with the page's own balance (lib/shaftBalance) ── */
import { shaftBalance } from '../../../lib/shaftBalance.ts';
import { dutyEfficiencyBasis, shaftExtraW } from '../../../lib/dutyEfficiency.ts';
const MARK = 'fe_on_shaft_k3d_v1';

/** what the Electromagnetic tab shows for this summary: shaftBalance with the run's own mechanical context */
const page = (s) => {
  const b = shaftBalance(s, shaftExtraW(s));
  return b?.eta == null ? null : b.eta * 100;
};

// every row of Astra's table (PR #916 review) + the 870 W motor and 850 W generator fixtures, formatted values included
const rows = [
  ['motor 870 W, X known', { op_mode: 'motor', P_mech_W: 870, P_loss_total_W: 100, efficiency: 0.8969, P_mech_extra_W: 20, P_bearings_W: 15, P_windage_W: 5 }],
  ['generator 850 W, X known', { op_mode: 'generator', P_mech_W: 850, P_loss_total_W: 80, efficiency: 0.9059, P_mech_extra_W: 20, P_bearings_W: 15, P_windage_W: 5 }],
  ['motor 870 W, X unknown', { op_mode: 'motor', P_mech_W: 870, P_loss_total_W: 100, efficiency: 0.8969 }],
  ['generator 850 W, X unknown', { op_mode: 'generator', P_mech_W: 850, P_loss_total_W: 80, efficiency: 0.9059 }],
  ['motor P=20.1 L=14.1 eta=.616, X unknown', { op_mode: 'motor', P_mech_W: 20.1, P_loss_total_W: 14.1, efficiency: 0.616 }],
  ['motor rounded P=1 L=.1 eta=.9455, X unknown', { op_mode: 'motor', P_mech_W: 1, P_loss_total_W: 0.1, efficiency: 0.9455 }],
  ['same rounded point, X=.1', { op_mode: 'motor', P_mech_W: 1, P_loss_total_W: 0.1, efficiency: 0.9455, P_mech_extra_W: 0.1 }],
  ['motor cut-in P=.4 L=.1 eta=0, X unknown', { op_mode: 'motor', P_mech_W: 0.4, P_loss_total_W: 0.1, efficiency: 0 }],
  ['motor cut-in, X=.1', { op_mode: 'motor', P_mech_W: 0.4, P_loss_total_W: 0.1, efficiency: 0, P_mech_extra_W: 0.1 }],
  ['generator P=.8 L=.1 eta=0, X unknown', { op_mode: 'generator', P_mech_W: 0.8, P_loss_total_W: 0.1, efficiency: 0 }],
  ['motor P=0, L=10, X=2', { op_mode: 'motor', P_mech_W: 0, P_loss_total_W: 10, efficiency: 0, P_mech_extra_W: 2 }],
  ['motor P=-5, L=10, X=2', { op_mode: 'motor', P_mech_W: -5, P_loss_total_W: 10, efficiency: 0, P_mech_extra_W: 2 }],
  ['motor 0<P<X', { op_mode: 'motor', P_mech_W: 1, P_loss_total_W: 1, efficiency: 0.5, P_mech_extra_W: 2 }],
  ['generator P=-5 L=1 X=2', { op_mode: 'generator', P_mech_W: -5, P_loss_total_W: 1, efficiency: 0, P_mech_extra_W: 2 }],
  ['null mechanical losses are unknown, not zero', { op_mode: 'motor', P_mech_W: 100, P_loss_total_W: 10, efficiency: 0.9, P_mech_extra_W: null, P_bearings_W: null, P_windage_W: null }],
  ['bearings fallback (no P_mech_extra_W)', { op_mode: 'motor', P_mech_W: 100, P_loss_total_W: 10, efficiency: 0.9, P_bearings_W: 3, P_windage_W: 1 }],
];

for (const [name, base] of rows) {
  test(`marked point, saved efficiency equals the page value: ${name}`, () => {
    const s = { shaft_convention: MARK, ...base };
    const saved = dutyEfficiencyPct(s, s.P_mech_W);
    const shown = page(s);
    if (shown == null) assert.equal(saved, null);
    else assert.equal(saved, shown);                              // the SAME helper: identical at full precision
    if (saved != null) assert.equal(saved.toFixed(2), shown.toFixed(2));          // and as formatted on the page
    assert.equal(dutyEfficiencyBasis(s) === null, shown == null);
  });
}

test('marked: the 870 W motor and the 850 W generator read 87.63 % and 88.51 % at the coupling', () => {
  const m = rows[0][1], g = rows[1][1];
  assert.equal(dutyEfficiencyPct({ shaft_convention: MARK, ...m }, 870).toFixed(2), '87.63');
  assert.equal(dutyEfficiencyPct({ shaft_convention: MARK, ...g }, 850).toFixed(2), '88.51');
  assert.equal(dutyEfficiencyBasis({ shaft_convention: MARK, ...m }), 'coupling');
  assert.equal(dutyEfficiencyBasis({ shaft_convention: MARK, ...rows[2][1] }), 'shaft_before_bearings');
  assert.equal(dutyEfficiencyBasis({ shaft_convention: MARK, ...rows[12][1] }), 'coupling');
});

test('null / non-finite mechanical context is unknown, never zero (Number(null) is not "known")', () => {
  assert.equal(shaftExtraW({ P_mech_extra_W: null, P_bearings_W: null, P_windage_W: null }), null);
  assert.equal(shaftExtraW({ P_mech_extra_W: Number.NaN }), null);
  assert.equal(shaftExtraW({ P_mech_extra_W: 5 }), 5);
  assert.equal(shaftExtraW({ P_bearings_W: 3 }), 3);
  assert.equal(shaftExtraW({ P_bearings_W: 3, P_windage_W: 1 }), 4);
});

test('golden: an unmarked summary (with or without an efficiency field) keeps the old formula bit for bit', () => {
  // values the pre-change function produced for these inputs
  assert.equal(dutyEfficiencyPct({ P_loss_total_W: 3000, P_bearings_W: 200, P_windage_W: 100 }, 100000), 99700 / 103000 * 100);
  assert.equal(dutyEfficiencyPct({ P_loss_total_W: 100, efficiency: 0.5 }, 900), 900 / 1000 * 100);
  assert.equal(dutyEfficiencyPct({ P_loss_total_W: 100, efficiency: 0.5, op_mode: 'generator' }, 900), 800 / 900 * 100);
  assert.equal(dutyEfficiencyPct({ P_loss_total_W: 100, efficiency: 0.5 }, 0), 50);
  assert.equal(dutyEfficiencyBasis({ P_loss_total_W: 1, efficiency: 0.9 }), null);
});
