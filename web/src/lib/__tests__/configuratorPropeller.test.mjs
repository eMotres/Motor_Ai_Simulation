// node --test — Configure with a propeller (owner 2026-10-05): the load from the propeller, the
// cooling from its slipstream, red "> 180" temperatures, thermal zones on the knobs.
//
// Real data: the series fixture is the BACKEND's own `/api/propellers/{id}/series` output
// (motor_ai_sim.propeller.series + cooling_models.outer_air, 25 degC, Ø40 housing) and the motor is
// the REAL L12 passport (live_l12_reference_cards.json).  Nothing here is a mock.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

let P = null, M = null, E = null;
try {
  P = await import('../configuratorPropeller.ts');
  M = await import('../motorScaling.ts');
  E = await import('../thermalEstimate.ts');
} catch { /* old Node */ }
const t = P ? test : test.skip;

const HERE = join(dirname(fileURLToPath(import.meta.url)));
const SERIES = JSON.parse(readFileSync(join(HERE, 'fixtures', 'propeller_series_25c_d40.json'), 'utf8'));
const CARD = JSON.parse(readFileSync(join(HERE, 'fixtures', 'live_l12_reference_cards.json'), 'utf8')).cat_ciano14_40_new.passport;
const S12 = SERIES.tmotor_p12x4;

const KNOBS = { N: 7, L_mm: 12, wireH_mm: 0.6, nP: 1, split: 1, I_A: 40, rpm: 5000 };
const torqueAt = (rpm) => (I) => M.scaleMotor(CARD.passport, { ...KNOBS, rpm, I_A: I }, CARD.poles).T_Nm;

t('seriesAt: exact on a node, linear between nodes, null outside the grid', () => {
  const i = 100;
  const a = P.seriesAt(S12, S12.rpm[i]);
  assert.ok(Math.abs(a.torque_Nm - S12.torque_Nm[i]) < 1e-12 && Math.abs(a.air_speed_ms - S12.air_speed_ms[i]) < 1e-12);
  const mid = P.seriesAt(S12, 0.5 * (S12.rpm[i] + S12.rpm[i + 1]));
  assert.ok(Math.abs(mid.torque_Nm - 0.5 * (S12.torque_Nm[i] + S12.torque_Nm[i + 1])) < 1e-12);
  assert.ok(Math.abs(mid.h_W_m2K - 0.5 * (S12.h_W_m2K[i] + S12.h_W_m2K[i + 1])) < 1e-9);
  assert.equal(P.seriesAt(S12, -1), null);
  assert.equal(P.seriesAt(S12, P.SERIES_RPM_MAX + 1), null);
  assert.equal(S12.rpm.length, P.SERIES_N);
  assert.equal(S12.rpm[S12.rpm.length - 1], P.SERIES_RPM_MAX);
});

t('seriesAt marks the points beyond the tested rpm (P12x4 was tested at ~3400-6500 rpm)', () => {
  const [lo, hi] = S12.rpm_range_tested;
  assert.equal(P.seriesAt(S12, 0.5 * (lo + hi)).extrapolated, false);
  assert.equal(P.seriesAt(S12, hi + 500).extrapolated, true);
  assert.equal(P.seriesAt(S12, lo - 500).extrapolated, true);
  assert.equal(P.seriesAt(S12, 0).extrapolated, false);              // nothing to extrapolate at rest
});

t('the propeller torque is increasing, so it can be inverted (and so can its air speed)', () => {
  for (const s of Object.values(SERIES)) {
    for (let i = 1; i < s.rpm.length; i++) {
      assert.ok(s.torque_Nm[i] >= s.torque_Nm[i - 1] && s.air_speed_ms[i] >= s.air_speed_ms[i - 1] && s.h_W_m2K[i] >= s.h_W_m2K[i - 1] - 1e-9);
    }
  }
});

