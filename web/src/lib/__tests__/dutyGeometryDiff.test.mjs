/**
 * node --test — lib/dutyGeometryDiff.ts: the rounding/formatting behind the
 * summary card's "⚠ older build" chip (components/simulation/SummaryTable.tsx),
 * which names what moved between a result's `_geoSig` stamp and the live
 * geometry.
 *
 * The module is dependency-free (no `import.meta.env`), so node imports the
 * SHIPPED file — same convention as lib/timeToLimitChip.ts.
 *
 * What this pins:
 *
 *   1. a count key (segments/slots/poles/wires/strands) rounds to a plain
 *      integer, no unit (incident 2026-09-29: unrounded floats);
 *   2. a physical dimension rounds to 0.001 mm and carries the "mm" unit;
 *   3. an angle key rounds to 4 significant digits and carries "°";
 *   4. a `_geoSig` stamp parses, and only keys that really moved are listed;
 *   5. the blocking "saved on a different geometry" dialog is gone for good
 *      (owner 2026-10-29): no module, no locale key, no import of it.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  isCountKey, toSignificantDigits, roundDutyGeometryValue, formatDutyGeometryValue,
  formatDutyGeometryDiffRow, formatDutyGeometryKeyList, parseGeoSig, geoSigDiffRows,
  staleBuildKeysText,
} from '../dutyGeometryDiff.ts';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const LOCALES = join(SRC, 'locales');
const json = (lng, ns) => JSON.parse(readFileSync(join(LOCALES, lng, `${ns}.json`), 'utf8'));

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

/* ── the key list (one side of a diff) ─────────────────────────────────── */

const DIE_DIFFS = [
  { key: 'magnet_up_gap', scope: 'die', live: 0.2, duty: 0.1 },
  { key: 'rotor_hole', scope: 'die', live: 0.9000000000000001, duty: 1.0 },
];

test('the key list shows the live values on one side and the saved values on the other', () => {
  assert.equal(formatDutyGeometryKeyList(DIE_DIFFS, 'live'),
    'magnet_up_gap 0.200 mm, rotor_hole 0.900 mm');
  assert.equal(formatDutyGeometryKeyList(DIE_DIFFS, 'duty'),
    'magnet_up_gap 0.100 mm, rotor_hole 1.000 mm');
});

/* ── a result's stamp against the live build ─────────────────────────────── */

// The owner's 2026-10-29 case: the configuration moved, the duty's EM
// summary still carries the stamp of the build it was solved on.
const STAMP = 'insulation_thickness:0.2|motor_length:100|num_seg:2|'
  + 'wire_spacing_x:0.1|wire_spacing_y:0.13';
const LIVE = { insulation_thickness: 0.25, motor_length: 100, num_seg: 2,
               wire_spacing_x: 0.12, wire_spacing_y: 0.1, label: 'L100' };

test('a _geoSig stamp parses into numbers; junk pairs are skipped', () => {
  assert.deepEqual(parseGeoSig('a:1|b:0.25|c:x|:3|d'), { a: 1, b: 0.25 });
  assert.deepEqual(parseGeoSig(''), {});
  assert.deepEqual(parseGeoSig(null), {});
});

test('only the keys that moved are listed, saved → now', () => {
  const rows = geoSigDiffRows(STAMP, LIVE);
  assert.deepEqual(rows.map((r) => r.key),
    ['insulation_thickness', 'wire_spacing_x', 'wire_spacing_y']);
  assert.equal(staleBuildKeysText(rows),
    'insulation_thickness 0.200 → 0.250 mm, wire_spacing_x 0.100 → 0.120 mm, '
    + 'wire_spacing_y 0.130 → 0.100 mm');
});

test('the same build gives no rows, float noise included', () => {
  assert.deepEqual(geoSigDiffRows(STAMP, { ...LIVE, insulation_thickness: 0.2,
    wire_spacing_x: 0.1 + 1e-12, wire_spacing_y: 0.13 }), []);
  assert.deepEqual(geoSigDiffRows('', LIVE), []);
});

test('a long diff is cut to `max` keys with a count of the rest', () => {
  const rows = geoSigDiffRows(STAMP, LIVE);
  assert.equal(staleBuildKeysText(rows, 1),
    'insulation_thickness 0.200 → 0.250 mm (+2 more)');
  assert.equal(staleBuildKeysText([{ key: 'num_wires_per_slot', scope: '', live: 6, duty: 5 }]),
    'num_wires_per_slot 5 → 6');
});

/* ── the chip's strings, and the dialog that must not come back ─────────── */

test('the stale chip strings exist in EN and a real zh-CN translation', () => {
  const en = json('en', 'results'); const zh = json('zh-CN', 'results');
  for (const k of ['staleBuild', 'staleBuildTip', 'recompute']) {
    assert.ok(en[k] && zh[k], k);
    assert.notEqual(en[k], zh[k], `${k} is untranslated`);
  }
  assert.match(en.staleBuildTip, /\{\{keys\}\}/);
  assert.match(zh.staleBuildTip, /\{\{keys\}\}/);
});

test('the blocking "different geometry" dialog is gone (owner 2026-10-29)', () => {
  assert.equal(existsSync(join(SRC, 'components', 'catalog', 'DutyGeometryDialog.tsx')), false);
  assert.equal(existsSync(join(SRC, 'lib', 'dutyGeometryDialogService.ts')), false);
  for (const lng of ['en', 'zh-CN']) {
    assert.deepEqual(Object.keys(json(lng, 'motors')).filter((k) => k.startsWith('dutyGeo')), []);
  }
  const walk = (dir) => readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    if (statSync(p).isDirectory()) return n === '__tests__' ? [] : walk(p);
    return /\.tsx?$/.test(n) ? [p] : [];
  });
  for (const f of walk(SRC)) {
    assert.doesNotMatch(readFileSync(f, 'utf8'), /DutyGeometryDialog|askDutyGeometryChoice/, f);
  }
  // the load never asks: it opens on the current build, keep_die always
  const apply = readFileSync(join(SRC, 'lib', 'dutyApply.ts'), 'utf8');
  assert.match(apply, /geometry_choice: 'keep_die'/);
  assert.doesNotMatch(apply, /apply_duty/);
});
