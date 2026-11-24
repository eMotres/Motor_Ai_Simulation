// node --test - the run settings of the Sweep / Optimization / Descent panels: ONE read-only line, the values of the tabs (owner 2026-11-24)
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { tabRequest, sameRequest, answerKey, runSettingsBody, baselineBody, lineText, notOptimization, wireCellOf } from '../runSettings.ts';
import { stepsReason } from '../eddySteps.ts';
import { readCurrentSettings, readStepsSource } from '../currentSettings.ts';

const root = new URL('../../', import.meta.url);
const read = (f) => readFileSync(new URL(f, root), 'utf8');
const tab = { steps: 48, gap_layers: 1, mesh_size_mm: 1.8, min_size_mm: 0.2, n_sectors: 2, demag: true, rotor_eddy: true, component_mesh: { coil_rel: 2 } };

test('the request carries the visible values under the name tab; a descent request omits demag (the machine\'s)', () => {
  const { request, missing } = tabRequest(tab, 'sweep');
  assert.deepEqual(missing, []);
  assert.deepEqual(request, { preset: 'tab', overrides: { steps_per_period: 48, gap_layers: 1, mesh_size_mm: 1.8, min_size_mm: 0.2, n_sectors: 2, coil_rel: 2, rotor_eddy: true, demag: true } });
  assert.equal('demag' in tabRequest(tab, 'descent').request.overrides, false);
  assert.equal(tabRequest({ ...tab, rotor_eddy: false }, 'sweep').request.overrides.rotor_eddy, false);   // the tab's value, not a default
});

test('a value the tabs do not hold is named and gives no request (unknown, Run disabled): nothing is filled in', () => {
  const r = tabRequest({ ...tab, steps: undefined, n_sectors: undefined }, 'sweep');
  assert.equal(r.request, null);
  assert.deepEqual(r.missing, ['steps per period', 'sectors']);
  assert.equal(tabRequest({ ...tab, gap_layers: NaN }, 'sweep').request, null);
  // rotor eddy and demag come from the tabs too: a missing one is unknown (demag not for a Descent run, which uses the machine's)
  assert.deepEqual(tabRequest({ ...tab, rotor_eddy: undefined }, 'sweep').missing, ['rotor eddy']);
  assert.deepEqual(tabRequest({ ...tab, demag: undefined }, 'sweep').missing, ['demag']);
  assert.deepEqual(tabRequest({ ...tab, demag: undefined }, 'auto').missing, ['demag']);
  assert.notEqual(tabRequest({ ...tab, demag: undefined }, 'descent').request, null);
});

const store = (o) => ({ getItem: (k) => (k in o ? o[k] : null) });
const stored = { 'sim.stepsPP': '48', 'mesh.gapLayers': '1', 'mesh.meshSize': '1.8', 'mesh.minSize': '0.2', 'mesh.nSectors': '2',
  'sim.demag': 'true', 'mesh.componentMesh': '{"stator":1.8,"coil_rel":2}' };

test('the REAL readCurrentSettings output feeds tabRequest: the names match, the wire cell comes from mesh.componentMesh', () => {
  const cur = readCurrentSettings(store(stored));
  for (const k of ['steps', 'gap_layers', 'mesh_size_mm', 'min_size_mm', 'n_sectors', 'demag', 'rotor_eddy', 'component_mesh']) {
    assert.ok(k in cur, k);
  }
  const { request, missing } = tabRequest(cur, 'sweep');
  assert.deepEqual(missing, []);
  assert.deepEqual(request.overrides, { steps_per_period: 48, gap_layers: 1, mesh_size_mm: 1.8, min_size_mm: 0.2, n_sectors: 2,
    coil_rel: 2, rotor_eddy: true, demag: true });
  // no wire-cell key = the Mesh tab's 1h; a stored sim.fieldLosses wins over the tab's constant
  assert.equal(tabRequest(readCurrentSettings(store({ ...stored, 'mesh.componentMesh': '{"stator":1.8}' })), 'sweep').request.overrides.coil_rel, 1);
  assert.equal(tabRequest(readCurrentSettings(store({ ...stored, 'sim.fieldLosses': 'false' })), 'sweep').request.overrides.rotor_eddy, false);
  // a field the tabs never wrote is unknown (nothing defaulted): fresh browser
  const fresh = tabRequest(readCurrentSettings(store({})), 'sweep');
  assert.equal(fresh.request, null);
  assert.deepEqual(fresh.missing, ['steps per period', 'gap rows', 'mesh size', 'min mesh size', 'sectors', 'demag']);
  const hook = read('lib/useRunSettings.ts');
  assert.ok(/import \{ readCurrentSettings, readStepsSource \} from '\.\/currentSettings'/.test(hook) && hook.includes('tabRequest(readCurrentSettings(), mode)'));
  assert.ok(read('lib/meshPresets.ts').includes("export { readCurrentSettings } from './currentSettings'"));
});

