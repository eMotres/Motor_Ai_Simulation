// node --test - the magnet temperature of a Sweep / Descent / Auto run: sent only when typed, shown in the one line with its source,
// read back from stored records (owner 2026-11-24: real magnet temperature everywhere)
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { tabRequest, sameRequest, answerKey, runSettingsBody, baselineBody, lineText, magnetOfRecord } from '../runSettings.ts';
import { readCurrentSettings } from '../currentSettings.ts';

const tab = { steps: 48, gap_layers: 1, mesh_size_mm: 1.8, min_size_mm: 0.2, n_sectors: 2, demag: true, rotor_eddy: true, component_mesh: { coil_rel: 2 } };
const store = (m) => ({ getItem: (k) => (k in m ? m[k] : null) });
const shown = { sig: 7, token: 't' };

test('the request carries the magnet temperature only when the field holds a number', () => {
  assert.equal('magnet_temp_c' in tabRequest(tab, 'sweep').request, false);
  assert.equal('magnet_temp_c' in tabRequest({ ...tab, magnet_temp_c: undefined }, 'sweep').request, false);
  assert.equal('magnet_temp_c' in tabRequest({ ...tab, magnet_temp_c: NaN }, 'sweep').request, false);
  assert.equal(tabRequest({ ...tab, magnet_temp_c: 60 }, 'sweep').request.magnet_temp_c, 60);
  assert.equal(tabRequest({ ...tab, magnet_temp_c: 0 }, 'auto').request.magnet_temp_c, 0);       // 0 C is a number, not blank
  assert.equal(tabRequest({ ...tab, magnet_temp_c: 60 }, 'descent').request.magnet_temp_c, 60);
});

test('every run body and the baseline carry it only when typed; the held answer is bound to it', () => {
  const blank = tabRequest(tab, 'sweep').request, typed = tabRequest({ ...tab, magnet_temp_c: 60 }, 'sweep').request;
  assert.equal('magnet_temp_c' in runSettingsBody(blank, shown), false);
  assert.equal(runSettingsBody(typed, shown).magnet_temp_c, 60);
  assert.equal(baselineBody({ operating_point: {}, current_bump_pct: 5 }, typed, { ...shown, settings: {} }).run_settings.magnet_temp_c, 60);
  assert.equal(sameRequest(blank, typed), false);
  assert.equal(sameRequest(typed, tabRequest({ ...tab, magnet_temp_c: 60 }, 'sweep').request), true);
  assert.notEqual(answerKey('c', 'sweep', blank), answerKey('c', 'sweep', typed));
  assert.equal(answerKey('c', 'sweep', blank), answerKey('c', 'sweep', tabRequest(tab, 'sweep').request));   // blank: the key it always had
});

test('the Simulation tab field is read as text: a number, a decimal comma, else absent', () => {
  assert.equal(readCurrentSettings(store({ 'sim.magnetTempC': '"60"' })).magnet_temp_c, 60);
  assert.equal(readCurrentSettings(store({ 'sim.magnetTempC': '"90,5"' })).magnet_temp_c, 90.5);
  assert.equal('magnet_temp_c' in readCurrentSettings(store({ 'sim.magnetTempC': '""' })), false);
  assert.equal('magnet_temp_c' in readCurrentSettings(store({ 'sim.magnetTempC': '"abc"' })), false);
  assert.equal('magnet_temp_c' in readCurrentSettings(store({})), false);
});

const words = {
  preset: (l, v) => `${l} v${v}`, steps: n => `${n} steps`, gap: n => `${n} gap`, wire: n => `wire ${n}h`, fromSim: 'Simulation', fromMesh: 'Mesh',
  notOptimization: 'not Optimization', diff: (k) => k,
  magnet: (t, s, card) => (s === 'typed' ? `magnets ${t} C (typed)` : `magnets ${t} C (card ${card})`),
};
const ans = (magnet) => ({ preset: 'optimization', label: 'optimization', custom: false, preset_label: 'Optimization', differs: {}, version: 2,
  settings: { steps_per_period: 48, gap_layers: 1, coil_rel: 2 }, sources: {}, clamps: [], sig: 1, mode: 'sweep', overrides: {}, ...(magnet ? { magnet } : {}) });

test('the line names the magnet temperature with its source once; an answer without it reads as before', () => {
  assert.match(lineText(ans({ requested: null, used_c: 120, source: 'card', card: 'F52SH_120C', card_c: 120 }), words), /magnets 120 C \(card F52SH_120C\)$/);
  assert.match(lineText(ans({ requested: 60, used_c: 60, source: 'typed', card: 'F52SH_120C', card_c: 120 }), words), /magnets 60 C \(typed\)$/);
  assert.match(lineText(ans({ requested: 0, used_c: null, source: 'typed', card: 'C', card_c: 120 }), words), /magnets 0 C \(typed\)$/);   // 0 C typed is 0 C
  assert.doesNotMatch(lineText(ans({ requested: null, used_c: null, source: 'typed', card: 'C', card_c: 120 }), words), /magnets/);   // no number: no "0 C"
  assert.equal(lineText(ans(null), words), 'Optimization v2 · 48 steps (Simulation), 1 gap (Mesh), wire 2h (Mesh)');
  const { magnet, ...w2 } = words;   // a caller without the word is unchanged too
  assert.equal(lineText(ans({ requested: 60, used_c: 60, source: 'typed', card: null, card_c: null }), w2), lineText(ans(null), w2));
});

test('a stored record without the magnet key is read as the card; a stored one as it states', () => {
  assert.deepEqual(magnetOfRecord({ scan_params: { coil_temp_c: 120 } }), { requested: null, used_c: null, source: 'card', card: null, card_c: null });
  assert.deepEqual(magnetOfRecord(null), { requested: null, used_c: null, source: 'card', card: null, card_c: null });
  const pointOnly = magnetOfRecord({ magnet_temp_used_c: 60 });   // a point alone cannot say typed or card: never labelled card
  assert.deepEqual([pointOnly.used_c, pointOnly.source], [60, 'unknown']);
  const typed = magnetOfRecord({ scan_params: { magnet: { requested: 60, used_c: 60, source: 'typed', card: 'C', card_c: 120 } } });
  assert.deepEqual([typed.requested, typed.used_c, typed.source, typed.card], [60, 60, 'typed', 'C']);
  const card = magnetOfRecord({ eval_params: { magnet: { requested: null, used_c: 120, source: 'card', card: 'C', card_c: 120 } } });
  assert.deepEqual([card.requested, card.used_c, card.source], [null, 120, 'card']);
});

test('the strings exist in EN and zh-CN with the same placeholders, and the bar uses the server answer', () => {
  const root = new URL('../../', import.meta.url);
  const en = JSON.parse(readFileSync(new URL('locales/en/simulation.json', root), 'utf8'));
  const zh = JSON.parse(readFileSync(new URL('locales/zh-CN/simulation.json', root), 'utf8'));
  for (const k of ['runSettingsMagnetCard', 'runSettingsMagnetCardOnly', 'runSettingsMagnetTyped', 'runSettingsMagnetHelp']) {
    assert.ok(en[k] && zh[k], k);
    assert.deepEqual((en[k].match(/\{\w+\}/g) || []).sort(), (zh[k].match(/\{\w+\}/g) || []).sort(), k);
  }
  const bar = readFileSync(new URL('components/sweep/RunSettingsBar.tsx', root), 'utf8');
  assert.match(bar, /runSettingsMagnetCard/);
  assert.match(bar, /runSettingsMagnetTyped/);
});
