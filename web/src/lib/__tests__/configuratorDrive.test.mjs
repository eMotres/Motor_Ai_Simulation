// node --test — Configure's Drive menu (owner 2026-10-05).
//
// Unlike the older tests here this one imports the module itself
// (lib/configuratorDrive.ts has no runtime imports), so it cannot drift from
// what the app ships.  It needs a Node that strips TypeScript types
// (>= 22.18 / 23.6 / 24); on an older Node the suite is skipped, not failed.
//
// What it pins:
//   * PWM lists exactly the passport's computed variants, labelled
//     "device · carrier kHz · tech";
//   * reading between computed points is bilinear over current x speed, exact
//     on a node, and REFUSED (specific reason) outside the computed envelope
//     or across a gap in the grid — never extrapolated;
//   * a variant is refused when its device cannot take the bus, the current or
//     the temperature;
//   * the per-machine memory of the choice and the saved-configuration text;
//   * Sine is the default: no drive field exists on a fresh knob set.
import test from 'node:test';
import assert from 'node:assert/strict';

let D = null;
try { D = await import('../configuratorDrive.ts'); } catch { /* old Node */ }
const t = D ? test : test.skip;

const pt = (rpm, I, k) => ({
  rpm, I_A: I, motor_pwm_loss_W: 100 * k, tj_C: 60 + 10 * k,
  inverter_loss_W: { cond: 10 * k, sw: 20 * k, dead: 2 * k },
  eta_drive_pct: 90 + k, eta_shaft_pct: 92 + k, p_cont_max_W: 1000 * k,
});

const V1 = {
  id: 'iqe018-48k', device: 'IQE018N06NM6SC', technology: 'Si', carrier_hz: 48000,
  dead_time_s: 1e-7, n_parallel: 2, modulation: 'SVPWM centred', m_max: 1.1,
  bus_v: { min: 36, nom: 44.4, max: 50.4 }, provenance: 'run 2026-10-05',
  points: {
    a: pt(1000, 10, 1), b: pt(1000, 30, 2),
    c: pt(3000, 10, 3), d: pt(3000, 30, 4),
  },
};
const V2 = { id: 'igc016-100k', device: 'IGC016K10S2', technology: 'GaN', carrier_hz: 100000,
  points: { 'rpm=2000,I=20A': pt(2000, 20, 5) } };

t('usableVariants keeps only complete entries; labels read device · kHz · tech', () => {
  const list = D.usableVariants([V1, V2, { id: 'x' }, null, { id: 'y', device: 'D', carrier_hz: 0, points: { a: {} } }]);
  assert.deepEqual(list.map((v) => v.id), ['iqe018-48k', 'igc016-100k']);
  assert.equal(D.variantLabel(V1), 'IQE018N06NM6SC · 48 kHz · Si');
  assert.equal(D.variantLabel(V2), 'IGC016K10S2 · 100 kHz · GaN');
  assert.deepEqual(D.usableVariants(undefined), []);
  assert.deepEqual(D.usableVariants(null), []);
});

t('variantFacts: dead time, parallel, bus, provenance', () => {
  const f = D.variantFacts(V1);
  assert.equal(f.deadTime, '100 ns');
  assert.equal(f.nParallel, 2);
  assert.equal(f.bus, '36–50.4 V (nom 44.4)');
  assert.equal(f.provenance, 'run 2026-10-05');
  assert.equal(D.variantFacts({ ...V1, dead_time_s: 2e-6 }).deadTime, '2 µs');
  assert.equal(D.variantFacts(V2).deadTime, null);
});

t('a node is read exactly', () => {
  const r = D.readVariant(V1, 1000, 30);
  assert.ok(r.ok && r.exact);
  assert.equal(r.values.motor_pwm_loss_W, 200);
  assert.equal(r.values.inv_total_W, 2 * 32);
  assert.equal(r.values.tj_C, 80);
});

t('between nodes the read is bilinear over current x speed', () => {
  const r = D.readVariant(V1, 2000, 20);       // centre of the cell: mean of 1..4
  assert.ok(r.ok && !r.exact);
  assert.ok(Math.abs(r.values.motor_pwm_loss_W - 250) < 1e-9);
  assert.ok(Math.abs(r.values.eta_drive_pct - 92.5) < 1e-9);
  const q = D.readVariant(V1, 1000, 20);       // on the rpm edge, mid current
  assert.ok(Math.abs(q.values.motor_pwm_loss_W - 150) < 1e-9);
});

t('outside the computed envelope is refused with the reason, never extrapolated', () => {
  let r = D.readVariant(V1, 4000, 20);
  assert.ok(!r.ok); assert.equal(r.refusal.kind, 'speed');
  assert.deepEqual([r.refusal.lo, r.refusal.hi], [1000, 3000]);
  r = D.readVariant(V1, 2000, 45);
  assert.ok(!r.ok); assert.equal(r.refusal.kind, 'current');
  assert.deepEqual([r.refusal.lo, r.refusal.hi], [10, 30]);
  // a hair over the edge is still the edge (typed 3000.0 vs 3000)
  assert.ok(D.readVariant(V1, 3000.5, 30).ok);
});

t('a hole in the grid is refused, not filled', () => {
  const holed = { ...V1, points: { a: pt(1000, 10, 1), b: pt(1000, 30, 2), c: pt(3000, 10, 3) } };
  const r = D.readVariant(holed, 2000, 20);
  assert.ok(!r.ok); assert.equal(r.refusal.kind, 'gap');
});

