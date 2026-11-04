// node --test - the Electromagnetic Simulation tab's POINT SWITCHER (owner 2026-11-04):
// the Motors catalog lists motors only and loads each configuration's default duty;
// every other duty is a point chosen here.  Imports lib/simPoints.ts itself (Node >= 22.18 / 24).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  configOfTree, driveAfterSwitch, drivesOf, isStale, pointsOf, pointTip, switcherLayout,
} from '../../../lib/simPoints.ts';
import { configButtons, defaultDutyOf } from '../../../lib/catalogRows.ts';

const read = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
const word = (id) => ({ current: 'Sine', pwm_voltage: 'PWM' }[id] ?? id);

const run = (drive, o = {}) => ({ drive, has_payload: true, ...o });
const duty = (name, o = {}) => ({
  name, mode: 'motor', current_arms: 240, rpm: 3000, gamma_deg: 0, torque_nm: 95.5, power_kw: 30,
  primary_drive: 'current', runs: [run('current', { primary: true }), run('pwm_voltage')],
  result: { efficiency_pct: 94.1, build_sig: 'B1' }, ...o,
});
const cfg = (duties, o = {}) => ({ name: 'L40', build_sig: 'B1', default_duty: 'rated', duties, ...o });

test('chips: rated first, then peak, then the rest in saved order', () => {
  const c = cfg([duty('30 kW'), duty('peak'), duty('2000 rpm'), duty('rated')]);
  assert.deepEqual(pointsOf(c, true).map((p) => p.name), ['rated', 'peak', '30 kW', '2000 rpm']);
});

test('the default point (the star) is the one the catalog loads', () => {
  const c = cfg([duty('peak'), duty('rated')]);
  const p = pointsOf(c, true);
  assert.deepEqual(p.filter((x) => x.isDefault).map((x) => x.name), ['rated']);
  assert.equal(defaultDutyOf(c).name, 'rated');
  assert.equal(configButtons({ name: 'D', configs: [c] })[0].duty, 'rated');
});

test('no duty called rated: the flag, then the first "rated ..." name, then the first duty - one rule for catalog and switcher', () => {
  const flagged = cfg([duty('a'), duty('b')], { default_duty: 'b' });
  assert.equal(pointsOf(flagged, true).find((p) => p.isDefault).name, 'b');
  assert.equal(configButtons({ name: 'D', configs: [flagged] })[0].duty, 'b');
  const prefixed = cfg([duty('peak'), duty('rated 0.5x9 mm')], { default_duty: undefined });
  assert.equal(pointsOf(prefixed, true).find((p) => p.isDefault).name, 'rated 0.5x9 mm');
  const first = cfg([duty('x'), duty('y')], { default_duty: undefined });
  assert.equal(pointsOf(first, true).find((p) => p.isDefault).name, 'x');
  assert.equal(configButtons({ name: 'D', configs: [first] })[0].duty, 'x');
});

test('a stored name with a Cyrillic look-alike (rated 120<С>) is matched as the tree sends it, never re-typed', () => {
  const name = 'rated 120С';                       // Cyrillic С, the 2026-09-19 trap
  const c = cfg([duty('peak'), duty(name)], { default_duty: undefined });
  assert.equal(pointsOf(c, true).find((p) => p.isDefault).name, name);
  assert.equal(configButtons({ name: 'D', configs: [c] })[0].duty, name);
});

test('a customer does not see test points; an editor does', () => {
  const c = cfg([duty('rated'), duty('peak'), duty('noload_20C', { kind: 'test' })]);
  assert.deepEqual(pointsOf(c, false).map((p) => p.name), ['rated', 'peak']);
  assert.equal(pointsOf(c, true).length, 3);
});

test('stale: the saved result or a stored run is from another build', () => {
  const c = cfg([duty('a', { result: { build_sig: 'OLD' } }), duty('b'),
                 duty('c', { runs: [run('current', { stale: true })] })]);
  assert.deepEqual(c.duties.map((d) => isStale(d, c)), [true, false, true]);
  assert.deepEqual(pointsOf(c, true).map((p) => p.stale), [true, false, true]);
});

test('drives: the primary excitation first, as the run chips show them', () => {
  assert.deepEqual(drivesOf(duty('x', { runs: [run('pwm_voltage'), run('current')] })), ['current', 'pwm_voltage']);
  assert.deepEqual(drivesOf(duty('x', { runs: [] })), []);
});