test('sim.stepsSource is read whether the tab stored it as JSON or as a plain string', () => {
  assert.equal(readStepsSource(store({ 'sim.stepsSource': '"eddy_default"' })), 'eddy_default');
  assert.equal(readStepsSource(store({ 'sim.stepsSource': 'eddy_default' })), 'eddy_default');
  assert.equal(readStepsSource(store({ 'sim.stepsSource': 'null' })), null);
  assert.equal(readStepsSource(store({})), null);
  assert.deepEqual(stepsReason(108, readStepsSource(store({ 'sim.stepsSource': 'eddy_default' })), 24, 28),
    { kind: 'ripple_grade', steps: 108, cycles: 12, samples: 9 });
});

test('Change in Mesh calls the typed store action with the tab id the app really uses', () => {
  const bar = read('components/sweep/RunSettingsBar.tsx');
  assert.ok(bar.includes('useUIStore(st => st.setActiveTab)') && bar.includes("goToTab('mesh')") && !/as never|: any/.test(bar));
  assert.ok(/setActiveTab: \(tab: [^)]*'mesh'[^)]*\) => void/.test(read('stores/uiStore.ts')));
  assert.ok(/\{ id: 'mesh', label: 'Mesh'/.test(read('App.tsx')));
});

test('Descent Run and Draw baseline say so when the tabs changed since the line was shown', () => {
  const d = read('components/sweep/DescentPanel.tsx');
  assert.ok(d.includes('setRsStale(RS_STALE)') && d.includes('setRsStale(q ? null : RS_STALE)') && d.includes('{rsStale && ('));
});

test('the wire cell is the Mesh tab\'s own reading: a missing key is 1h', () => {
  assert.equal(wireCellOf(undefined), 1);
  assert.equal(wireCellOf({ stator: 1 }), 1);
  assert.equal(wireCellOf({ coil_rel: 0.5 }), 0.5);
  assert.equal(tabRequest({ ...tab, component_mesh: {} }, 'sweep').request.overrides.coil_rel, 1);
});

test('requests are compared by their values, not their key order', () => {
  const a = tabRequest(tab, 'sweep').request;
  const b = { preset: 'tab', overrides: Object.fromEntries(Object.entries(a.overrides).reverse()) };
  assert.equal(sameRequest(a, b), true);
  assert.equal(sameRequest(a, tabRequest({ ...tab, steps: 108 }, 'sweep').request), false);
  assert.equal(sameRequest(a, null), false);
  assert.equal(sameRequest(null, null), true);
});

test('an answer is current only for the same machine, duty, mode and values', () => {
  const a = tabRequest(tab, 'sweep').request;
  const k = answerKey('d/c/S1', 'sweep', a);
  assert.notEqual(k, answerKey('d/c/S2', 'sweep', a));
  assert.notEqual(k, answerKey('d/c/S1', 'descent', a));
  assert.notEqual(k, answerKey('d/c/S1', 'sweep', tabRequest({ ...tab, steps: 96 }, 'sweep').request));
});

test('the request carries the values and the digest and token that were shown', () => {
  const a = tabRequest(tab, 'sweep').request;
  assert.deepEqual(runSettingsBody(a, { sig: 7 }), { preset: 'tab', overrides: a.overrides, expect_sig: 7 });
  assert.deepEqual(runSettingsBody(a, { sig: 7, token: 'abc' }), { preset: 'tab', overrides: a.overrides, expect_sig: 7, expect_token: 'abc' });
});

const words = { preset: (l, v) => `${l} preset v${v}`, custom: 'custom', steps: n => `${n} steps`, gap: n => `${n} gap ${n === 1 ? 'row' : 'rows'}`, wire: n => `wire cell ${n}h` };
const ans = (over) => ({ label: 'optimization', custom: false, preset_label: 'Optimization', version: 2, settings: { steps_per_period: 48, gap_layers: 1, coil_rel: 2 }, ...over });

