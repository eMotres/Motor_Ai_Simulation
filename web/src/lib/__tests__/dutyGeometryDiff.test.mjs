/**
 * node --test — lib/dutyGeometryDiff.ts: the rounding/formatting behind the
 * "duty saved on a different geometry" modal (lib/dutyApply.ts,
 * components/catalog/DutyGeometryDialog.tsx) and the choice-mapping helpers
 * it shares with them.
 *
 * The module is dependency-free (no `import.meta.env`), so node imports the
 * SHIPPED file — same convention as lib/timeToLimitChip.ts.
 *
 * What this pins (incident 2026-09-29: the old window.confirm() printed
 * "rotor_inner_radius 4.800000000000002 → 5.000000000000002" — the owner
 * could not tell what actually differs, and picked the wrong button):
 *
 *   1. a count key (segments/slots/poles/wires/strands) rounds to a plain
 *      integer, no unit;
 *   2. a physical dimension rounds to 0.001 mm and carries the "mm" unit;
 *   3. an angle key rounds to 4 significant digits and carries "°";
 *   4. apply is disabled with a reason on a locked die OR a foreign
 *      lamination, and offered with no reason otherwise;
 *   5. the cancelled-load message is one fixed string both the dialog's
 *      Cancel button and "no dialog could be shown" resolve to.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  isCountKey, toSignificantDigits, roundDutyGeometryValue, formatDutyGeometryValue,
  formatDutyGeometryDiffRow, dutyGeometryApplyDisabledReason, dutyGeometryCancelledMessage,
  formatDutyGeometryKeyList, dutyGeometryButtonPlan, dutyGeometryApplyDisabledCode,
  dutyGeometryRewritesDie,
} from '../dutyGeometryDiff.ts';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import IntlMessageFormat from 'intl-messageformat';

const LOCALES = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'locales');
const motors = (lng) => JSON.parse(readFileSync(join(LOCALES, lng, 'motors.json'), 'utf8'));
const say = (lng, key, vars) => new IntlMessageFormat(motors(lng)[key], lng).format(vars);

/* ── the owner's own example, rounded ────────────────────────────────────── */

test('the owner\'s own example: unrounded floats become clean mm values', () => {
  assert.equal(formatDutyGeometryValue('rotor_inner_radius', 4.800000000000002),
    '4.800 mm');
  assert.equal(formatDutyGeometryValue('rotor_inner_radius', 5.000000000000002),
    '5.000 mm');
});

/* ── counts: integers, no unit ───────────────────────────────────────────── */

test('count keys are plain integers', () => {
  for (const k of ['num_seg', 'num_slots_per_segment', 'num_poles_per_segment',
                   'num_wires_per_slot', 'wire_split', 'wire_parallel']) {
    assert.ok(isCountKey(k), k);
  }
  assert.equal(formatDutyGeometryValue('num_wires_per_slot', 6.0), '6');
  assert.equal(roundDutyGeometryValue('num_poles_per_segment', 4.9999999), 5);
});

test('any num_ prefixed key counts as a count key', () => {
  assert.ok(isCountKey('num_anything_future'));
});

/* ── physical dimensions: 0.001 mm ───────────────────────────────────────── */

test('mm dimensions round to 0.001 and keep three decimals', () => {
  assert.equal(roundDutyGeometryValue('tooth_width', 1.70000004), 1.7);
  assert.equal(formatDutyGeometryValue('tooth_width', 1.70000004), '1.700 mm');
  assert.equal(formatDutyGeometryValue('wire_width', 0.8), '0.800 mm');
  assert.equal(formatDutyGeometryValue('stator_diameter', 12), '12.000 mm');
});

test('an unrecognised key still gets the mm treatment (the common case today)', () => {
  assert.equal(formatDutyGeometryValue('rotor_fill_r', 1.3000049), '1.300 mm');
});

/* ── angles: 4 significant digits, degree sign ───────────────────────────── */

