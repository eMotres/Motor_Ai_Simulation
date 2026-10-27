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
//   * current: the lowest motor limit (motor peak / demag / passport grid; the controller
//     the board is only a marker, never a cap), named; never the bare MOSFET rating; none known = the passport
//     rule ("no limit"); speed: the envelope at the pack maximum, else Kv x V x m;
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
  iCap: null, speed: NO_SPEED, ...o });

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

t('current: the binding real limit is named, none known = the passport rule', () => {
  for (const [kind, basis] of [['peak', 'motor_peak'], ['demag', 'demag'], ['grid', 'grid']]) {
    const r = L.physicalRanges(inputs({ iCap: { kind, A: 46 } }));
    assert.equal(r.I_A.basis, basis); assert.equal(r.I_A.max, 46); assert.equal(r.I_A.min, 0);
  }
  const none = L.physicalRanges(inputs());
  assert.equal(none.I_A.basis, 'no_limit'); assert.equal(none.I_A.max, 80);   // 2 x 40
});

t('current: the lowest motor limit binds; equal limits keep the motor first', () => {
  const caps = (o) => L.buildCurrentCaps({ peak: { A: 46, s: 1 }, demag: { A: 52, s: 1 }, gridTopA: 64.7, sLive: 1, ...o });
  assert.equal(L.bindingCurrentCap(caps({})).kind, 'peak');
  assert.equal(L.bindingCurrentCap(caps({ demag: { A: 46, s: 1 } })).kind, 'peak');
  assert.equal(L.bindingCurrentCap(caps({ peak: { A: 60, s: 1 } })).kind, 'demag');
  assert.equal(L.bindingCurrentCap(caps({ peak: null, demag: null })).kind, 'grid');
  assert.equal(L.bindingCurrentCap([]), null);
});

t('current: motor limits move with the ampere-turns', () => {
  // twice the series turns per ampere -> half the current for the same ampere-turns
  const by = (sLive) => Object.fromEntries(L.buildCurrentCaps({
    peak: { A: 46, s: 1 }, demag: { A: 52, s: 1 }, gridTopA: 64.7, sLive }).map((c) => [c.kind, c.A]));
  assert.equal(by(2).peak, 23); assert.equal(by(2).demag, 26);
  assert.equal(L.buildCurrentCaps({ peak: { A: 46, s: 1 }, demag: null, gridTopA: null, sLive: 0.5 })[0].A, 92);
  assert.deepEqual(L.buildCurrentCaps({ peak: null, demag: null, gridTopA: 61, sLive: 2 }), [{ kind: 'grid', A: 30.5 }]);
});

t('current: the passport grid top is a data bound, never lower than a saved duty', () => {
  const caps = L.buildCurrentCaps({ peak: { A: 48.8, s: 1 }, demag: null, gridTopA: 40, sLive: 1 });
  assert.deepEqual(caps.map((c) => c.kind), ['peak']);
  assert.deepEqual(L.buildCurrentCaps({ peak: null, demag: null, gridTopA: 61, sLive: 1 }), [{ kind: 'grid', A: 61 }]);
});

t('current: the peak duty is the highest duty named peak, nothing else', () => {
  assert.equal(L.peakDutyA([{ name: 'rated', current_A: 7.17 }, { name: 'peak', current_A: 45.962 }]), 45.962);
  assert.equal(L.peakDutyA([{ name: 'rated', current_A: 7.17 }]), null);
  assert.equal(L.peakDutyA([{ name: 'Peak 2', current_A: 30 }, { name: 'peak', current_A: 40 }, { name: 'peak3', current_A: null }]), 40);
  assert.equal(L.peakDutyA(undefined), null);
  assert.equal(L.highestDutyA([{ current_A: 7 }, { current_A: 46 }, { current_A: null }]), 46);
});

t('the controller board is a marker, never a cap', () => {
  const m = L.boardMarker({ A: 26.1, device: 'IQE018N06NM6SC', n: 1 }, 43.18);
  assert.deepEqual(m, { A: 26.1, device: 'IQE018N06NM6SC', n: 1, over: true });
  assert.equal(L.boardMarker({ A: 26.1, device: 'D', n: 1 }, 20).over, false);
  assert.equal(L.boardMarker(null, 20), null);
  // no cap kind exists for it, and the maximum ignores it
  const cap = L.sliderCurrentCap({ duties: [{ name: 'rated', current_A: 43.18 }, { name: 'peak', current_A: 48.79 }],
    sDuty: 1, demag: null, gridTopA: 61, sLive: 1 });
  assert.equal(cap.A, 48.79);
});

