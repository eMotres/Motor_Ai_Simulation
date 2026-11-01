// The cogging-torque tile (owner 2026-10-26): headline pk-pk, half-amplitude / % of continuous / magnets / method in the tooltip.
import test from 'node:test';
import assert from 'node:assert/strict';
import { coggingDigits, coggingValue, coggingTipParts, coggingFromFlat, coggingTipText } from '../coggingTile.ts';

// the tile modules import each other the way Vite does (no '.ts'); resolve them for the tests only
const hooks = await import('node:module');
if (typeof hooks.registerHooks === 'function') {
  hooks.registerHooks({
    resolve(specifier, context, nextResolve) {
      try { return nextResolve(specifier, context); } catch (err) {
        if (/^\.{1,2}\//.test(specifier) && !/\.[cm]?[jt]sx?$/.test(specifier)) return nextResolve(`${specifier}.ts`, context);
        throw err;
      }
    },
  });
}
const E = await import('../emResultTiles.ts');

test('digits follow the size of the value', () => {
  assert.equal(coggingDigits(0.0637), 4);
  assert.equal(coggingDigits(2.5), 3);
  assert.equal(coggingDigits(45), 2);
});

test('nothing is drawn without a finite, non-zero value', () => {
  assert.equal(coggingValue(null), null);
  assert.equal(coggingValue({ pkpk_Nm: 0 }), null);
  assert.equal(coggingValue({ pkpk_Nm: Number.NaN }), null);
  assert.equal(coggingValue({ pkpk_Nm: 0.01 }), 0.01);
  assert.deepEqual(coggingTipParts({ pkpk_Nm: 0 }), []);
});

test('tooltip parts: half-amplitude always, % / magnet temperature only when known, staleness only when stale', () => {
  const keys = (v) => coggingTipParts(v).map((p) => p.key);
  assert.deepEqual(keys({ pkpk_Nm: 0.1 }), ['configure.coggingTipHalf', 'configure.coggingTipMethod']);
  assert.deepEqual(keys({ pkpk_Nm: 0.1, pct_of_continuous: 1.5, magnet_temp_c: 120, stale: true }),
    ['configure.coggingTipHalf', 'configure.coggingTipPct', 'configure.coggingTipTemp', 'configure.coggingTipMethod', 'configure.coggingTipStale']);
  const half = coggingTipParts({ pkpk_Nm: 0.0637 })[0];
  assert.equal(half.args.half, '0.0319');                         // half of pk-pk when the server sent none
  const t = coggingTipText({ pkpk_Nm: 0.0637, half_amp_Nm: 0.03182, pct_of_continuous: 0.31, magnet_temp_c: 120 }, (k, a) => `${k}${a ? JSON.stringify(a) : ''}`);
  assert.match(t, /^configure\.coggingTip /);
  assert.match(t, /"half":"0.0318"/);
  assert.match(t, /"pct":"0.31"/);
  assert.match(t, /"temp":"120"/);
});

test('flat comparison keys map back to a view', () => {
  assert.equal(coggingFromFlat({}), null);
  assert.equal(coggingFromFlat({ cogging_pkpk_nm: 'x' }), null);
  const v = coggingFromFlat({ cogging_pkpk_nm: 0.2, cogging_half_nm: 0.1, cogging_pct_of_continuous: 2, cogging_stale: true });
  assert.deepEqual([v.pkpk_Nm, v.half_amp_Nm, v.pct_of_continuous, v.stale], [0.2, 0.1, 2, true]);
});

test('the information panel draws one cogging tile after ripple, only when a value is stored (no zero / empty tile)', () => {
  const tiles = (cogging) => E.emTileRows({ T_em_avg_Nm: 5, T_ripple_pct: 3, P_mech_W: 500, P_loss_total_W: 20 }, { cogging: coggingFromFlat(cogging) })[0].map((c) => c.id);
  assert.deepEqual(tiles({}), ['torque', 'power', 'elecPower', 'efficiency', 'tRipple']);
  assert.deepEqual(tiles({ cogging_pkpk_nm: 0.0637 }), ['torque', 'power', 'elecPower', 'efficiency', 'tRipple', 'cogging']);
  assert.ok(!tiles({ cogging_pkpk_nm: 0 }).includes('cogging'));
  const cell = E.emTileRows({ T_em_avg_Nm: 5 }, { cogging: coggingFromFlat({ cogging_pkpk_nm: 0.0637, cogging_half_nm: 0.0319, cogging_pct_of_continuous: 0.4, cogging_magnet_temp_c: 120 }) })[0].find((c) => c.id === 'cogging');
  assert.equal(cell.labelKey, 'configure.cogging');
  assert.equal(cell.tipKey, 'configure.coggingTip');
  assert.equal(cell.unit, 'N·m');
  assert.equal(cell.d, 4);
  assert.deepEqual(cell.tipParts.map((p) => p.key), ['configure.coggingTipHalf', 'configure.coggingTipPct', 'configure.coggingTipTemp', 'configure.coggingTipMethod']);
});

test('the percentage names its base: the continuous rating, or the rated duty (with its source); overrides are named', () => {
  const keys = (v) => coggingTipParts(v).map((p) => p.key);
  assert.ok(keys({ pkpk_Nm: 0.1, pct_of_continuous: 1 }).includes('configure.coggingTipPct'));
  const rated = coggingTipParts({ pkpk_Nm: 0.1, pct_of_rated_duty: 2.5, continuous_torque_source: "solved torque of duty 'rated'" });
  const p = rated.find((x) => x.key === 'configure.coggingTipPctRated');
  assert.deepEqual(p.args, { pct: '2.50', source: "solved torque of duty 'rated'" });
  assert.ok(!rated.some((x) => x.key === 'configure.coggingTipPct'));
  const over = coggingTipParts({ pkpk_Nm: 0.1, overrides: ['frozen permeability', 'mesh 2 mm'] }).find((x) => x.key === 'configure.coggingTipOverride');
  assert.equal(over.args.what, 'frozen permeability, mesh 2 mm');
  const flat = coggingFromFlat({ cogging_pkpk_nm: 0.1, cogging_pct_of_rated_duty: 3, cogging_torque_source: 'x', cogging_overrides: 'a; b' });
  assert.deepEqual([flat.pct_of_rated_duty, flat.continuous_torque_source, flat.overrides], [3, 'x', ['a', 'b']]);
});