test('a _deg key uses 4 significant digits and the degree sign', () => {
  assert.equal(toSignificantDigits(30.123456, 4), 30.12);
  assert.equal(formatDutyGeometryValue('daxis_deg', 30.123456), '30.12°');
  assert.equal(formatDutyGeometryValue('daxis_deg', 5.0019999), '5.002°');
});

test('4 significant digits handles zero and small magnitudes', () => {
  assert.equal(toSignificantDigits(0, 4), 0);
  assert.equal(toSignificantDigits(0.00012345, 4), 0.0001235);
});

/* ── malformed values pass through rather than throwing ──────────────────── */

test('a non-finite value is not rounded, just stringified', () => {
  assert.equal(formatDutyGeometryValue('tooth_width', NaN), 'NaN');
  assert.equal(roundDutyGeometryValue('tooth_width', Infinity), Infinity);
});

/* ── the diff row text (the fallback / log rendering) ────────────────────── */

test('a die-scope row reads "key (scope): die V1 → duty V2"', () => {
  assert.equal(
    formatDutyGeometryDiffRow({ key: 'tooth_width', scope: 'die', live: 1.5, duty: 1.70000004 }),
    'tooth_width (die): die 1.500 mm → duty 1.700 mm');
});

test('a winding-scope count row carries no unit', () => {
  assert.equal(
    formatDutyGeometryDiffRow({ key: 'num_wires_per_slot', scope: 'winding', live: 5, duty: 6 }),
    'num_wires_per_slot (winding): die 5 → duty 6');
});

/* ── apply disabled reason: locked die, foreign lamination, or neither ──── */

test('an unlocked die with only winding/die diffs offers apply', () => {
  const diffs = [{ key: 'tooth_width', scope: 'die', live: 1.5, duty: 1.7 }];
  assert.equal(dutyGeometryApplyDisabledReason(diffs, false), null);
});

test('a locked die with a die-scope diff disables apply, with a reason', () => {
  const diffs = [{ key: 'tooth_width', scope: 'die', live: 1.5, duty: 1.7 }];
  const why = dutyGeometryApplyDisabledReason(diffs, true);
  assert.match(why, /locked/);
  assert.match(why, /unlock it/);
});

test('an identity (foreign-lamination) diff disables apply regardless of the lock', () => {
  const diffs = [{ key: 'stator_diameter', scope: 'identity', live: 12, duty: 30 }];
  const why = dutyGeometryApplyDisabledReason(diffs, false);
  assert.match(why, /lamination/);
  assert.match(why, /new die/);
});

test('dieLocked is trusted as given — the backend already scopes it to '
  + '"the lock would block THIS apply" (see family.py activate())', () => {
  // A winding-only diff never touches die.yaml, so the 409 payload's own
  // die_locked would be false for it (pinned server-side in
  // tests/test_duty_load_no_silent_die_write.py); the helper does not
  // second-guess that — it disables whenever the caller says dieLocked=true.
  const diffs = [{ key: 'num_wires_per_slot', scope: 'winding', live: 5, duty: 6 }];
  assert.equal(dutyGeometryApplyDisabledReason(diffs, false), null);
  assert.match(dutyGeometryApplyDisabledReason(diffs, true), /locked/);
});

/* ── the cancelled-load message: one string, everywhere ─────────────────── */

test('the cancelled message is fixed, not composed per-call', () => {
  assert.equal(dutyGeometryCancelledMessage(),
    'load cancelled — die geometry differs from the duty');
});

/* ── the choice buttons say exactly what happens, with the values ────────── */

const DIE_DIFFS = [
  { key: 'magnet_up_gap', scope: 'die', live: 0.2, duty: 0.1 },
  { key: 'rotor_hole', scope: 'die', live: 0.9000000000000001, duty: 1.0 },
];

test('the key list shows the die values on one side and the duty values on the other', () => {
  assert.equal(formatDutyGeometryKeyList(DIE_DIFFS, 'live'),
    'magnet_up_gap 0.200 mm, rotor_hole 0.900 mm');
  assert.equal(formatDutyGeometryKeyList(DIE_DIFFS, 'duty'),
    'magnet_up_gap 0.100 mm, rotor_hole 1.000 mm');
});