t('a single computed point reads only at itself; coordinates may ride in the key', () => {
  assert.ok(D.readVariant(V2, 2000, 20).ok);
  assert.ok(!D.readVariant(V2, 2500, 20).ok);
  const named = { ...V1, points: { L155_rated: { motor_pwm_loss_W: 1 } } };
  const r = D.readVariant(named, 1000, 10);
  assert.ok(!r.ok); assert.equal(r.refusal.kind, 'no_coords');
});

t('a missing field stays null instead of becoming zero', () => {
  const v = { ...V1, points: { ...V1.points, a: { ...pt(1000, 10, 1), tj_C: null } } };
  const r = D.readVariant(v, 2000, 20);
  assert.ok(r.ok); assert.equal(r.values.tj_C, null);
  assert.ok(r.values.motor_pwm_loss_W > 0);
});

t('build knobs off the loaded machine are flagged; current and speed are free', () => {
  const base = { N: 12, L_mm: 40, wireH_mm: 1, nP: 1, I_A: 10, rpm: 1000 };
  assert.equal(D.buildTuned({ ...base, I_A: 25, rpm: 2500 }, base), false);
  assert.equal(D.buildTuned({ ...base, N: 13 }, base), true);
  assert.equal(D.buildTuned({ ...base, L_mm: 41 }, base), true);
  assert.equal(D.buildTuned(base, null), false);
});

t('limits: bus headroom, per-switch current, junction, pack beyond the variant', () => {
  const r = D.readVariant(V1, 1000, 30).values;       // tj 80
  const dev = { v_dss_V: 60, i_d_100c_A: 100, t_j_max_c: 175 };
  assert.deepEqual(D.limitProblems(V1, r, 30, dev, 50.4), []);        // 50.4 <= 54
  let p = D.limitProblems(V1, r, 30, { ...dev, v_dss_V: 55 }, 50.4);  // 0.9*55 = 49.5
  assert.equal(p.length, 1); assert.equal(p[0].kind, 'vds');
  p = D.limitProblems(V1, r, 30, { ...dev, i_d_100c_A: 10 }, 50.4);   // 30/sqrt2/2 = 10.6
  assert.equal(p[0].kind, 'rating');
  p = D.limitProblems(V1, { ...r, tj_C: 180 }, 30, dev, 50.4);
  assert.equal(p[0].kind, 'tj');
  p = D.limitProblems(V1, r, 30, dev, 60);                            // pack above the variant's bus
  assert.ok(p.some((q) => q.kind === 'bus'));
  // no catalogue card known: only the variant's own bus envelope can speak
  assert.deepEqual(D.limitProblems(V1, r, 30, null, 50.4), []);
});

t('the choice is remembered per machine and Sine forgets it', () => {
  let raw = null;
  raw = D.writeDriveChoice(raw, 'cat:a', { drive: 'pwm', drive_variant: 'v1' });
  raw = D.writeDriveChoice(raw, 'cat:b', { drive: 'pwm', drive_variant: 'v2' });
  assert.deepEqual(D.readDriveChoice(raw, 'cat:a'), { drive: 'pwm', drive_variant: 'v1' });
  assert.deepEqual(D.readDriveChoice(raw, 'cat:b'), { drive: 'pwm', drive_variant: 'v2' });
  assert.equal(D.readDriveChoice(raw, 'cat:c'), null);
  raw = D.writeDriveChoice(raw, 'cat:a', { drive: 'sine', drive_variant: 'v1' });
  assert.equal(D.readDriveChoice(raw, 'cat:a'), null);
  assert.equal(D.readDriveChoice('not json', 'cat:a'), null);
  assert.equal(D.readDriveChoice(null, 'cat:a'), null);
});

t('Sine is the default: a fresh knob set carries no drive field', () => {
  assert.deepEqual(D.pickDrive({ N: 1 }), {});
  assert.deepEqual(D.pickDrive({ drive: 'sine', drive_variant: 'v1' }), { drive_variant: 'v1' });
  assert.deepEqual(D.pickDrive({ drive: 'pwm', drive_variant: 'v1' }), { drive: 'pwm', drive_variant: 'v1' });
});

t('scaleMotor ignores every drive field: Sine numbers cannot move', async () => {
  const M = await import('../motorScaling.ts');
  const p = { N0: 7, L0_mm: 12, wireH0_mm: 0.6, I0_A: 40, rpm0: 13000, nP0: 1, T0_Nm: 0.6,
    Vemf0_peak_V: 10, R0_ohm: 0.01, endWindFrac: 0.4, Pfe0_W: 8, Pmag0_W: 3, mass0_kg: 0.09 };
  const k = { N: 8, L_mm: 15, wireH_mm: 0.6, nP: 1, I_A: 30, rpm: 9000 };
  const a = JSON.stringify(M.scaleMotor(p, k, 14));
  assert.equal(JSON.stringify(M.scaleMotor(p, { ...k, drive: 'pwm', drive_variant: 'v1' }, 14)), a);
  assert.equal(JSON.stringify(M.scaleMotor(p, { ...k, drive: 'sine' }, 14)), a);
});

t('driveText: the saved-configuration cell', () => {
  assert.equal(D.driveText(null), 'Sine');
  assert.equal(D.driveText({ mode: 'sine' }), 'Sine');
  assert.equal(D.driveText({ mode: 'pwm', device: 'IGC016K10S2', carrier_hz: 100000 }), 'PWM · IGC016K10S2 · 100 kHz');
  assert.equal(D.driveText({ mode: 'pwm' }), 'PWM');
});
