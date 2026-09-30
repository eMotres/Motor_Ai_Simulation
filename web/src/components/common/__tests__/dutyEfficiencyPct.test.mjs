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
 * The function is re-implemented verbatim below rather than imported: the
 * module is TypeScript and lives beside components that pull in
 * `import.meta.env`, which `node --test` cannot load (same reason as
 * common/__tests__/progressStrip and controller/__tests__/compareRows).
 * Changing `dutyEfficiencyPct` in `ActiveFamilyStrip.tsx` therefore has to
 * change this file too — and that is the moment someone has to justify the
 * new behaviour.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

/* ── verbatim copy of the shipped helper ─────────────────────────────────── */

function dutyEfficiencyPct(s, pMechW) {
  const pm = Math.abs(Number(pMechW) || 0);
  const loss = Number(s?.P_loss_total_W) || 0;
  const brg = Number(s?.P_bearings_W);
  const wind = Number(s?.P_windage_W);
  const known = Number.isFinite(brg) || Number.isFinite(wind);
  const extra = (Number.isFinite(brg) ? brg : 0) + (Number.isFinite(wind) ? wind : 0);
  const gen = s?.op_mode === 'generator';
  if (pm <= 0) return (Number(s?.efficiency) || 0) * 100;
  const pElec = gen ? Math.max(0, pm - loss) : pm + loss;
  if (!known) return (gen ? pElec / pm : pm / pElec) * 100;
  const pShaft = gen ? pm + extra : Math.max(0, pm - extra);
  return (gen ? (pShaft > 0 ? pElec / pShaft : 0)
              : (pElec > 0 ? pShaft / pElec : 0)) * 100;
}

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
  const s = { efficiency: 0.9846 };
  assert.ok(Math.abs(dutyEfficiencyPct(s, 0) - 98.46) < 1e-9);
  assert.ok(Math.abs(dutyEfficiencyPct(s, NaN) - 98.46) < 1e-9);
});