test('a die-key difference rewrites the die for all configurations; a winding-only one does not', () => {
  assert.equal(dutyGeometryRewritesDie(DIE_DIFFS), true);
  const winding = [{ key: 'wire_width', scope: 'winding', live: 0.8, duty: 2 }];
  assert.equal(dutyGeometryRewritesDie(winding), false);
  assert.equal(dutyGeometryButtonPlan(winding, 7).applyKey, 'dutyGeoApplyConfig');
  assert.equal(dutyGeometryButtonPlan(DIE_DIFFS, 7).applyKey, 'dutyGeoApplyDie');
  assert.equal(dutyGeometryButtonPlan(DIE_DIFFS, 7).configCount, 7);
  // an older server that does not send the count: wording without the number
  assert.equal(dutyGeometryButtonPlan(DIE_DIFFS, undefined).applyKey, 'dutyGeoApplyDieNoCount');
});

test('English button 1 names the die values and says nothing is written', () => {
  const plan = dutyGeometryButtonPlan(DIE_DIFFS, 7);
  assert.equal(say('en', 'dutyGeoKeepDie', { values: plan.keepValues }),
    'Use the current die: magnet_up_gap 0.200 mm, rotor_hole 0.900 mm — the saved results '
    + 'of this duty do not apply and will be recomputed when you run. Nothing is written.');
});

test('English button 2 names the duty values, ALL configurations and their count', () => {
  const plan = dutyGeometryButtonPlan(DIE_DIFFS, 7);
  assert.equal(say('en', plan.applyKey, { values: plan.applyValues, n: plan.configCount }),
    'Change the die back to the duty’s lamination: magnet_up_gap 0.100 mm, rotor_hole 1.000 mm '
    + '— this rewrites the die for ALL its configurations (7 configs) and their 3-D models');
  assert.match(say('en', 'dutyGeoApplyDie', { values: 'x 1', n: 1 }), /\(1 config\)/);
});

test('the winding-only apply names the configuration and says the die stays', () => {
  const winding = [{ key: 'wire_width', scope: 'winding', live: 0.8, duty: 2 }];
  const plan = dutyGeometryButtonPlan(winding, 7);
  const s = say('en', plan.applyKey, { values: plan.applyValues, config: 'L10' });
  assert.match(s, /configuration L10/);
  assert.match(s, /wire_width 2\.000 mm/);
  assert.match(s, /only this configuration/);
});

test('zh-CN carries the same buttons as a real translation (values and count intact)', () => {
  const plan = dutyGeometryButtonPlan(DIE_DIFFS, 7);
  const keep = say('zh-CN', 'dutyGeoKeepDie', { values: plan.keepValues });
  const apply = say('zh-CN', plan.applyKey, { values: plan.applyValues, n: plan.configCount });
  assert.match(keep, /magnet_up_gap 0\.200 mm, rotor_hole 0\.900 mm/);
  assert.match(apply, /magnet_up_gap 0\.100 mm, rotor_hole 1\.000 mm/);
  assert.match(apply, /7 /);
  for (const s of [keep, apply]) assert.match(s, /[一-鿿]/);
  // every new key exists in both locales and the Chinese is not the English
  const en = motors('en'); const zh = motors('zh-CN');
  const keys = Object.keys(en).filter((k) => k.startsWith('dutyGeo'));
  assert.ok(keys.length >= 20);
  for (const k of keys) {
    assert.ok(zh[k], `zh-CN is missing ${k}`);
    assert.notEqual(zh[k], en[k], `${k} is untranslated`);
  }
});

test('the apply-unavailable code mirrors the reason helper', () => {
  assert.equal(dutyGeometryApplyDisabledCode(
    [{ key: 'stator_diameter', scope: 'identity', live: 12, duty: 30 }], false), 'identity');
  assert.equal(dutyGeometryApplyDisabledCode(DIE_DIFFS, true), 'locked');
  assert.equal(dutyGeometryApplyDisabledCode(DIE_DIFFS, false), null);
});
