// node --test — Configure's physical slider ranges (owner 2026-10-05).
// Imports lib/configuratorLimits.ts itself (no runtime imports); needs a Node
// that strips TypeScript types (>= 22.18 / 23.6 / 24), else the suite is skipped.
//
// What it pins:
//   * stack length: max = the hand-set value, else today's rule ("default");
//   * wire: min 0.2 mm, step 0.1, max = thickest that fits N rows;
//   * turns: max = the rows that fit the chosen wire — live, and NO slider
//     combination can overflow the slot (same inequality as the solver's
//     geometry_constraints._wire_height_max, pinned in tests/test_configure_limits.py);
//   * current: the inverter ceiling when a controller is set, else the passport
//     rule ("no controller"); speed: the envelope at the pack maximum, else Kv x V x m;
//   * an admin override can only NARROW; the connection warning.
import test from 'node:test';
import assert from 'node:assert/strict';

let L = null;
try { L = await import('../configuratorLimits.ts'); } catch { /* old Node */ }
const t = L ? test : test.skip;

const P = { L0_mm: 12, N0: 7, wireH0_mm: 0.6, I0_A: 40, rpm0: 13000 };
const FIT = { slotHeight_mm: 6.0, insulation_mm: 0.06, wireSpacingY_mm: 0.1 };
const NO_SPEED = { rpm: null, basis: 'no_battery' };
const inputs = (o = {}) => ({ p: P, fit: FIT, N: 7, wireH_mm: 0.6, lMaxMm: null,
  iMaxA: null, speed: NO_SPEED, ...o });

t('stack length: hand-set max wins, else the default rule is labelled default', () => {
  const d = L.physicalRanges(inputs());
  assert.equal(d.L_mm.basis, 'default');
  assert.equal(d.L_mm.max, 36);                    // 3.0 x 12 (today's rule)
  assert.equal(d.L_mm.min, 3.6);
  const h = L.physicalRanges(inputs({ lMaxMm: 30 }));
  assert.equal(h.L_mm.basis, 'hand');
  assert.equal(h.L_mm.max, 30);
  assert.equal(h.L_mm.min, d.L_mm.min);            // the min rule is unchanged
});

t('wire: min 0.2 mm, step 0.1 mm, max = thickest wire that fits N rows', () => {
  assert.equal(L.WIRE_MIN_MM, 0.2);
  assert.equal(L.WIRE_STEP_MM, 0.1);
  const r = L.physicalRanges(inputs({ N: 7 }));
  assert.equal(r.wireH_mm.min, 0.2);
  assert.ok(Math.abs(r.wireH_mm.max - 0.7) < 1e-9);          // (5.88/7 - 0.1) = 0.74 -> 0.7
  // more turns -> thinner max wire, live
  assert.ok(L.physicalRanges(inputs({ N: 14 })).wireH_mm.max < r.wireH_mm.max);
  // never below the manufacturing minimum
  assert.equal(L.physicalRanges(inputs({ N: 60 })).wireH_mm.max, 0.2);
});

t('turns: max = rows that fit the chosen wire, updating with the wire', () => {
  assert.equal(L.physicalRanges(inputs({ wireH_mm: 0.6 })).N.max, 8);   // 5.88/0.7 = 8.4
  assert.equal(L.physicalRanges(inputs({ wireH_mm: 0.2 })).N.max, 19);  // 5.88/0.3 = 19.6
  assert.equal(L.physicalRanges(inputs({ wireH_mm: 1.0 })).N.max, 5);   // 5.88/1.1 = 5.3
  assert.equal(L.physicalRanges(inputs({ wireH_mm: 0.6 })).N.basis, 'fit');
});

t('no slider combination can overflow the slot', () => {
  for (let h = 0.2; h <= 2.5001; h += 0.1) {
    const nMax = L.physicalRanges(inputs({ wireH_mm: h })).N.max;
    // the maximum fits ...
    assert.ok(!L.overflows(FIT, nMax, h) || nMax === 1, `N=${nMax} h=${h.toFixed(1)}`);
    // ... and one more row would not
    assert.ok(L.overflows(FIT, nMax + 1, h), `N+1 fits at h=${h.toFixed(1)}`);
  }
  for (let n = 1; n <= 25; n++) {
    const hMax = L.physicalRanges(inputs({ N: n })).wireH_mm.max;
    assert.ok(!L.overflows(FIT, n, hMax) || hMax === 0.2, `h=${hMax} n=${n}`);
  }
  assert.ok(L.overflows(FIT, 9, 0.6));
  assert.ok(!L.overflows(FIT, 8, 0.6));
});

