import test from 'node:test';
import assert from 'node:assert/strict';

let P;
try { P = await import('../propellerDesign.ts'); } catch { /* old Node */ }
const t = P ? test : test.skip;

const rows = [
  { name: 'blades', value: 2, unit: '-', group: 'topology', options: [2, 3] },
  { name: 'chord_R@0.2', value: 0.17, lo: 0.05, hi: 0.3, unit: 'c/R', group: 'inner' },
  { name: 'chord_R@0.65', value: 0.19, lo: 0.05, hi: 0.3, unit: 'c/R', group: 'outer' },
  { name: 'airfoil_tip', value: 'naca4-4408', unit: '-', group: 'airfoil', options: ['naca4-4408'] },
  { name: 'weird', value: 1, unit: '-', group: 'nope' },
];

t('rows are grouped in display order and an unknown group is kept, not dropped', () => {
  const g = P.groupRows(rows);
  assert.deepEqual(g.map((x) => x.group), ['inner', 'outer', 'airfoil', 'topology']);
  assert.deepEqual(g[3].rows.map((r) => r.name), ['blades', 'weird']);
  assert.deepEqual(P.groupRows([]), []);
});

t('bounds: out of range, not finite and unbounded rows', () => {
  const r = rows[1];
  assert.equal(P.outOfBounds(r, 0.17), false);
  assert.equal(P.outOfBounds(r, 0.04), true);
  assert.equal(P.outOfBounds(r, 0.31), true);
  assert.equal(P.outOfBounds(r, NaN), true);
  assert.equal(P.outOfBounds(r, '0.1'), true);
  assert.equal(P.outOfBounds(rows[0], 'anything'), false);       // no bounds stated
  assert.equal(P.outOfBounds({ lo: 1 }, 5), false);
});

t('number parsing accepts a decimal comma and refuses junk', () => {
  assert.equal(P.parseNumber(' 0,25 '), 0.25);
  assert.equal(P.parseNumber('12'), 12);
  assert.equal(P.parseNumber(''), null);
  assert.equal(P.parseNumber('abc'), null);
  assert.equal(P.parseNumber('1e999'), null);
});

t('run labels name what changed', () => {
  const a = { x: 1, y: 2, z: 'a' };
  assert.equal(P.runLabel(null, a), 'start');
  assert.equal(P.runLabel(a, { ...a }), 'same');
  assert.equal(P.runLabel(a, { ...a, x: 1.23456789 }), 'x=1.23');                // display rounding: two decimals (owner 2026-11-16)
  assert.equal(P.runLabel(a, { x: 5, y: 6, z: 'b', w: 1 }, 2), 'x=5, y=6 +2');
  assert.deepEqual(P.changedNames(null, a), ['x', 'y', 'z']);
});

t('the Pareto front keeps the non-dominated runs, ignores runs with a missing objective, and handles ties', () => {
  const runs = [
    { thrust_per_shaft_W: 0.040, air_ms: 9, constraintsMet: true },     // 0 front (best efficiency)
    { thrust_per_shaft_W: 0.038, air_ms: 11, constraintsMet: true },    // 1 front
    { thrust_per_shaft_W: 0.036, air_ms: 10, constraintsMet: true },    // 2 dominated by 1
    { thrust_per_shaft_W: 0.030, air_ms: 14, constraintsMet: true },    // 3 front (most air)
    { thrust_per_shaft_W: null, air_ms: 20, constraintsMet: true },     // 4 not comparable
    { thrust_per_shaft_W: 0.040, air_ms: 9, constraintsMet: true },     // 5 equal to 0: neither dominates, both stay
  ];
  assert.deepEqual(P.paretoFront(runs), [0, 1, 3, 5]);
  assert.deepEqual(P.paretoFront([]), []);
  assert.deepEqual(P.paretoFront([{ thrust_per_shaft_W: null, air_ms: null }]), []);
});