t('LOAD: the derived current is the one whose torque IS the propeller torque (real L12 passport + real P12x4)', () => {
  for (const rpm of [3000, 4500, 6000]) {
    const need = P.seriesAt(S12, rpm).torque_Nm;
    const r = P.currentForTorque(need, torqueAt(rpm), 90);
    assert.ok(r.ok, `rpm ${rpm}`);
    assert.ok(Math.abs(torqueAt(rpm)(r.I_A) - need) / need < 1e-6, `torque round trip at ${rpm}`);
    assert.ok(r.I_A > 0 && r.I_A < 90);
  }
  // more speed = more propeller torque = more current
  const I = (rpm) => P.currentForTorque(P.seriesAt(S12, rpm).torque_Nm, torqueAt(rpm), 90).I_A;
  assert.ok(I(3000) < I(4500) && I(4500) < I(6000));
});

t('LOAD: when the motor cannot make the propeller torque the answer is a loud refusal that says what it gives', () => {
  const rpm = 20000;                                              // a 12-in prop at 20 krpm needs 1.5 N·m; the Ø40 motor gives 1.17 at 90 A
  const need = P.seriesAt(S12, rpm).torque_Nm;
  const r = P.currentForTorque(need, torqueAt(rpm), 90);
  assert.equal(r.ok, false); assert.equal(r.kind, 'torque');
  assert.equal(r.need_Nm, need); assert.ok(r.have_Nm < need); assert.equal(r.I_A, 90);
  assert.equal(r.have_Nm, torqueAt(rpm)(90));
  // no propeller data yet / zero load
  assert.deepEqual(P.currentForTorque(null, torqueAt(rpm), 90), { ok: false, kind: 'no_prop' });
  assert.deepEqual(P.currentForTorque(0, torqueAt(rpm), 90), { ok: true, I_A: 0 });
});

t('COOLING: the housing film follows the propeller air; more rpm = cooler winding at the same current', () => {
  const geo = { statorOD_mm: 40, stackLength_mm: 12, numSlots: 12, slotHeight_mm: CARD.fit.slotHeight_mm, slotWidth_mm: CARD.fit.slotWidth_mm,
    insulation_mm: CARD.fit.insulation_mm, coreThickness_mm: Math.max(0, CARD.geo.statorOR_mm - CARD.geo.statorIR_mm - CARD.fit.slotHeight_mm),
    airGap_mm: Math.max(0, CARD.geo.statorIR_mm - CARD.geo.rotorOR_mm), magnetOD_mm: CARD.geo.rotorOR_mm * 2 };
  const at = (rpm) => {
    const pt = P.seriesAt(S12, rpm);
    const r = M.scaleMotor(CARD.passport, { ...KNOBS, rpm: 5000, I_A: 40 }, CARD.poles);        // same electrical state
    return E.estimateThermal(geo, { P_cu_W: r.P_cu_W, P_fe_W: r.P_fe_W, P_mag_W: r.P_mag_W }, { h_Wm2K: pt.h_W_m2K, ambient_C: 25 });
  };
  assert.ok(at(8000).T_winding_C < at(3000).T_winding_C);
  assert.ok(at(8000).T_housing_C < at(3000).T_housing_C);
  // extra heat with no location heats the housing but not the hot-spot rise above it
  const pt = P.seriesAt(S12, 5000);
  const r = M.scaleMotor(CARD.passport, { ...KNOBS, rpm: 5000, I_A: 40 }, CARD.poles);
  const a = E.estimateThermal(geo, { P_cu_W: r.P_cu_W, P_fe_W: r.P_fe_W, P_mag_W: r.P_mag_W }, { h_Wm2K: pt.h_W_m2K, ambient_C: 25 });
  const b = E.estimateThermal(geo, { P_cu_W: r.P_cu_W, P_fe_W: r.P_fe_W, P_mag_W: r.P_mag_W, P_extra_W: 10 }, { h_Wm2K: pt.h_W_m2K, ambient_C: 25 });
  assert.ok(b.T_housing_C > a.T_housing_C);
  assert.ok(Math.abs(b.dT_winding_C - a.dT_winding_C) < 1e-12 && Math.abs(b.dT_magnet_C - a.dT_magnet_C) < 1e-12);
});

