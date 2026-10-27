// Owner 2026-10-27 (CIANO56 400_80 vadim 3 / L110-2x3ph / cruise): a within-limits limit run is a steady state, the maps are framed
// to the solved part, and an empty map tab says why.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { temperatureTiles } from '../temperatureTiles.ts';
import { limitKind, otherPointFields, storedFieldKinds, thermalTiles } from '../pointsLimited.ts';
import { drawnExtent, isCropped, mapExtent } from '../storedFieldPayload.ts';

const loc = (lang) => JSON.parse(readFileSync(new URL(`../../../locales/${lang}/development.json`, import.meta.url), 'utf8'));
const prov = { computed_at: '2026-10-27T03:15:58' };
const by = (tiles) => Object.fromEntries(tiles.map((t) => [t.id, t]));

// what the server now sends for the cruise record: the within-limits run as the duty's steady state
const steadyDuty = {
  duty: 'cruise',
  sources: { em: { kind: 'em_run', coil_temp_c: 150 } },
  thermal: {
    duty_temps: { kind: 'coupled', from_limit_run: true, winding_c: 137.3, magnet_c: 82.4, winding_max_c: 141.4, magnet_max_c: 84.1, provenance: prov },
    limit_search: { kind: 'coupled_limit', solve_to: 'limits', mode: 'steady', reached_limit: false, winding_c: 137.3, magnet_c: 82.4, provenance: prov },
    s2: { within_limits: true },
  },
};

test('a within-limits limit run gives both temperature tiles, not the muted 150 C EM input', () => {
  const tiles = temperatureTiles(steadyDuty);
  const t = by(tiles);
  assert.deepEqual(tiles.map((x) => x.id), ['magnetTemp', 'windingTemp']);
  assert.equal(t.windingTemp.value, 137.3);
  assert.equal(t.magnetTemp.value, 82.4);
  assert.ok(tiles.every((x) => !x.muted && x.basis === 'coupled'));
});

test('the hottest winding and magnet go to the tooltip and the tooltip calls it a steady state', () => {
  const t = by(temperatureTiles(steadyDuty));
  assert.equal(t.windingTemp.tip[0].key, 'points.tempTile.steady.winding');
  assert.equal(t.magnetTemp.tip[0].key, 'points.tempTile.steady.magnet');
  assert.deepEqual(t.windingTemp.tip[1], { key: 'points.tempTile.hottestWinding', args: { value: 141 } });
  assert.deepEqual(t.magnetTemp.tip[1], { key: 'points.tempTile.hottestMagnet', args: { value: 84 } });
});

test('a plain coupled loop still says coupled, and shows no hottest line when it states none', () => {
  const t = by(temperatureTiles({ duty: 'x', thermal: { duty_temps: { kind: 'coupled', winding_c: 128.5, magnet_c: 72, provenance: prov } } }));
  assert.equal(t.windingTemp.tip[0].key, 'points.tempTile.coupled.winding');
  assert.equal(t.windingTemp.tip.length, 1);
});

test('only a run that reached a limit is a limit state; the thermal tiles of the block show the steady one', () => {
  assert.equal(limitKind(steadyDuty.thermal.limit_search), 'steady');
  assert.equal(limitKind({ kind: 'coupled_limit', solve_to: 'limits', mode: 'limited', reached_limit: true }), 'time');
  assert.equal(limitKind({ kind: 'coupled_limit', solve_to: 'limits' }), 'time');       // an older payload without the flag keeps its meaning
  assert.equal(limitKind({ kind: 'coupled_limit', solve_to: 'continuous', reached_limit: true }), 'continuous');
  const tiles = by(thermalTiles(steadyDuty.thermal));
  assert.equal(tiles.winding.value, 137.3);
  assert.equal(tiles.magnet.value, 84.1);
});