test('the line is ONE sentence from the server answer; the warning follows the label, not the panel', () => {
  assert.equal(lineText(ans(), words), 'Optimization preset v2 \u00b7 48 steps \u00b7 1 gap row \u00b7 wire cell 2h');
  assert.equal(notOptimization(ans()), false);
  const c = ans({ label: 'custom', custom: true, settings: { steps_per_period: 108, gap_layers: 1, coil_rel: 1 } });
  assert.equal(lineText(c, words), 'custom: 108 steps, 1 gap row, wire cell 1h');
  assert.equal(notOptimization(c), true);
  assert.equal(notOptimization(ans({ label: 'simulation', preset_label: 'Simulation' })), true);
});

test('why the steps are what they are: the Simulation tab\'s own rule, from the same helpers', () => {
  const r = stepsReason(108, 'eddy_default', 24, 28);          // 12 cogging cycles x 9
  assert.deepEqual(r, { kind: 'ripple_grade', steps: 108, cycles: 12, samples: 9 });
  assert.equal(stepsReason(108, 'user', 24, 28), null);          // a count the user picked has no rule behind it
  assert.equal(stepsReason(96, 'eddy_default', 24, 28), null);   // not the default of this machine
  assert.deepEqual(stepsReason(72, 'eddy_default', 6, 4), { kind: 'eddy_default', steps: 72 });
});