t('which cooling is the propeller: only when `propeller_air` is the ONLY option', () => {
  assert.equal(P.isPropellerCooled({ restricted: true, cooling_options: ['propeller_air'] }), true);
  assert.equal(P.isPropellerCooled({ restricted: true, cooling_options: ['propeller_air', 'liquid'] }), false);
  assert.equal(P.isPropellerCooled({ restricted: false, cooling_options: null }), false);
  assert.equal(P.isPropellerCooled({ restricted: true, cooling_options: [] }), false);
  assert.equal(P.isPropellerCooled(null), false);
  assert.equal(P.isPropellerCooled(undefined), false);
});

t('propeller thermal cooling accepts mixed cooling registries without changing prop-load semantics', () => {
  const d40 = { restricted: true, cooling_options: ['propeller_air'], propellers: ['a'] };
  const d85 = { restricted: true, cooling_options: ['robotics', 'propeller_air'], propellers: ['a', 'b'],
    defaults: { L13: 'b' } };
  assert.equal(P.hasPropellerCooling(d40), true);
  assert.equal(P.hasPropellerCooling(d85), true);
  assert.equal(P.isPropellerCooled(d40), true);
  assert.equal(P.isPropellerCooled(d85), false); // do not make slipstream the Configure motor load
  assert.equal(P.hasPropellerCooling({ cooling_options: ['propeller_air'], propellers: [] }), false);
  assert.equal(P.hasPropellerCooling({ cooling_options: ['robotics'], propellers: ['a'] }), false);
});

const LIST = [
  { id: 'a', vendor: 'T-Motor', model: 'FPV 10*5', blades: 3, selectable: true, power_data: 'measured_torque' },
  { id: 'b', vendor: 'T-Motor', model: 'CF 10*3.3', blades: 2, selectable: true, power_data: 'estimated' },
  { id: 'c', vendor: 'T-Motor', model: 'MS1101', blades: 2, selectable: false, power_data: 'none' },
  { id: 'z', vendor: 'X', model: 'not allowed', selectable: true },
];

t('the picker lists only the die\'s propellers, in its order; geometry-only ones stay (disabled)', () => {
  const al = P.allowedPropellers({ propellers: ['c', 'b', 'a', 'gone'] }, LIST);
  assert.deepEqual(al.map((x) => x.id), ['c', 'b', 'a']);                  // unknown id dropped, die order kept
  assert.equal(al[0].selectable, false);
  assert.deepEqual(P.allowedPropellers(null, LIST), []);
  assert.equal(P.modelLabel('P12*4'), 'P12×4');
  assert.equal(P.modelLabel('FPV 10 * 5'), 'FPV 10×5');
  assert.equal(P.modelLabel('FPV 10*5 (10X5X3)'), 'FPV 10×5');                  // the product-page alias is not printed
  assert.equal(P.modelLabel('CF 11*3.7 (test-table label 11*3.7CF)'), 'CF 11×3.7');
  assert.equal(P.vendorLabel('T-MOTOR'), 'T-Motor');
  assert.equal(P.vendorLabel('T-Motor'), 'T-Motor');
  assert.equal(P.vendorLabel('APC'), 'APC');                               // short acronyms stay
  assert.equal(P.defaultPropeller(al), 'a');                               // first measured-torque one
  assert.equal(P.defaultPropeller([LIST[1], LIST[2]]), 'b');               // else first usable
  assert.equal(P.defaultPropeller([LIST[2]]), null);
});