test('the new captions are locale strings in EN and zh-CN; the time-to-limit caption stays for a real limit', () => {
  for (const lang of ['en', 'zh-CN']) {
    const l = loc(lang);
    assert.ok(l.points.thermal.steadyLine.includes('{day}'), lang);
    assert.ok(l.points.thermal.steadyTip.length > 5, lang);
    assert.ok(l.points.tempTile.steady.winding.includes('{day}') && l.points.tempTile.steady.magnet.includes('{day}'), lang);
    assert.ok(l.points.tempTile.hottestWinding.includes('{value}'), lang);
    assert.ok(l.points.fields.otherPoint.includes('{field}') && l.points.fields.otherPoint.includes('{point}'), lang);
    assert.ok(l.points.fields.whole && l.points.fields.wholeTip, lang);
  }
  assert.match(loc('en').points.thermal.steadyLine, /steady \(inside every limit\)/);
  assert.match(loc('en').points.thermal.timeLine, /first limit is reached/);
});

// -- the map crop -------------------------------------------------------------------------------------------------------
// two drawn triangles in a corner of a 200 mm wide motor, a third triangle far away
const pos = Float32Array.from([
  10, 10, 0, 20, 10, 0, 10, 20, 0,
  20, 10, 0, 20, 20, 0, 10, 20, 0,
  -90, -90, 0, -80, -90, 0, -90, -80, 0,
]);

test('drawnExtent: the box of the triangles that carry a value, with a small margin', () => {
  const vals = Float32Array.from([1, 2, 3, 2, 4, 3, NaN, NaN, NaN]);
  const e = drawnExtent(pos, vals);
  assert.ok(e);
  assert.ok(e[0] < 10 && e[0] > 9 && e[1] > 20 && e[1] < 21);
  assert.ok(e[2] < 10 && e[2] > 9 && e[3] > 20 && e[3] < 21);
  assert.ok(Math.abs((10 - e[0]) - 0.4) < 1e-6);               // margin = 4 % of the larger span (10 mm)
  const all = drawnExtent(pos);                                 // without values every drawn triangle counts
  assert.ok(all[0] < -90 && all[1] > 20);
});

test('drawnExtent: nothing drawn gives null; a triangle with a missing vertex value is dropped', () => {
  assert.equal(drawnExtent(null), null);
  assert.equal(drawnExtent(new Float32Array(0)), null);
  assert.equal(drawnExtent(pos, new Float32Array(9).fill(NaN)), null);
  const one = drawnExtent(pos, Float32Array.from([1, 1, NaN, 1, 1, 1, NaN, 1, 1]));
  assert.ok(one[1] < 21 && one[0] > 9);                         // the second triangle alone: x 10..20
});

test('isCropped / mapExtent: the solved part by default, the whole motor on request or when there is nothing to crop', () => {
  const whole = [-100, 100, -100, 100];
  const crop = [9, 21, 9, 21];
  assert.equal(isCropped(whole, crop), true);
  assert.equal(isCropped(whole, [-99, 99, -99, 99]), false);
  assert.equal(isCropped(null, crop), false);
  assert.deepEqual(mapExtent(whole, crop, false), crop);
  assert.deepEqual(mapExtent(whole, crop, true), whole);
  assert.deepEqual(mapExtent(whole, null, false), whole);
  assert.equal(mapExtent(null, null, false), null);
});

// -- an empty map tab says why ------------------------------------------------------------------------------------------
test('otherPointFields: the speeds of a map left out as another point; unstamped maps are offered', () => {
  const d = {
    duty: 'cruise', operating_point: { rpm: 850 },
    fields: { thermal: { point: { rpm: 850, T_min: 51.8, T_max: 141.4 } }, em: { unstamped: true, point: {} } },
    hidden_records: [
      { kind: 'field:em', reason: 'other_point', field_rpm: 1000.4, point_rpm: 850 },
      { kind: 'field:modes', reason: 'other_point' },
      { kind: 'em_run', reason: 'other_point' },
    ],
  };
  assert.deepEqual(otherPointFields(d), { em: { field: 1000, point: 850 } });
  assert.deepEqual(otherPointFields(null), {});
  assert.deepEqual(storedFieldKinds(d), ['em', 'thermal']);     // an unstamped map has no speed to contradict the point's
});