test('the line has no editable field and no preset button; Change in Mesh opens the Mesh tab', () => {
  const bar = read('components/sweep/RunSettingsBar.tsx');
  assert.ok(!/TextField|<Select|applyPreset|setOverride|choose\(/.test(bar));
  assert.ok(bar.includes("goToTab('mesh'") && bar.includes('runSettingsChangeInMesh') && bar.includes('runSettingsNotOptimization'));
  assert.ok(bar.includes('stepsReason(') && !bar.includes('rippleGradeSteps'));            // imported helper, rule not restated
});

test('no per-panel record: no stored record, no preset buttons, the old view key is dropped', () => {
  const rs = read('lib/runSettings.ts'), hook = read('lib/useRunSettings.ts');
  for (const w of ['RunRecord', 'validRecord', 'forMode', 'choosePreset', 'withOverride', 'DEFAULT_RECORD', 'recordScope']) {
    assert.ok(!rs.includes(w) && !hook.includes(w), w);
  }
  assert.ok(!/writeView|readView|localStorage\.setItem/.test(hook));
  assert.ok(hook.includes("view.runSettings.") && hook.includes('removeItem'));
});

test('opening a panel never writes the ordinary-run settings: no preset apply, no config PATCH, no localStorage write', () => {
  for (const f of ['lib/useRunSettings.ts', 'lib/runSettings.ts', 'components/sweep/RunSettingsBar.tsx']) {
    assert.ok(!/applyPreset|localStorage\.setItem|method:\s*'PATCH'|api\/mesh\/config/.test(read(f)), f);
  }
  for (const f of ['components/sweep/SweepStudyPanel.tsx', 'components/sweep/DescentPanel.tsx', 'components/sweep/AutoOptimizePanel.tsx']) {
    assert.ok(!/applyPreset|useRunPreset|MeshPresetSelect/.test(read(f)), f);
  }
});

test('each panel gates Run on the held answer and re-reads the tabs at Run time (what the line shows is what runs)', () => {
  const sweep = read('components/sweep/SweepStudyPanel.tsx');
  assert.ok(sweep.includes('run_settings: runSettingsBody(rsReq') && sweep.includes('!rs.ready') && sweep.includes('rs.verify()'));
  const desc = read('components/sweep/DescentPanel.tsx');
  assert.ok(desc.includes('runSettings: runSettingsBody(rsReq') && desc.includes('!rs.ready') && desc.includes('rs.verify()'));
  const auto = read('components/sweep/AutoOptimizePanel.tsx');
  assert.ok(auto.includes('run_settings: runSettingsBody(rsReq') && auto.includes('!rs.ready') && auto.includes('rs.verify()'));
});

test('applying a descent / auto result leaves the ordinary-run fidelity alone: no steps, gap rows, sectors or mesh sizes are restored', () => {
  const src = readFileSync(new URL('../../stores/motorStore.ts', import.meta.url), 'utf8');
  const a = src.indexOf('function restoreDescentEvalParams');
  const body = src.slice(a, src.indexOf('\n}\n', a));
  for (const key of ['sim.stepsPP', 'mesh.gapLayers', 'mesh.meshSize', 'mesh.minSize', 'mesh.nSectors']) {
    assert.ok(!body.includes(key), key);
  }
  assert.ok(body.includes("'descent-eval-params'") && body.includes('steps_per_period: _s'));   // the live event is stripped too
});

test('no UI string or comment claims a complete-settings identity or a stale-display guard for more than steps and gap rows', () => {
  const root = new URL('../../', import.meta.url);
  for (const f of ['locales/en/simulation.json', 'lib/sweepSettings.ts', 'lib/runSettings.ts', 'components/sweep/RunSettingsBar.tsx']) {
    assert.ok(!/full settings|complete settings|COMPLETE settings/i.test(readFileSync(new URL(f, root), 'utf8')), f);
  }
});

test('no UI string or comment claims a complete-settings identity or a stale-display guard for more than steps and gap rows', () => {
  const root = new URL('../../', import.meta.url);
  for (const f of ['locales/en/simulation.json', 'lib/sweepSettings.ts', 'lib/runSettings.ts', 'components/sweep/RunSettingsBar.tsx']) {
    assert.ok(!/full settings|complete settings|COMPLETE settings/i.test(readFileSync(new URL(f, root), 'utf8')), f);
  }
});

test('the Draw baseline body carries the resolved steps and gap rows and the request, nothing from the stored Mesh tab', () => {
  const shown = { sig: 5, settings: { steps_per_period: 96, gap_layers: 2, n_sectors: 2, mesh_size_mm: 1, min_size_mm: 0.1 } };
  const req = tabRequest(tab, 'descent').request;
  const b = baselineBody({ operating_point: { current_a: 10 }, current_bump_pct: 10 }, req, shown);
  assert.equal(b.steps_per_period, 96);
  assert.equal(b.gap_layers, 2);
  assert.equal(b.n_sectors, 2);
  assert.deepEqual(b.run_settings, { preset: 'tab', overrides: req.overrides, expect_sig: 5 });
  const src = read('stores/motorStore.ts');
  const fn = src.slice(src.indexOf('computeBaselineLine: async'), src.indexOf('cancelDescent: async'));
  assert.ok(fn.includes('baselineBody(') && !/storedGapLayers|mesh\.meshSize|mesh\.minSize|sim\.fieldLosses/.test(fn));
});

test('the new line strings exist in EN and zh-CN and the removed ones are gone', () => {
  for (const l of ['en', 'zh-CN']) {
    const j = JSON.parse(read(`locales/${l}/simulation.json`));
    for (const k of ['runSettingsPresetLine', 'runSettingsStepsN', 'runSettingsGapOne', 'runSettingsGapMany', 'runSettingsWireN', 'runSettingsSetInMesh',
                     'runSettingsChangeInMesh', 'runSettingsNotOptimization', 'runSettingsUnknown', 'runSettingsReasonRipple', 'runSettingsReasonEddy']) assert.ok(j[k], `${l} ${k}`);
    for (const k of ['runPresetOptimization', 'runPresetSimulation', 'runSettingsSteps', 'runSettingsGap', 'runSettingsLabel', 'runSettingsPresetWord', 'runSettingsSrcTyped']) assert.ok(!(k in j), `${l} ${k}`);
  }
});

test('a refresh invalidates the held answer (a geometry change cannot leave the old one usable)', () => {
  const hook = read('lib/useRunSettings.ts');
  const before = hook.slice(hook.indexOf('let live = true;'), hook.indexOf("fetch(`${API}"));
  assert.ok(before.includes('setHeld(null)'));
  assert.ok(/'family-changed'/.test(hook) && /'sim-design-applied'/.test(hook) && /'mesh-preset-applied'/.test(hook));
});

test('a refused baseline (409) refreshes the held answer', () => {
  const store = readFileSync(new URL('../../stores/motorStore.ts', import.meta.url), 'utf8');
  const fn = store.slice(store.indexOf('computeBaselineLine: async'), store.indexOf('cancelDescent: async'));
  assert.ok(fn.includes('res.status === 409') && fn.includes('return { stale }'));
  const panel = readFileSync(new URL('../../components/sweep/DescentPanel.tsx', import.meta.url), 'utf8');
  assert.ok(/computeBaselineLine\([^]*?\)\.then\(r => \{ if \(r\.stale\) rs\.refresh\(\); \}\)/.test(panel));
});