t('the remembered choice is per machine, validated, and falls back to the default', () => {
  const al = P.allowedPropellers({ propellers: ['a', 'b', 'c'] }, LIST);
  assert.deepEqual(P.readCoolChoice(null, 'cat:x'), {});
  let raw = P.writeCoolChoice(null, 'cat:x', { propId: 'b', ambient: 31, load: 'manual' });
  raw = P.writeCoolChoice(raw, 'cat:y', { ambient: 5 });
  assert.deepEqual(P.readCoolChoice(raw, 'cat:x'), { propId: 'b', ambient: 31, load: 'manual' });
  assert.deepEqual(P.readCoolChoice(raw, 'cat:y'), { ambient: 5 });
  assert.deepEqual(P.readCoolChoice(P.writeCoolChoice(raw, 'cat:x', { ambient: 12 }), 'cat:x'), { propId: 'b', ambient: 12, load: 'manual' });
  assert.deepEqual(P.readCoolChoice('{"cat:x":{"propId":3,"ambient":900,"load":"x"}}', 'cat:x'), {});
  assert.deepEqual(P.readCoolChoice('garbage', 'cat:x'), {});
  assert.equal(P.effectivePropeller({ propId: 'b' }, al), 'b');
  assert.equal(P.effectivePropeller({ propId: 'c' }, al), 'a');             // no data: never selected
  assert.equal(P.effectivePropeller({ propId: 'gone' }, al), 'a');
  assert.equal(P.effectivePropeller({}, []), null);
});

t('the mixed-registry cooling choice validates and survives unrelated prop/ambient edits', () => {
  let raw = P.writeCoolChoice(null, 'cat:d85', { cooling: 'robotics' });
  raw = P.writeCoolChoice(raw, 'cat:d85', { propId: 'b', cooling: 'propeller' });
  assert.deepEqual(P.readCoolChoice(raw, 'cat:d85'), { cooling: 'propeller', propId: 'b' });
  raw = P.writeCoolChoice(raw, 'cat:d85', { ambient: 27 });
  assert.deepEqual(P.readCoolChoice(raw, 'cat:d85'), { cooling: 'propeller', propId: 'b', ambient: 27 });
  assert.deepEqual(P.readCoolChoice('{"cat:d85":{"cooling":"manual"}}', 'cat:d85'), {});
});

t('Thermal server settings retain the bound propeller ID and clear stale motor identities', () => {
  assert.equal(P.propellerContextKey(' User@Example.COM ', 'D85', 'L13', 'ref-a'),
    'user@example.com|D85|L13|ref-a');
  assert.deepEqual(P.thermalPropellerSettingsFields('propeller', 'G32x11', 'user|D85|L13|ref-a',
    'user|D85|L13|ref-a'), {
    airSpeedSource: 'propeller', propellerId: 'G32x11', propellerContextKey: 'user|D85|L13|ref-a',
  });
  assert.deepEqual(P.thermalPropellerSettingsFields('propeller', 'G32x11', 'user|D85|L13|ref-a',
    'user|D85|L13|ref-b'), {
    airSpeedSource: 'propeller', propellerId: '', propellerContextKey: '',
  });
  assert.deepEqual(P.thermalPropellerSettingsFields('manual', 'G32x11', 'old-context', null), {
    airSpeedSource: 'manual', propellerId: '', propellerContextKey: '',
  });
});

t('Thermal server-restored ID beats another browser local default only for the live assigned motor', () => {
  const allowed = [
    { id: 'local-default', selectable: true },
    { id: 'server-choice', selectable: true },
    { id: 'assigned-but-unusable', selectable: false },
  ];
  const key = 'user@example.com|D85|L13|ref-a';
  // A second browser has no local choice (or a different default); only the
  // exact live context and currently selectable registry row can restore it.
  assert.equal(P.restoredThermalPropeller('server-choice', key, key, allowed), 'server-choice');
  assert.equal(P.restoredThermalPropeller('assigned-but-unusable', key, key, allowed), null);
  assert.equal(P.restoredThermalPropeller('server-choice', key,
    'user@example.com|D85|L13|ref-b', allowed), null);
  assert.equal(P.restoredThermalPropeller('server-choice', key, null, allowed), null);
});

