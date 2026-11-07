// node --test — the tooltip of the voltage / psi_PM / KV tiles names the magnet temperature the number is at
// (owner 2026-11-03: a rated duty at 173 C magnets reads 468 V, the bench comparison at 65 C reads 500 V, and nothing said which).
//   * with a temperature the tooltip gets ONE extra sentence, 0 decimals; the text under the value is not touched (tiles carry no sub-text);
//   * without a temperature the tooltip is exactly what it was;
//   * the voltages follow the point's EM source (loop temperature for a coupled overlay, the run's own for an EM run);
//   * psi_PM and the no-load KV follow the probe's own stamp, never another field;
//   * EN + zh-CN carry the same key with the same placeholder.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

try {
  const m = await import('node:module');
  if (typeof m.registerHooks === 'function') {
    m.registerHooks({
      resolve(specifier, context, nextResolve) {
        try { return nextResolve(specifier, context); }
        catch (err) {
          if (/^\.{1,2}\//.test(specifier) && !/\.[cm]?[jt]sx?$/.test(specifier)) return nextResolve(`${specifier}.ts`, context);
          throw err;
        }
      },
    });
  }
} catch { /* no hooks: the import below skips */ }

let E = null;
try { E = await import('../emResultTiles.ts'); } catch { /* old Node */ }
const t = E ? test : test.skip;

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const loc = (l) => JSON.parse(readFileSync(join(SRC, `locales/${l}/controller.json`), 'utf8'));
const lookup = (l) => {
  const j = loc(l);
  return (key, vars = {}) => {
    const v = key.split('.').reduce((o, k) => (o == null ? o : o[k]), j);
    assert.equal(typeof v, 'string', `${l}: ${key}`);
    return v.replace(/\{(\w+)\}/g, (_, n) => String(vars[n]));
  };
};

const one = (rows, id) => rows.flat().find((x) => x.id === id);
const VOLT = ['vLinePeak', 'vPhasePeak', 'vLineRms', 'vPhaseRms'];
const RUN = {
  V_phase_peak_V: 270, V_phase_rms_V: 191, V_line_peak_V: 468, V_line_rms_V: 331, star_delta: 'star',
  psi_pm_Wb: 0.0123, KV_noload_rpm_per_V_line: 6.4,
};

t('a voltage tile of a run solved at 173 C names it, once, with no decimals', () => {
  const s = { ...RUN, magnet_temp_used_c: 172.6 };
  const rows = E.emTileRows(s);
  for (const id of VOLT) {
    const tile = one(rows, id);
    assert.deepEqual(tile.tipExtraKeys, ['emTiles.atMagnetTemp'], id);
    assert.equal(E.tileTooltip(tile, lookup('en')).endsWith(' At magnet temperature 173 °C.'), true, id);
    assert.equal(E.tileTooltip(tile, lookup('zh-CN')).endsWith('磁钢温度 173 °C 时。'), true, id);
  }
});

t('the exact tooltip text, EN and zh-CN', () => {
  const tile = one(E.emTileRows({ ...RUN, magnet_temp_used_c: 173 }), 'vLinePeak');
  assert.equal(E.tileTooltip(tile, lookup('en')), "Peak line-to-line voltage of this run's waveforms. At magnet temperature 173 °C.");
  assert.equal(E.tileTooltip(tile, lookup('zh-CN')), '本次求解波形的线电压峰值。 磁钢温度 173 °C 时。');
});

t('without a magnet temperature the tooltip is unchanged', () => {
  const rows = E.emTileRows(RUN);
  for (const id of [...VOLT, 'psiPm', 'kv']) {
    const tile = one(rows, id);
    assert.equal(tile.tipExtraKeys, undefined, id);
    assert.equal(tile.tipVars, undefined, id);
    assert.equal(E.tileTooltip(tile, lookup('en')).includes('magnet temperature'), false, id);
  }
  assert.deepEqual(E.magnetTempTip(null), {});
  assert.deepEqual(E.magnetTempTip(65), { tipExtraKeys: ['emTiles.atMagnetTemp'], tipVars: { magT: '65' } });
});

t('the voltages follow the EM source of the point', () => {
  const s = { ...RUN, magnet_temp_used_c: 65, coupling: { magnet_temp_c: 173 } };
  const temp = (src) => one(E.emTileRows(s, { emSource: src }), 'vLinePeak').tipVars?.magT;
  assert.equal(temp('coupled'), '173');   // the loop's numbers replaced the saved run's
  assert.equal(temp('em_run'), '65');     // the EM run's own temperature, not the thermal map's
  assert.equal(temp(undefined), '173');   // a plain run summary: the same as its magnet-temperature tile (loop first)
  // an EM run with only a thermal map beside it and no stamp of its own: unknown, so nothing is said
  const noStamp = { ...RUN, coupling: { magnet_temp_c: 130 } };
  assert.equal(one(E.emTileRows(noStamp, { emSource: 'em_run' }), 'vLinePeak').tipExtraKeys, undefined);
});

t('psi_PM and the no-load KV carry the probe stamp, not the run temperature', () => {
  const s = { ...RUN, magnet_temp_used_c: 173, psi_pm_magnet_temp_c: 120 };
  const rows = E.emTileRows(s);
  assert.equal(one(rows, 'psiPm').tipVars.magT, '120');
  assert.equal(one(rows, 'kv').tipVars.magT, '120');
  assert.equal(one(rows, 'vLinePeak').tipVars.magT, '173');
  // no probe stamp: psi_PM says nothing; the loaded KV (no no-load figure) is a terminal-voltage number at the run's temperature
  const bare = E.emTileRows({ ...RUN, magnet_temp_used_c: 173 });
  assert.equal(one(bare, 'psiPm').tipExtraKeys, undefined);
  const loaded = E.emTileRows({ V_line_peak_V: 40, KV_rpm_per_V_line: 900, magnet_temp_used_c: 90 });
  assert.equal(one(loaded, 'kv').tipVars.magT, '90');
});

t('EN and zh-CN carry the key with the same placeholder', () => {
  const en = loc('en').emTiles.atMagnetTemp, zh = loc('zh-CN').emTiles.atMagnetTemp;
  assert.match(en, /\{magT\}/);
  assert.match(zh, /\{magT\}/);
});
