// MAGNET TEMP / WINDING TEMP tiles of the points-limited Configure (owner 2026-10-27).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { temperatureRowIndex, temperatureTiles } from '../temperatureTiles.ts';

const prov = { computed_at: '2026-10-26T09:30:00' };
const by = (tiles) => Object.fromEntries(tiles.map((t) => [t.id, t]));
const keys = (tile) => tile.tip.map((p) => p.key);

test('a coupled record gives the mean temperatures, the hottest magnet in the tooltip, not muted', () => {
  const tiles = temperatureTiles({
    duty: 'cruise',
    sources: { em: { kind: 'em_run', coil_temp_c: 150, magnet_temp_c: 80 } },
    thermal: { duty_temps: { kind: 'coupled', winding_c: 128.5, magnet_c: 72, magnet_max_c: 79.4, provenance: prov } },
  });
  const t = by(tiles);
  assert.deepEqual(tiles.map((x) => x.id), ['magnetTemp', 'windingTemp']);
  assert.equal(t.magnetTemp.value, 72);                                      // the mean, not the EM input and not the hottest
  assert.equal(t.windingTemp.value, 128.5);
  assert.equal(t.magnetTemp.basis, 'coupled');
  assert.equal(t.magnetTemp.muted, false);
  assert.ok(keys(t.magnetTemp).includes('points.tempTile.hottestMagnet'));
  assert.equal(t.magnetTemp.tip.find((p) => p.key === 'points.tempTile.hottestMagnet').args.value, 79);
  assert.ok(!keys(t.windingTemp).includes('points.tempTile.hottestMagnet'));
  assert.equal(t.windingTemp.tip[0].args.day, '2026-10-26 09:30');
});

test('the tooltip names the basis: time to the limit, S1 rating and the cooling air speed when stored', () => {
  const tiles = temperatureTiles({
    duty: 'peak',
    thermal: {
      duty_temps: { kind: 'coupled', winding_c: 180, magnet_c: 90, provenance: prov },
      s2: { time_to_limit_s: 46.6, limiting_part: 'winding' },
      s1: { I_cont_A_rms: 30.04, limiting_part: 'magnet' },
      cooling_air_speed_mps: 24.6,
    },
  });
  const tip = by(tiles).windingTemp.tip;
  assert.deepEqual(tip.map((p) => p.key), ['points.tempTile.coupled.winding', 'points.tempTile.timeToLimit', 'points.tempTile.s1', 'points.tempTile.air']);
  assert.deepEqual(tip[1].args, { time: 47, part: 'winding' });
  assert.deepEqual(tip[2].args, { current: 30, part: 'magnet' });
  assert.deepEqual(tip[3].args, { speed: 24.6 });
});

test('without a coupled record the EM run input is shown muted and named an input, not a result', () => {
  const tiles = temperatureTiles({ duty: 'cruise', sources: { em: { kind: 'em_run', coil_temp_c: 150, magnet_temp_c: 80 } } });
  const t = by(tiles);
  assert.equal(t.magnetTemp.value, 80);
  assert.equal(t.windingTemp.value, 150);
  assert.ok(tiles.every((x) => x.muted && x.basis === 'em_input'));
  assert.deepEqual(keys(t.magnetTemp), ['points.tempTile.emInput.magnet']);
  // the locale text says "EM input (card / setpoint), not a computed result"
  const en = JSON.parse(readFileSync(new URL('../../../locales/en/development.json', import.meta.url), 'utf8'));
  assert.match(en.points.tempTile.emInput.magnet, /EM input \(card \/ setpoint\), not a computed result/);
});

test('only the missing half falls back: coupled winding with no stored magnet shows the EM magnet input muted', () => {
  const t = by(temperatureTiles({
    duty: 'x', sources: { em: { kind: 'em_run', magnet_temp_c: 65 } },
    thermal: { duty_temps: { kind: 'coupled', winding_c: 110, provenance: prov } },
  }));
  assert.equal(t.windingTemp.basis, 'coupled');
  assert.equal(t.magnetTemp.basis, 'em_input');
  assert.equal(t.magnetTemp.muted, true);
});

test('a steady map with no coupled loop is its own basis, not muted; a limit search is never the duty temperature', () => {
  const t = by(temperatureTiles({ duty: 'x', thermal: { duty_temps: { kind: 'thermal', winding_c: 120, magnet_c: 75, provenance: prov } } }));
  assert.equal(t.windingTemp.basis, 'thermal');
  assert.equal(t.windingTemp.muted, false);
  const none = temperatureTiles({ duty: 'x', thermal: { limit_search: { kind: 'coupled_limit', winding_c: 179.6, magnet_c: 90 } } });
  assert.deepEqual(none, []);
});

test('nothing stored, or an impossible temperature, draws nothing; a coupled-only point has no EM input to show', () => {
  assert.deepEqual(temperatureTiles(null), []);
  assert.deepEqual(temperatureTiles({ duty: 'x' }), []);
  assert.deepEqual(temperatureTiles({ duty: 'x', sources: { em: { kind: 'em_run', coil_temp_c: -300, magnet_temp_c: Number.NaN } } }), []);
  assert.deepEqual(temperatureTiles({ duty: 'x', sources: { em: { kind: 'coupled', coil_temp_c: 150 } } }), []);
});

test('the temperatures row sits before the electrical row, or last', () => {
  assert.equal(temperatureRowIndex([[{ id: 'torque' }], [{ id: 'losses' }], [{ id: 'dcBus' }]]), 2);
  assert.equal(temperatureRowIndex([[{ id: 'torque' }], [{ id: 'losses' }]]), 2);
  assert.equal(temperatureRowIndex([]), 0);
});

test('wiring: PointsConfigure draws the row from the module; the locale keys exist in EN and ZH', () => {
  const src = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
  const panel = src('../../../components/compare/PointsConfigure.tsx');
  assert.match(panel, /temperatureTiles\(current\)/);
  assert.match(panel, /temperatureRowIndex\(grid\)/);
  assert.match(panel, /data-testid=\{`temp-tile-\$\{tile\.id\}`\}/);
  const tipKeys = (o) => Object.keys(o.points.tempTile).sort().join(',');
  const en = JSON.parse(src('../../../locales/en/development.json'));
  const zh = JSON.parse(src('../../../locales/zh-CN/development.json'));
  assert.equal(tipKeys(en), tipKeys(zh));
  for (const key of ['magnet', 'winding', 'coupled', 'thermal', 'emInput', 'hottestMagnet', 'timeToLimit', 's1', 'air']) assert.ok(zh.points.tempTile[key]);
  assert.match(zh.points.tempTile.emInput.magnet, /[\u4e00-\u9fff]/);
});