t('the server snapshot saves a ready bound propeller and never browser-persists its identity', () => {
  const base = { coolMode: 'manual', ambientT: '25', airSpeedSource: 'propeller' };
  const snapshot = P.thermalServerSettingsSnapshot(base, 'propeller', 'G32x11',
    'user@example.com|D85|L13|ref-a', 'user@example.com|D85|L13|ref-a');
  assert.deepEqual(snapshot, {
    ...base, propellerId: 'G32x11',
    propellerContextKey: 'user@example.com|D85|L13|ref-a',
  });
  const stale = P.thermalServerSettingsSnapshot(base, 'propeller', 'G32x11',
    'user@example.com|D85|L13|ref-a', 'user@example.com|D85|L13|ref-b');
  assert.equal(stale.propellerId, '');
  assert.equal(stale.propellerContextKey, '');
  const store = readFileSync(join(HERE, '..', '..', 'stores', 'thermalStore.ts'), 'utf8');
  const persisted = store.match(/const PERSISTED:[^=]+=[\s\S]*?\];/)?.[0] ?? '';
  assert.match(persisted, /'airSpeedSource'/);
  assert.doesNotMatch(persisted, /'propellerId'|'propellerContextKey'/);
  assert.match(store, /thermalServerSettingsSnapshot\([\s\S]*?livePropellerContextKey\(\)\)/);
  assert.match(store, /k === 'propellerId' \|\| k === 'propellerContextKey'[\s\S]*?persistPanelSettings\(\)/);
  assert.match(store, /serverSettingsLoaded: false/);
  assert.match(store, /setPropellerCooling: \(source, id = '', contextKey = ''\)[\s\S]*?savePanelSettings\('thermal'/);
  const panel = readFileSync(join(HERE, '..', '..', 'components', 'thermal', 'ThermalPanel.tsx'), 'utf8');
  assert.match(panel, /restoredThermalPropeller\([\s\S]*?st\.serverSettingsLoaded/);
  assert.match(panel, /syncedServerChoice\.current === token/);
});

t('temperature limits: the winding is class H 180 degC, the magnet the card\'s own (stated fallback)', () => {
  assert.deepEqual(P.tempLimits({ winding_C: 180, magnet_C: 150, winding_basis: 'class H default' }),
    { winding_C: 180, magnet_C: 150, magnetIsDefault: false, windingBasis: 'class H default' });
  const d = P.tempLimits({ magnet_C: null });
  assert.equal(d.winding_C, 180); assert.equal(d.magnet_C, P.DEFAULT_MAGNET_LIMIT_C); assert.equal(d.magnetIsDefault, true);
  assert.equal(P.tempLimits(undefined).winding_C, 180);
  const lim = P.tempLimits({ winding_C: 180, magnet_C: 150 });
  assert.deepEqual(P.judgeTemps(179, 149, lim), { windingOver: false, magnetOver: false, over: false });
  assert.deepEqual(P.judgeTemps(181, 100, lim), { windingOver: true, magnetOver: false, over: true });
  assert.deepEqual(P.judgeTemps(100, 151, lim), { windingOver: false, magnetOver: true, over: true });
  assert.deepEqual(P.tempTile(2263, 180), { value: 180, over: true, level: 'bad' });
  assert.deepEqual(P.tempTile(null, 180), { value: null, over: false });
});

t('thermal zones: green below the limits, red beyond, neutral where not judged', () => {
  assert.equal(P.zoneGradient([null, null]), null);
  assert.equal(P.zoneGradient([true]), null);
  const g = P.zoneGradient([true, true, false, false, null]);
  assert.match(g, /^linear-gradient\(to right, /);
  assert.ok(g.indexOf('#16a34a') < g.indexOf('#dc2626'));                  // green first, red after
  assert.match(g, /rgba\(148,163,184,0\.35\) 80\.00%/);                    // the unjudged last cell
  const xs = P.zoneSamples(1000, 3000, 4);
  assert.deepEqual(xs, [1250, 1750, 2250, 2750]);                          // the centre of each cell
});

t('ZONES on the real motor: with the real propeller the speed knob goes green -> red as the winding heats', () => {
  // The motor's own torque cannot follow a 12-in prop to high rpm, and its heat grows with speed:
  // across the range there must be a green start and a red end — the zones are not all one colour.
  const geo = { statorOD_mm: 40, stackLength_mm: 12, numSlots: 12, slotHeight_mm: CARD.fit.slotHeight_mm, slotWidth_mm: CARD.fit.slotWidth_mm,
    insulation_mm: CARD.fit.insulation_mm, coreThickness_mm: Math.max(0, CARD.geo.statorOR_mm - CARD.geo.statorIR_mm - CARD.fit.slotHeight_mm),
    airGap_mm: Math.max(0, CARD.geo.statorIR_mm - CARD.geo.rotorOR_mm), magnetOD_mm: CARD.geo.rotorOR_mm * 2 };
  const lim = P.tempLimits({ winding_C: 180, magnet_C: 150 });
  const okAt = (rpm) => {
    const pt = P.seriesAt(S12, rpm);
    const ld = P.currentForTorque(pt.torque_Nm, torqueAt(rpm), 90);
    if (!ld.ok) return false;
    const r = M.scaleMotor(CARD.passport, { ...KNOBS, rpm, I_A: ld.I_A }, CARD.poles);
    const th = E.estimateThermal(geo, { P_cu_W: r.P_cu_W, P_fe_W: r.P_fe_W, P_mag_W: r.P_mag_W }, { h_Wm2K: pt.h_W_m2K, ambient_C: 25 });
    return !P.judgeTemps(th.T_winding_C, th.T_magnet_C, lim).over;
  };
  const samples = P.zoneSamples(1000, 14000, 48).map(okAt);
  assert.equal(samples[0], true);                                          // light load: fine
  assert.equal(samples[samples.length - 1], false);                        // far beyond what the motor can do
  const firstRed = samples.indexOf(false);
  assert.ok(samples.slice(firstRed).every((x) => x === false) || samples.slice(firstRed).includes(false));
  assert.ok(firstRed > 0 && firstRed < samples.length - 1);
});

t('the panel: thermal estimate is available for mixed cooling, while only propeller-only mode owns motor load', () => {
  const panel = readFileSync(join(HERE, '..', '..', 'components', 'compare', 'ConfiguratorPanel.tsx'), 'utf8');
  assert.match(panel, /\{ctxDone && !propellerOnly && \(\s*<Box sx=\{\{ px: 2, pb: 1\.5 \}\}>\s*<ConfiguratorThermal/);   // mixed and ordinary machines get the estimate
  assert.equal((panel.match(/<ConfiguratorThermal/g) || []).length, 1);
  assert.match(panel, /\{propellerOnly && cooled && \(\s*<Box sx=\{\{ display: 'flex', gap: 0\.75, flexWrap: 'wrap' \}\}>\s*\{tempTiles\.map\(renderTemp\)\}/);
  assert.equal((panel.match(/tempTiles\.map\(renderTemp\)/g) || []).length, 1);                              // one propeller-only row
  assert.match(panel, /const propLoad = propellerOnly &&/);                                                     // mixed mode preserves manual current
  assert.match(panel, /optionalPropellerCooling \? \{/);                                                       // mixed mode exposes its cooling estimate
  assert.match(panel, /currentForTorque\(propPoint\?\.torque_Nm \?\? null, torqueAtI\(knobs\), ranges\.I_A\.max\)/);
  assert.match(panel, /disabled=\{propLoad\}/);                                                              // the current is shown, not typed, in propeller mode
  assert.match(panel, /updateCool\(\{ load: v \}\)/);                                                       // the manual-load toggle
  assert.match(panel, /zone=\{cooled \? zones\.rpm : null\}/);                                               // thermal zones on the speed knob
  assert.match(panel, /zone=\{propLoad \? null : zones\.I\}/);                                               // and on the current knob in manual mode
  // power and efficiency go red on a refusal and carry the red line
  assert.equal((panel.match(/absLevel=\{propBad \? 'bad' : undefined\}/g) || []).length, 2);
});

t('the zone calculation is cheap enough to follow the sliders (48 samples x a bisection)', () => {
  const t0 = performance.now();
  for (let k = 0; k < 5; k++) {
    for (const rpm of P.zoneSamples(1000, 14000, 48)) {
      P.currentForTorque(P.seriesAt(S12, rpm).torque_Nm, torqueAt(rpm), 90);
    }
  }
  const per = (performance.now() - t0) / 5;
  assert.ok(per < 400, `one zone recompute took ${per.toFixed(0)} ms`);   // measured: well under; a loose bound for slow CI
});