t('an infeasible run never enters the front, even with the best objectives (review 2026-11-21 H3)', () => {
  const runs = [
    { thrust_per_shaft_W: 0.050, air_ms: 20, constraintsMet: false },   // 0 best numbers, infeasible
    { thrust_per_shaft_W: 0.040, air_ms: 9, constraintsMet: true },     // 1 front
    { thrust_per_shaft_W: 0.030, air_ms: 14 },                          // 2 unknown feasibility = not on the front (review 2026-11-22)
    { thrust_per_shaft_W: 0.030, air_ms: 13, constraintsMet: true },    // 3 front
  ];
  assert.deepEqual(P.paretoFront(runs), [1, 3]);
});

t('front cost: efficiency given up per m/s gained, null with fewer than two points', () => {
  const runs = [{ thrust_per_shaft_W: 0.040, air_ms: 9 }, { thrust_per_shaft_W: 0.030, air_ms: 14 }];
  assert.equal(P.frontCost(runs, [0, 1]), (0.040 - 0.030) / 5);
  assert.equal(P.frontCost(runs, [0]), null);
  assert.equal(P.frontCost([{ thrust_per_shaft_W: 0.04, air_ms: 9 }, { thrust_per_shaft_W: 0.03, air_ms: 9 }], [0, 1]), null);
});

t('planform polygon is closed around the pitch axis and the profile keeps gaps as null', () => {
  const poly = P.planformPolygon([{ r_mm: 10, chord_mm: 20, twist_deg: 30 }, { r_mm: 20, chord_mm: 10, twist_deg: 10 }]);
  assert.deepEqual(poly, [[10, 5], [20, 2.5], [20, -7.5], [10, -15]]);
  const rows2 = P.profileRows({ r_m: [0.01, null, 0.03], axial_total_ms: [5, null, 7], swirl_ms: [1, 2, null] });
  assert.deepEqual(rows2.map((r) => r.axial), [5, null, 7]);
  assert.equal(rows2[0].r_mm, 10);
  assert.equal(rows2[2].swirl, null);
});

t('key flags keep a fixed order and drop the rest; formatting never prints NaN', () => {
  assert.deepEqual(P.keyFlags(['rpm_capped', 'x', 'root_flow_not_validated']), ['root_flow_not_validated', 'rpm_capped']);
  assert.equal(P.fmt(null), '—');
  assert.equal(P.fmt(NaN), '—');
  assert.equal(P.fmt(1.2345, 1), '1.2');
});

t('display rounding: at most two decimals, small values keep two significant digits, the value itself is not changed', () => {
  assert.equal(P.round2(38.511887253966584), 38.51);
  assert.equal(P.fmt2(38.511887253966584), '38.51');
  assert.equal(P.fmt2(0.0412345), '0.041');
  assert.equal(P.fmt2(-0.5), '-0.5');
  assert.equal(P.fmt2(0), '0');
  assert.equal(P.fmt2(null), '—');
  assert.equal(P.fmt2(NaN), '—');
  assert.equal(P.fmt2(P.gPerW(0.0401)), '4.09');                                              // N/W shown as g/W
  assert.equal(P.gPerW(null), null);
  const v = { x: 38.511887253966584 };
  P.runLabel(null, v);
  assert.equal(v.x, 38.511887253966584);
});

t('toRun copies the vector so a later edit cannot change the stored run', () => {
  const v = { a: 1 };
  const run = P.toRun(1, 'start', v, { design_id: 'd', operating_point: 'L12', operating: { rpm: 1000, torque_limit_Nm: 0.6, motor_radius_mm: 20 },
    thrust_N: 1, torque_Nm: 0.6, shaft_power_W: 100, tip_mach: 0.5, objectives: { thrust_per_shaft_W: 0.01, cooling_air_axial_ms: 8 },
    constraints: {}, constraints_met: true, profile: { r_m: [], axial_total_ms: [], swirl_ms: [] }, flags: [] });
  v.a = 99;
  assert.equal(run.vector.a, 1);
  assert.equal(run.air_ms, 8);
});
