/** Execute SummaryTable's mechanical block and ActiveFamilyStrip's save expressions without React/browser dependencies. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import * as duty from '../../../lib/dutyEfficiency.ts';
import { shaftBalance } from '../../../lib/shaftBalance.ts';
import { atomicDutyRunBody } from '../../../lib/dutyRunSave.ts';

const table = fs.readFileSync(new URL('../../simulation/SummaryTable.tsx', import.meta.url), 'utf8');
const saver = fs.readFileSync(new URL('../ActiveFamilyStrip.tsx', import.meta.url), 'utf8');
const block = table.slice(table.indexOf('  const runMech ='), table.indexOf('  // Stale-operating-point guard:'));
assert.ok(block.includes('const mechView: EmMech ='));
assert.ok(table.includes('mech={mechView}'));
const render = new Function('summary', 's', 'mech', 'readOnly',
  'hasMechanicalContext', 'resolveMechanicalContext', 'rememberPageMechanicalContext',
  stripTypeScriptTypes(block) + '\nreturn mechView;');
const captureLine = saver.match(/const s = [^\n]*readLS\('lastSummary', null\)[^\n]*/)?.[0];
assert.ok(captureLine);
const capture = new Function('readLS', 'withPageMechanicalContext', captureLine + '\nreturn s;');
const resultBlock = saver.slice(saver.indexOf('        const result = { efficiency_pct:'),
  saver.indexOf('        // Read-only charts derive'));
assert.ok(resultBlock.includes('dutyEfficiencyPct(s, rotorW)'));
assert.ok(saver.includes('settings: dutyBody.mesh, summary: s, result,'));
const result = new Function('s', 'rotorW', 'sc', 'dutyEfficiencyPct', 'dutyEfficiencyBasis',
  'shaftExtraW', 'quotedRipple', 'k3d', 'vw', resultBlock + '\nreturn result;');

const marked = { shaft_convention: 'fe_on_shaft_k3d_v1', op_mode: 'motor',
  P_mech_W: 100, P_loss_total_W: 10, P_bearings_W: 3, P_windage_W: 1, _runAt: 'run-A' };
const live20 = { has_bearings: true, P_mech_extra_W: 20, P_bearings_W: 19, P_windage_W: 1 };
const draw = (s, live, readOnly = false) => render(s, s, live, readOnly,
  duty.hasMechanicalContext, duty.resolveMechanicalContext, duty.rememberPageMechanicalContext);
const snapshot = (s) => capture(() => JSON.parse(JSON.stringify(s)), duty.withPageMechanicalContext);
const save = (s) => JSON.parse(JSON.stringify(atomicDutyRunBody({ die: 'SYNTH', config: 'L40',
  duty: 'rated', drive: 'current', summary: s,
  result: result(s, s.P_mech_W, true, duty.dutyEfficiencyPct, duty.dutyEfficiencyBasis,
    duty.shaftExtraW, () => ({ value: null }), null, null) })));

const payloadBlock = saver.slice(saver.indexOf('        const savedPayload ='),
  saver.indexOf('        // Summary and numbers are one save'));
const payloadFor = new Function('s', 'sc', 'tOfRun', payloadBlock + '\nreturn savedPayload;');
assert.ok(saver.includes('assignment_sig: dutyBody.assignment_sig }, savedPayload))'));