// The SAVED duties of the owner machines (rated / peak phase current, A rms) - a loaded rated or peak mode must
// always sit inside the slider, whatever the passport's demag limit, grid top or the board say (launch rule).
const MACHINES = {
  '40 L12': { rated: 43.18, peak: 48.79 }, '40 L20': { rated: 36.97, peak: 42.3 },
  '50 L15': { rated: 38.2, peak: 53 }, '85 L13': { rated: 24.749, peak: 45.962 },
  '85 L15': { rated: 7.17, peak: 45.962 }, '150 L35': { rated: 108.47, peak: null },
};
t('a loaded rated or peak preset always sits inside the slider range, on every machine', () => {
  for (const [name, m] of Object.entries(MACHINES)) {
    const duties = [{ name: 'rated', current_A: m.rated }, ...(m.peak ? [{ name: 'peak', current_A: m.peak }] : [])];
    // hostile data: a demag limit and a grid top BELOW the saved duties, a lower board, other winding scale
    for (const [demag, grid] of [[null, null], [30, 25], [m.rated * 0.6, m.rated * 0.5], [m.rated * 3, m.rated * 4]]) {
      const cap = L.sliderCurrentCap({ duties, sDuty: 1, demag: demag == null ? null : { A: demag, s: 1 }, gridTopA: grid, sLive: 1 });
      const max = L.physicalRanges(inputs({ iCap: cap })).I_A.max;
      assert.ok(m.rated <= max + 1e-9, `${name} rated ${m.rated} <= ${max}`);
      if (m.peak) assert.ok(m.peak <= max + 1e-9, `${name} peak ${m.peak} <= ${max}`);
    }
  }
  // the saved peaks are the cap when nothing is tighter, and it is named a motor peak
  const cap = L.sliderCurrentCap({ duties: [{ name: 'rated', current_A: 7.17 }, { name: 'peak', current_A: 45.962 }],
    sDuty: 1, demag: { A: 52.4, s: 1 }, gridTopA: 64.7, sLive: 1 });
  assert.equal(cap.kind, 'peak'); assert.equal(cap.A, 45.962);
  // a demag limit between rated and peak is not allowed to cut the loaded peak off
  const cut = L.sliderCurrentCap({ duties: [{ name: 'rated', current_A: 43.18 }, { name: 'peak', current_A: 48.79 }],
    sDuty: 1, demag: { A: 33, s: 1 }, gridTopA: 61, sLive: 1 });
  assert.ok(cut.A >= 48.79);
  // the floor moves with the winding like the limits do
  assert.equal(L.sliderCurrentCap({ duties: [{ name: 'peak', current_A: 40 }], sDuty: 1, demag: { A: 10, s: 1 }, gridTopA: null, sLive: 2 }).A, 20);
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

// ── the speed maximum is the LOWEST of the pack, the bearings and the hand-set value (owner 2026-10-28) ──
const PACK = { rpm: 9000, basis: 'envelope' };
const BRG = { set: true, rpm: 6300, lubrication: 'grease', cards: ['61814-2RZ'] };

t('speed caps: the lowest binds, the pack on a tie, an invalid pack stays invalid', () => {
  assert.deepEqual(L.withSpeedCaps(PACK, {}), PACK);                                   // nothing to combine
  assert.deepEqual(L.withSpeedCaps(PACK, { bearing: { set: false, reason: 'x' }, handRpm: null }), PACK);
  assert.deepEqual(L.withSpeedCaps(PACK, { bearing: BRG }), { rpm: 6300, basis: 'bearing', packRpm: 9000 });
  assert.deepEqual(L.withSpeedCaps(PACK, { bearing: BRG, handRpm: 5000 }), { rpm: 5000, basis: 'hand', packRpm: 9000 });
  assert.deepEqual(L.withSpeedCaps(PACK, { bearing: BRG, handRpm: 7000 }), { rpm: 6300, basis: 'bearing', packRpm: 9000 });
  assert.deepEqual(L.withSpeedCaps(PACK, { bearing: { ...BRG, rpm: 9000 }, handRpm: 9000 }), PACK);   // tie: the pack
  assert.deepEqual(L.withSpeedCaps({ rpm: 4000, basis: 'kv' }, { bearing: BRG, handRpm: 5000 }), { rpm: 4000, basis: 'kv' });
  // no pack known: the bearings still limit, with no pack figure beside them
  assert.deepEqual(L.withSpeedCaps({ rpm: null, basis: 'no_battery' }, { bearing: BRG }), { rpm: 6300, basis: 'bearing', packRpm: null });
  const bad = { rpm: null, basis: 'invalid_battery' };
  assert.equal(L.withSpeedCaps(bad, { bearing: BRG, handRpm: 100 }), bad);
  // a not-set / customer-owned bearing limit and junk hand values never limit
  assert.deepEqual(L.withSpeedCaps(PACK, { bearing: { set: false, customer_owned: true }, handRpm: 0 }), PACK);
  assert.deepEqual(L.withSpeedCaps(PACK, { handRpm: Number.NaN }), PACK);
});

t('speed caps: the slider range carries the binding basis and every judgement reads the combined limit', () => {
  const lim = L.withSpeedCaps(PACK, { bearing: BRG });
  const r = L.physicalRanges(inputs({ speed: lim }));
  assert.equal(r.rpm.basis, 'bearing'); assert.equal(r.rpm.max, 6300);
  assert.equal(L.physicalRanges(inputs({ speed: L.withSpeedCaps(PACK, { handRpm: 5000 }) })).rpm.basis, 'hand');
  assert.equal(L.physicalRanges(inputs({ speed: PACK })).rpm.basis, 'envelope');
  // a speed above the bearings is refused although the pack would carry it
  assert.equal(L.speedRequestAccepted(7000, PACK.rpm), true);
  assert.equal(L.speedRequestAccepted(7000, lim.rpm), false);
  assert.equal(L.speedRequestAccepted(6300, lim.rpm), true);
});