t('current: inverter ceiling when a controller is set, else the passport rule', () => {
  const inv = L.physicalRanges(inputs({ iMaxA: 120 }));
  assert.equal(inv.I_A.basis, 'inverter'); assert.equal(inv.I_A.max, 120);
  const none = L.physicalRanges(inputs());
  assert.equal(none.I_A.basis, 'no_controller'); assert.equal(none.I_A.max, 80);   // 2 x 40
});

t('speed: envelope at the pack maximum (linear voltage model), else Kv x V x m', () => {
  // line peak 0.01 V per rpm + 1 V drop: at m x Vmax = 0.89 x 54.6 = 48.6 V -> 4760 rpm
  const e = L.speedLimit({ vMax: 54.6, m: 0.89, kvRpmPerV: 99, v0: 1, v1000: 11 });
  assert.equal(e.basis, 'envelope');
  assert.ok(Math.abs(e.rpm - (0.89 * 54.6 - 1) / 0.01) < 1e-6);
  const kv = L.speedLimit({ vMax: 54.6, m: 0.89, kvRpmPerV: 100, v0: null, v1000: null });
  assert.equal(kv.basis, 'kv'); assert.ok(Math.abs(kv.rpm - 100 * 54.6 * 0.89) < 1e-9);
  assert.equal(L.speedLimit({ vMax: null, m: 0.89, kvRpmPerV: 100, v0: 0, v1000: 10 }).basis, 'no_battery');
  assert.equal(L.DEFAULT_MODULATION, 0.89);
  const r = L.physicalRanges(inputs({ speed: e }));
  assert.equal(r.rpm.basis, 'envelope'); assert.equal(r.rpm.max, e.rpm);
  assert.equal(L.physicalRanges(inputs({ speed: kv })).rpm.basis, 'kv');
  const nb = L.physicalRanges(inputs());
  assert.equal(nb.rpm.basis, 'no_battery'); assert.equal(nb.rpm.max, 26000);
  // a voltage that already exceeds the pack at zero speed has no envelope
  assert.equal(L.envelopeRpm(60, 70, 48), null);
});

t('an admin can only narrow a physical range, never widen it', () => {
  const phys = { min: 3.6, max: 30 };
  assert.deepEqual(L.narrowRange(phys, { min: 10, max: 20 }), { min: 10, max: 20 });
  assert.deepEqual(L.narrowRange(phys, { min: 0, max: 100 }), { min: 3.6, max: 30 });
  assert.deepEqual(L.narrowRange(phys, { min: 10, max: 100 }), { min: 10, max: 30 });
  assert.deepEqual(L.narrowRange(phys, undefined), { min: 3.6, max: 30 });
  assert.deepEqual(L.narrowRange(phys, { min: 25, max: 5 }), { min: 3.6, max: 30 });  // inverted: ignored
  // a narrowing stored earlier is clamped again when the physical limit later shrinks
  assert.deepEqual(L.narrowRange({ min: 3.6, max: 15 }, { min: 10, max: 20 }), { min: 10, max: 15 });
});

t('the connection stays free: only a warning above the pack nominal', () => {
  assert.equal(L.lineVoltageWarning(40, 44.4), null);
  assert.equal(L.lineVoltageWarning(44.4, 44.4), null);
  assert.deepEqual(L.lineVoltageWarning(60, 44.4), { line: 60, nominal: 44.4 });
  assert.equal(L.lineVoltageWarning(60, null), null);
});

t('overrides are remembered per machine', () => {
  let raw = null;
  raw = L.writeOverrides(raw, 'cat:a', { L_mm: { min: 5, max: 20 } });
  raw = L.writeOverrides(raw, 'cat:b', { I_A: { min: 0, max: 50 } });
  assert.deepEqual(L.readOverrides(raw, 'cat:a'), { L_mm: { min: 5, max: 20 } });
  assert.deepEqual(L.readOverrides(raw, 'cat:b'), { I_A: { min: 0, max: 50 } });
  assert.deepEqual(L.readOverrides(raw, 'cat:c'), {});
  raw = L.writeOverrides(raw, 'cat:a', {});
  assert.deepEqual(L.readOverrides(raw, 'cat:a'), {});
  assert.deepEqual(L.readOverrides('not json', 'cat:a'), {});
});

t('the warehouse-stock hook is present and inert until the feed exists', () => {
  const free = L.physicalRanges(inputs({ N: 7 }));
  const stocked = L.physicalRanges(inputs({ N: 7, allowedWireH: [0.3, 0.5, 0.6] }));
  assert.equal(free.wireH_mm.max > 0.6, true);
  assert.equal(stocked.wireH_mm.max, 0.6);
  assert.deepEqual(L.physicalRanges(inputs({ allowedWireH: null })).wireH_mm, free.wireH_mm);
});