const rows = [];
for (const [name, live, extra, source, expected] of [
  ['response absent', null, null, 'unknown', 100 / 110 * 100],
  ['response says no bearings', { has_bearings: false, P_windage_W: 1 }, null, 'unknown', 100 / 110 * 100],
  ['live friction 20 W', live20, 20, 'live_bearings', 80 / 110 * 100],
]) {
  test(`actual page fallback -> save -> reopened page: ${name}`, () => {
    const view = draw(marked, live);
    const shown = shaftBalance(marked, view.extra_W).eta * 100;
    const payload = save(snapshot(marked));
    assert.equal(shown, expected);
    assert.equal(payload.result.efficiency_pct, shown);
    assert.equal(payload.result.efficiency_pct.toFixed(2), shown.toFixed(2));
    assert.equal(payload.summary.P_mech_extra_W, extra);
    assert.equal(payload.summary.P_mech_extra_source, source);
    assert.equal(payload.result.efficiency_basis, extra == null ? 'shaft_before_bearings' : 'coupling');
    assert.equal(payload.result.loss_mech_w, extra ?? undefined);
    // A future live response cannot replace the context saved with this point (including a resolved null).
    const reopened = draw(payload.summary, { ...live20, P_mech_extra_W: 99 }, true);
    assert.equal(reopened.extra_W, view.extra_W);
    assert.equal(shaftBalance(payload.summary, reopened.extra_W).eta * 100, shown);
    assert.equal(duty.hasMechanicalContext(payload.summary), true); // skips a new bearings fetch
    rows.push({ name, input_W: { power: 100, em_loss: 10, stored_bearings: 3, stored_windage: 1 },
      resolved_extra_W: extra, source, page_pct: shown, saved_pct: payload.result.efficiency_pct,
      page_2dp: shown.toFixed(2), saved_2dp: payload.result.efficiency_pct.toFixed(2) });
  });
}

test('run total wins over live response and survives the atomic save', () => {
  const s = { ...marked, P_mech_extra_W: 4 };
  const view = draw(s, live20);
  const payload = save(snapshot(s));
  assert.equal(view.extra_W, 4);
  assert.equal(payload.summary.P_mech_extra_source, 'run');
  assert.equal(payload.result.efficiency_pct, shaftBalance(s, view.extra_W).eta * 100);
});

test('capture before awaits is immutable; read-only cards and other runs cannot overwrite its context', () => {
  draw(marked, live20);
  draw({ ...marked, _runAt: 'other-card' }, null, true);
  const captured = snapshot(marked);
  draw(marked, null); // fetch changes while Save awaits waveforms
  assert.equal(save(captured).summary.P_mech_extra_W, 20);
  assert.equal(save(captured).result.efficiency_pct, 80 / 110 * 100);
  const other = { ...marked, _runAt: 'run-B' };
  assert.equal(snapshot(other).P_mech_extra_W, null);
  assert.equal(snapshot(other).P_mech_extra_source, 'unknown');
  // Full identity match, not merely rpm/current/gamma or a reused timestamp.
  draw(marked, live20);
  assert.equal(snapshot({ ...marked, P_loss_total_W: 11 }).P_mech_extra_W, null);
  assert.equal(marked.P_mech_extra_W, undefined);
});

test('waveform summary receives exactly the saved mechanical context, including null', () => {
  for (const live of [null, live20]) {
    draw(marked, live);
    const s = snapshot(marked);
    const transient = { time_s: [0, 1], summary: { ...marked, chart_only: 123 } };
    const saved = payloadFor(s, true, transient);
    for (const key of ['P_mech_extra_W', 'P_mech_extra_source', 'P_bearings_W', 'P_windage_W']) {
      assert.equal(saved.summary[key], s[key]);
    }
    assert.equal(saved.summary.chart_only, 123);
    assert.equal(transient.summary.P_mech_extra_W, undefined);
    assert.equal(shaftBalance(saved.summary, draw(saved.summary, live20, true).extra_W).eta * 100,
      save(s).result.efficiency_pct);
    assert.equal(payloadFor(s, false, transient), transient);
    assert.equal(payloadFor(s, true, null), null);
  }
});

test('unmarked capture keeps the legacy summary byte-for-byte', () => {
  const s = { ...marked, shaft_convention: undefined };
  draw(s, live20);
  assert.equal(JSON.stringify(snapshot(s)), JSON.stringify(s));
});

test('record synthetic numeric evidence when requested', () => {
  if (process.env.ASTRA_EFFICIENCY_ROWS) fs.writeFileSync(process.env.ASTRA_EFFICIENCY_ROWS,
    JSON.stringify({ meaning: 'Synthetic watts and percent from actual page/save source blocks; calculated, not FEM.', rows }, null, 2));
});