test('the tooltip is one line: mode, power, torque, rpm, current, gamma, efficiency, stored drives', () => {
  const c = cfg([duty('rated')]);
  const tip = pointTip(c.duties[0], c, word);
  assert.equal(tip, 'motor · 30.0 kW · 95.5 N·m · 3000 rpm · 240 A · γ 0° · η 94.1 % · Sine + PWM');
  assert.ok(!tip.includes('\n'));
  assert.match(pointTip(duty('p', { result: { build_sig: 'OLD' } }), c, word), /older build/);
  // six-phase: the current is shown per set, like the rest of the app
  assert.match(pointTip(c.duties[0], { ...c, phases: 6 }, word), /120 A\/set/);
});

test('the switcher: chips from two points, editor controls from one, nothing for a customer with one', () => {
  const two = pointsOf(cfg([duty('rated'), duty('peak')]), false);
  const one = pointsOf(cfg([duty('rated')]), false);
  assert.deepEqual(switcherLayout(two, false, 'rated'), { chips: true, controls: false });
  assert.deepEqual(switcherLayout(one, false, 'rated'), { chips: false, controls: false });
  assert.deepEqual(switcherLayout(one, true, 'rated'), { chips: false, controls: true });
  assert.deepEqual(switcherLayout(two, true, 'rated'), { chips: true, controls: true });
});

test('changing the point keeps the excitation that was on screen when the new point has it', () => {
  const both = { drives: ['current', 'pwm_voltage'], primaryDrive: 'current' };
  const sineOnly = { drives: ['current'], primaryDrive: 'current' };
  assert.equal(driveAfterSwitch('pwm_voltage', both), 'pwm_voltage');
  assert.equal(driveAfterSwitch('pwm_voltage', sineOnly), null);   // not stored: the primary stays
  assert.equal(driveAfterSwitch('current', both), null);           // the load already shows the primary
  assert.equal(driveAfterSwitch(null, both), null);
});

test('configOfTree finds the configuration of the loaded die', () => {
  const dies = [{ name: 'D1', configs: [{ name: 'L40', duties: [] }] }];
  assert.equal(configOfTree(dies, 'D1', 'L40').name, 'L40');
  assert.equal(configOfTree(dies, 'D1', 'nope'), null);
  assert.equal(configOfTree(dies, null, 'L40'), null);
});

// ── wiring, pinned from the shipped source ──────────────────────────────────

test('the dashboard mounts the switcher beside the run chips', () => {
  const dash = read('../PhysicsDashboard.tsx');
  assert.match(dash, /<SimPointSwitcher \/>[\s\S]{0,400}<StoredRunSelector \/>/);
});

test('a click loads through the one shared load and keeps the excitation through the shared run code', () => {
  const sw = read('../SimPointSwitcher.tsx');
  assert.match(sw, /applyDutyEverywhere\(duty\.die, duty\.config, name, canWrite\)/);
  assert.match(sw, /showStoredRun\(/);
  assert.match(read('../StoredRunSelector.tsx'), /await showStoredRun\(duty, drive\)/);
  assert.doesNotMatch(sw, /duty_result|duty_run/);   // switching writes nothing
});

test('the duty controls moved here: create, rename, duplicate, delete, default, save, pictures, history', () => {
  const sw = read('../SimPointSwitcher.tsx');
  for (const f of ['createDuty', 'renameDuty', 'duplicateDuty', 'deleteDuty', 'setDefaultDuty', 'saveResults', 'reportPicturesKey', 'ConfigHistoryDialog']) {
    assert.ok(sw.includes(f), f);
  }
  const ops = read('../../../lib/dutyOps.ts');
  assert.match(ops, /\/api\/family\/duty`/);
  assert.match(ops, /default_duty/);
  assert.match(ops, /\/duplicate/);
});

test('the duty-cycle chip in the tooltip goes through its feature gate', () => {
  assert.match(read('../SimPointSwitcher.tsx'), /gatedDutyCycleChip\(dutyCycleChip\(d\.duty_cycle\)\)/);
});

test('the catalog has no duty rows or duty controls any more; motors load their default duty', () => {
  const fam = read('../../catalog/FamilyCatalog.tsx');
  assert.doesNotMatch(fam, /renameDuty|duplicateDuty|deleteDuty|createDuty|setDefaultDuty/);
  assert.doesNotMatch(fam, /Box component="table"/);
  assert.doesNotMatch(fam, /＋ duty/);
  assert.match(fam, /configClickPlan\(btns, b\.config/);                      // the configuration button still loads
  assert.match(fam, /void applyDuty\(die\.name, plan\.pick, plan\.load\)/);
  // the report-pictures choice is read from the key the switcher writes
  assert.match(fam, /report\.pictures:\$\{die\}\/\$\{cfg\}/);
  assert.match(read('../../../lib/dutyOps.ts'), /`report\.pictures:\$\{die\}\/\$\{cfg\}`/);
});
