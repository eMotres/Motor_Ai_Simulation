// node --test — the results tile block is the SAME in Sine and PWM (owner 2026-10-05:
// «сделаем поле значений одинаковым для sin и pwm, а то всё дёргается при переключении»), and the
// tile titles carry no method / provenance tags («не надо писать · 3-D flux»).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

let T = null;
try { T = await import('../configuratorTiles.ts'); } catch { /* old Node */ }
const t = T ? test : test.skip;

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const read = (f) => readFileSync(join(SRC, f), 'utf8');
const loc = (l) => JSON.parse(read(`locales/${l}/controller.json`));

const FULL = { motor_pwm_loss_W: 12.5, inv_cond_W: 3, inv_sw_W: 4, inv_dead_W: 0.5, inv_total_W: 7.5, board_copper_W: 2, tj_C: 71,
  eta_drive_pct: 93.2, eta_shaft_pct: 89.9, p_cont_max_W: 1450 };
const PARTIAL = { ...FULL, tj_C: null, p_cont_max_W: null, motor_pwm_loss_W: null, inv_total_W: null, board_copper_W: null };
// every state the panel can be in
const STATES = {
  sine: ['sine', null],
  pwm: ['pwm', FULL],
  pwmRefused: ['pwm', null],            // outside the envelope, a device limit, the bus range ...
  pwmPartial: ['pwm', PARTIAL],         // a computed point missing some fields
};
const ids = (mode, drv) => [...T.driveRowTiles(mode, drv, 150).map((x) => x.id), ...T.lossTailTiles(mode, drv).map((x) => x.id)];

t('the drive-dependent tiles are the same list, in the same order, in Sine, PWM and every refusal', () => {
  const want = [...T.DRIVE_ROW_IDS, ...T.LOSS_TAIL_IDS];
  assert.deepEqual([...T.LOSS_TAIL_IDS], ['motorLoss', 'invLoss']);            // PWM extra loss, then controller loss
  assert.deepEqual([...T.DRIVE_ROW_IDS], ['tj', 'motorEff', 'pContMax']);
  for (const [name, [mode, drv]] of Object.entries(STATES)) assert.deepEqual(ids(mode, drv), want, name);
  // the label / unit / precision of a tile never depends on the mode either (the width cannot move)
  const shape = (mode, drv) => JSON.stringify([...T.driveRowTiles(mode, drv, 150), ...T.lossTailTiles(mode, drv)]
    .map(({ id, labelKey, tipKey, unit, d }) => ({ id, labelKey, tipKey, unit, d })));
  for (const [name, [mode, drv]] of Object.entries(STATES)) assert.equal(shape(mode, drv), shape('sine', null), name);
});

t('Sine: the PWM extra loss and the controller loss are a real 0 W; the rest of the drive is "—"', () => {
  for (const x of T.lossTailTiles('sine', null)) { assert.equal(x.value, 0, x.id); assert.equal(x.blank, null, x.id); }
  for (const r of T.driveRowTiles('sine', null, 150)) { assert.equal(r.value, null, r.id); assert.equal(r.blank, 'sine', r.id); }
});

t('PWM: the numbers; a refusal or a missing field blanks the value in place', () => {
  const rows = Object.fromEntries(T.driveRowTiles('pwm', FULL, 150).map((r) => [r.id, r.value]));
  assert.deepEqual(rows, { tj: 71, motorEff: null, pContMax: 1.45 });                       // motorEff is given by the caller
  assert.deepEqual(T.lossTailTiles('pwm', FULL).map((x) => [x.id, x.value]), [['motorLoss', 12.5], ['invLoss', 9.5]]);
  for (const r of T.driveRowTiles('pwm', null, 150)) assert.deepEqual([r.value, r.blank], [null, 'refused'], r.id);
  for (const x of T.lossTailTiles('pwm', null)) assert.deepEqual([x.value, x.blank], [null, 'refused'], x.id);
  const part = Object.fromEntries(T.driveRowTiles('pwm', PARTIAL, 150, 90).map((r) => [r.id, r.blank]));
  assert.equal(part.tj, 'missing'); assert.equal(part.pContMax, 'missing'); assert.equal(part.motorEff, null);
  assert.deepEqual(T.lossTailTiles('pwm', PARTIAL).map((x) => x.blank), ['missing', 'missing']);
});

t('T_j is coloured by the device limit only when there is a number and a limit', () => {
  const tj = (v, lim) => T.driveRowTiles('pwm', { ...FULL, tj_C: v }, lim).find((r) => r.id === 'tj').level;
  assert.equal(tj(71, 150), 'ok'); assert.equal(tj(140, 150), 'warn'); assert.equal(tj(71, null), undefined);
});

t('TOTAL LOSS = motor losses + PWM extra + controller loss (Sine: the motor losses alone)', () => {
  assert.equal(T.totalLossShown(74, 'sine', null), 74);
  assert.equal(T.totalLossShown(74, 'pwm', FULL), 74 + 12.5 + 7.5 + 2);      // bridge + board copper are both the controller's
  assert.equal(T.totalLossShown(74, 'pwm', null), null);              // never a total that leaves a part out
  assert.equal(T.totalLossShown(74, 'pwm', PARTIAL), null);
  assert.equal(T.totalLossShown(74, 'pwm', { ...FULL, inv_total_W: null }), null);
  assert.equal(T.totalLossShown(74, 'pwm', { ...FULL, board_copper_W: null }), null);   // a total never leaves the board copper out
  assert.equal(T.lossDensityShown(764, 74, 'sine', null), 764);
  assert.ok(Math.abs(T.lossDensityShown(764, 74, 'pwm', FULL) - 764 * (96 / 74)) < 1e-9);
  assert.equal(T.lossDensityShown(764, 74, 'pwm', null), null);
});

// ── the temperatures row (every cooling mode: propeller air, still air, typed air, liquid) ───
const LIM = { winding_C: 200, magnet_C: 150 };   // the housing is judged against the winding's limit
const TEMPS = { T_winding_C: 120, T_magnet_C: 100, T_housing_C: 60, air_speed_ms: 9.5, h_W_m2K: 61, surface_cm2: 120 };

t('the temperatures are ONE fixed row of four tiles, present with or without data', () => {
  const want = [...T.TEMP_ROW_IDS];
  assert.deepEqual(want, ['tWinding', 'tMagnet', 'tHousing', 'airSpeed']);              // no film-h tile
  assert.deepEqual(T.tempRowTiles(null, LIM).map((x) => x.id), want);
  assert.deepEqual(T.tempRowTiles(TEMPS, LIM).map((x) => x.id), want);
  assert.ok(T.tempRowTiles(null, LIM).every((x) => x.value == null && x.display == null));   // "—" while waiting
  const row = Object.fromEntries(T.tempRowTiles(TEMPS, LIM).map((x) => [x.id, x]));
  assert.equal(row.tWinding.value, 120); assert.equal(row.tWinding.level, 'ok');
  assert.equal(row.airSpeed.value, 9.5); assert.equal(row.filmH, undefined);
  // the housing tile still carries air / h / surface in its spec (the tooltip itself is bare since 2026-10-20)
  assert.deepEqual([row.tHousing.air, row.tHousing.h, row.tHousing.surface], [9.5, 61, 120]);
  const none = T.tempRowTiles(null, LIM).find((x) => x.id === 'tHousing');
  assert.deepEqual([none.air, none.h, none.surface], [null, null, null]);
  for (const l of ['en', 'zh-CN']) {
    assert.equal(loc(l).configurePropeller.filmH, undefined); assert.equal(loc(l).configurePropeller.filmHTip, undefined);
  }
  assert.match(read('components/compare/ConfiguratorPanel.tsx'), /air: t\.air != null \? fmt\(t\.air, 1\)/);
  assert.match(read('components/compare/ConfiguratorPanel.tsx'), /surface: t\.surface != null \? fmt\(t\.surface, 0\)/);
});

t('over the limit a temperature tile reads "> limit" in red, never the absurd number', () => {
  const row = Object.fromEntries(T.tempRowTiles({ ...TEMPS, T_winding_C: 2263, T_magnet_C: 151, T_housing_C: 201 }, LIM).map((x) => [x.id, x]));
  assert.equal(row.tWinding.display, '> 200'); assert.equal(row.tWinding.level, 'bad'); assert.equal(row.tWinding.value, null);
  assert.equal(row.tMagnet.display, '> 150'); assert.equal(row.tMagnet.level, 'bad');
  // the housing has no limit of its own: it is judged against the winding's (it cannot be hotter)
  assert.equal(row.tHousing.display, '> 200'); assert.equal(row.tHousing.level, 'bad'); assert.equal(row.tHousing.limit, 200);
  // within 20 K of the limit: amber; exactly at the limit is still allowed
  const near = Object.fromEntries(T.tempRowTiles({ ...TEMPS, T_winding_C: 185, T_magnet_C: 150, T_housing_C: 185 }, LIM).map((x) => [x.id, x]));
  assert.equal(near.tWinding.level, 'warn'); assert.equal(near.tWinding.display, undefined);
  assert.equal(near.tMagnet.level, 'warn'); assert.equal(near.tMagnet.value, 150);
  assert.equal(near.tHousing.value, 185);
});

t('the still-air (Robotics) estimate lands in the same row: 0 m/s, h 7, absurd numbers read "> limit"', () => {
  // the numbers the removed block used to print for Ø85 L13 in still air — they must never reappear
  const row = Object.fromEntries(T.tempRowTiles({ T_winding_C: 1421, T_magnet_C: 1379, T_housing_C: 1377,
    air_speed_ms: 0, h_W_m2K: 7, surface_cm2: 96 }, LIM).map((x) => [x.id, x]));
  assert.equal(row.tWinding.display, '> 200'); assert.equal(row.tWinding.level, 'bad'); assert.equal(row.tWinding.value, null);
  assert.equal(row.tMagnet.display, '> 150'); assert.equal(row.tMagnet.level, 'bad');
  assert.equal(row.tHousing.display, '> 200'); assert.equal(row.tHousing.level, 'bad');
  assert.equal(row.airSpeed.value, 0);                                       // still air is a real 0, not "—"
  assert.deepEqual([row.tHousing.air, row.tHousing.h, row.tHousing.surface], [0, 7, 96]);
  // a liquid jacket has no cooling air: the COOLING AIR tile is "—", the housing tooltip still carries h
  const liq = Object.fromEntries(T.tempRowTiles({ ...TEMPS, air_speed_ms: null }, LIM).map((x) => [x.id, x]));
  assert.equal(liq.airSpeed.value, null); assert.equal(liq.airSpeed.display, undefined);
  assert.equal(liq.tHousing.air, null); assert.equal(liq.tHousing.h, 61);
});

// ── the panel renders ONLY these for the drive-dependent tiles ───────────────────────────────
t('no results tile in the panel is conditional on the drive (no `drv &&` around a MetricTile)', () => {
  const panel = read('components/compare/ConfiguratorPanel.tsx');
  assert.doesNotMatch(panel, /\{\s*drv\s*&&[^}]*<MetricTile/s);
  assert.doesNotMatch(panel, /\{\s*drv\s*&&\s*variant\s*&&/);
  assert.doesNotMatch(panel, /\{\s*driveOn\s*&&[^}]*<MetricTile/s);
  assert.match(panel, /driveRowTiles\(driveMode, drv,/);
  assert.match(panel, /lossTailTiles\(driveMode, drv\)\.map\(renderSpec\)/);
  // the two drive-dependent loss tiles are the LAST tiles of the loss row, right after the loss density
  const i = panel.indexOf("tx('configure.lossDensity')");
  const j = panel.indexOf('lossTailTiles(driveMode, drv)', i);
  assert.ok(i > 0 && j > i && j - i < 700, 'directly after loss density');
  // a fixed tile width: a number never resizes a tile (the tile itself moved to
  // components/common/ResultTiles.tsx — shared with the Electromagnetic tab, 2026-10-12)
  const tiles = read('components/common/ResultTiles.tsx');
  assert.match(tiles, /width: TILE_W/);
  assert.doesNotMatch(tiles, /minWidth: 108, maxWidth: 168/);
});

t('a refusal is a fixed slot, never a block that shifts the grid', () => {
  const panel = read('components/compare/ConfiguratorPanel.tsx');
  assert.doesNotMatch(panel, /driveRefusals\.map\(\(r\) => \(\s*<Typography/);   // the old inserted lines
  assert.match(panel, /height: 16, overflow: 'hidden'/);                       // the drive refusal slot (one line, owner 2026-10-20)
  assert.match(panel, /height: 16, whiteSpace: 'nowrap'/);                       // the propeller line
});

// ── clean tile titles ────────────────────────────────────────────────────────────────────────
for (const l of ['en', 'zh-CN']) {
  t(`tile titles carry no method / provenance suffix (${l})`, () => {
    const c = loc(l).configure;
    const bad = /3-D|2-D|三维|二维|flux|磁链|rated|额定|analytic|lumped|FEM|有限元|instant|即时|sine|正弦/i;
    for (const k of ['torque', 'kt', 'km', 'tRipple', 'power', 'efficiency', 'totalLoss', 'lossDensity', 'kvNoLoad', 'kmPerMass']) {
      assert.ok(c[k], `${k} exists`);
      assert.doesNotMatch(c[k], bad, `${l} configure.${k} = "${c[k]}"`);
      assert.doesNotMatch(c[k], /\{basis\}/, k);
    }
    // the title row of the panel carries no method tag; the separate "Estimated temperatures" block is gone
    assert.doesNotMatch(c.subtitle, /FEM|有限元/);
    for (const k of ['thermalTitle', 'thermalHelp', 'thermalSub', 'hotWinding', 'hotMagnet', 'hotAdvice', 'hUsed', 'surface'])
      assert.equal(c[k], undefined, `${l} configure.${k}`);
    const p = loc(l).configurePropeller;
    for (const k of ['estimateTitle', 'estimateTip', 'coolingAirSpeed', 'hotWinding', 'hotMagnet'])
      assert.equal(p[k], undefined, `${l} configurePropeller.${k}`);
    // `temperatureLimits` lives again: the Electromagnetic tab's tile panel (PR #139) shows the line
    assert.ok(p.temperatureLimits, `${l} configurePropeller.temperatureLimits`);
    // the method moved into ONE tooltip line
    for (const k of ['ktTip3d', 'ktTipFlux', 'ktTip2d', 'tRippleTip', 'totalLossTip']) assert.ok(c[k], k);
    // the drive tiles are named for what they are in BOTH modes (no "(PWM)" tag on a tile that exists in Sine)
    const d = loc(l).configureDrive;
    assert.doesNotMatch(d.motorEff, /PWM/, `${l} motorEff`);
    assert.equal(d.driveEff, undefined);                                   // the duplicate "eta drive" tile is gone
    assert.equal(d.shaftEffPwm, undefined);
    assert.match(d.invLoss, /Ctrl|Controller|控制器/);
  });
}

t('the source no longer prints the basis, the "instant" tag or the thermal tag', () => {
  const panel = read('components/compare/ConfiguratorPanel.tsx');
  assert.doesNotMatch(panel, /KT_BASIS_LABEL|basis3d|basis2d/);
  assert.doesNotMatch(panel, /<Typography[^>]*>\{tx\('configure\.instant'\)\}/);
  assert.doesNotMatch(panel, /ConfiguratorThermal/);                           // the block file is deleted
});

// ── one contiguous controller group, short titles, no Greek capitals (owner 2026-10-05) ───────
t('ONE contiguous controller group: PWM loss, controller loss, T_j, eta motor, P cont — in that order', () => {
  const group = ['motorLoss', 'invLoss', ...T.DRIVE_ROW_IDS];
  assert.deepEqual(group, ['motorLoss', 'invLoss', 'tj', 'motorEff', 'pContMax']);
  for (const [name, [mode, drv]] of Object.entries(STATES)) {
    assert.deepEqual([...T.lossTailTiles(mode, drv), ...T.driveRowTiles(mode, drv, 150)].map((x) => x.id), group, name);
  }
  const panel = read('components/compare/ConfiguratorPanel.tsx');
  const i = panel.indexOf("tx('configure.lossDensity')");
  const a = panel.indexOf('lossTailTiles(driveMode, drv).map(renderSpec)', i);
  const b = panel.indexOf('driveRowTiles(driveMode, drv,', a);
  const end = panel.indexOf('</Box>', b);
  assert.ok(i > 0 && a > i && b > a && end > b, 'the group follows the loss density');
  // nothing between them closes the row: they are siblings in the SAME flex row
  assert.doesNotMatch(panel.slice(a, b), /<\/Box>/);
  assert.equal((panel.match(/driveRowTiles\(driveMode, drv,/g) || []).length, 1);          // no second, separate drive row
});

for (const [l, max] of [['en', 11], ['zh-CN', 12]]) {
  t(`controller-group titles are short enough for the tile (no ellipsis) and the full name is in the tooltip (${l})`, () => {
    const d = loc(l).configureDrive;
    for (const k of ['motorLoss', 'invLoss', 'tj', 'motorEff', 'pContMax']) {
      assert.ok([...d[k]].length <= max, `${l} configureDrive.${k} = "${d[k]}" (${[...d[k]].length} > ${max})`);
      assert.ok(d[`${k}Tip`] && d[`${k}Tip`].length > d[k].length, `${k}Tip carries the details`);
    }
    assert.match(d.motorEffTip, /controller|控制器/i);                    // says the controller is not in it (the Efficiency tile is the system)
    assert.match(d.motorLossTip, /PWM/);
    // the coefficient titles too: one short line at the tile width, full name in the tooltip
    const c = loc(l).configure;
    const kMax = l === 'en' ? 11 : 12;
    for (const k of ['demagKoef', 'saturationKoef', 'totalKoef']) {
      assert.ok([...c[k]].length <= kMax, `${l} configure.${k} = "${c[k]}" (${[...c[k]].length} > ${kMax})`);
      assert.ok(c[`${k}Tip`] && c[`${k}Tip`].length > c[k].length, `${k}Tip carries the full name`);
    }
  });
}

t('Greek letters are protected from CSS uppercase (eta must not become the Latin-looking H)', async () => {
  const G = await import('../greekLabel.ts');
  assert.deepEqual(G.greekParts('η drive'), [{ text: 'η', greek: true }, { text: ' drive', greek: false }]);
  assert.deepEqual(G.greekParts('驱动效率 η'), [{ text: '驱动效率 ', greek: false }, { text: 'η', greek: true }]);
  assert.deepEqual(G.greekParts('T_j'), [{ text: 'T_j', greek: false }]);
  assert.deepEqual(G.greekParts('ψ_PM · η'), [{ text: 'ψ', greek: true }, { text: '_PM · ', greek: false }, { text: 'η', greek: true }]);
  assert.deepEqual(G.greekParts(''), []);
  // the tile label and the table headers go through it (the tile label lives in
  // components/common/ResultTiles.tsx since the Electromagnetic tab shares it)
  const panel = read('components/compare/ConfiguratorPanel.tsx');
  assert.match(read('components/common/ResultTiles.tsx'), /<GreekLabel text=\{label\} \/>/);
  assert.match(panel, /<GreekLabel text=\{k\.label\} \/>/);
  assert.match(panel, /<GreekLabel text=\{r\.label\} \/>/);
  assert.match(read('components/compare/GreekLabel.tsx'), /textTransform: 'none'/);
});

t('the drive facts are not printed: no variant line under the Drive title, tooltip only', () => {
  const panel = read('components/compare/ConfiguratorPanel.tsx');
  assert.doesNotMatch(panel, /driveFactsLine/);
  // the tooltip is built with join('\n') rather than a template literal: the hard-coded-English
  // scanner (configureLocale.test.mjs) mistakes a `\n${...}` template for JSX text.
  assert.match(panel, /title=\{\[tx\('configureDrive\.transistorTip'\), driveFactsTip\]\.join\('\\n'\)\}/);
});

// ── EFFICIENCY = the SYSTEM (motor + controller), battery -> shaft ───────────────────────────
t('system efficiency = P_shaft / (P_shaft + TOTAL LOSS), from the numbers shown', () => {
  assert.deepEqual(T.systemEfficiency('sine', null, 900, 100), { value: 90, blank: null });
  const pwm = T.systemEfficiency('pwm', FULL, 900, 74);
  assert.ok(Math.abs(pwm.value - 100 * 900 / (900 + 74 + 12.5 + 9.5)) < 1e-12 && pwm.blank === null);
  assert.deepEqual(T.systemEfficiency('pwm', null, 900, 74), { value: null, blank: 'refused' });
  assert.deepEqual(T.systemEfficiency('pwm', { ...FULL, board_copper_W: null }, 900, 74), { value: null, blank: 'missing' });
  assert.equal(T.systemEfficiency('sine', null, 0, 74).value, 0);            // no shaft power: 0, as the model says
});

t('the motor-only efficiency stays in the controller group: the model in Sine, motor losses + PWM extra in PWM', () => {
  assert.equal(T.motorEfficiency('sine', null, 900, 100), 90);
  assert.ok(Math.abs(T.motorEfficiency('pwm', FULL, 900, 74) - 100 * 900 / (900 + 74 + 12.5)) < 1e-12);
  assert.equal(T.motorEfficiency('pwm', null, 900, 74), null);
  const m = (mode, drv, e) => T.driveRowTiles(mode, drv, 150, e).find((x) => x.id === 'motorEff');
  assert.deepEqual([m('sine', null, 90).value, m('sine', null, 90).blank], [90, null]);
  assert.deepEqual([m('pwm', FULL, 89).value, m('pwm', FULL, 89).blank], [89, null]);
  assert.equal(m('pwm', null, null).blank, 'refused'); assert.equal(m('pwm', PARTIAL, null).blank, 'missing');
  assert.equal(m('sine', null, null).blank, 'sine');                          // never an invented number
  assert.deepEqual(T.driveRowTiles('sine', null, 150, 90).map((x) => x.id), [...T.DRIVE_ROW_IDS]);
});

// ── THE TILES ADD UP — real passport, real pilot variants (owner 2026-10-05) ─────────────────
// EFFICIENCY = POWER / (POWER + TOTAL LOSS) with the numbers the tiles show, in Sine AND in PWM, and the
// controller tile carries the board copper (so the loss tiles add up too).
t('the tiles add up: EFFICIENCY = POWER / (POWER + TOTAL LOSS), Sine and PWM, on the real L12 passport', async () => {
  const M = await import('../motorScaling.ts');
  const D = await import('../configuratorDrive.ts');
  const CARD = JSON.parse(readFileSync(join(SRC, 'lib', '__tests__', 'fixtures', 'live_l12_reference_cards.json'), 'utf8')).cat_ciano14_40_new.passport;
  const L12 = JSON.parse(readFileSync(join(SRC, '..', '..', 'config', 'passports', 'CIANO14 40 new', 'L12.json'), 'utf8'));
  const variants = D.usableVariants(L12.pwm_variants);
  assert.ok(variants.length >= 4);
  let checked = 0;
  for (const v of variants) {
    for (const [rpm, I] of [[6000, 30], [13000, 45], [16000, 60], [9000, 80]]) {
      const r = D.readVariant(v, rpm, I);
      if (!r.ok) continue;
      const res = M.scaleMotor(CARD.passport, { N: 7, L_mm: 12, wireH_mm: 0.6, nP: 1, split: 1, I_A: I, rpm }, CARD.poles);
      // what the tiles print
      const P = res.P_mech_W;
      const sineTotal = T.totalLossShown(res.P_loss_W, 'sine', null);
      const sineEff = T.systemEfficiency('sine', null, P, res.P_loss_W).value;
      assert.ok(Math.abs(sineTotal - res.P_loss_W) < 1e-12);
      assert.ok(Math.abs(sineEff - 100 * res.efficiency) < 1e-9, 'Sine EFFICIENCY is the model\'s');
      assert.ok(Math.abs(sineEff - 100 * P / (P + sineTotal)) < 1e-9, `Sine identity at ${v.id} ${rpm}/${I}`);
      const total = T.totalLossShown(res.P_loss_W, 'pwm', r.values);
      const eff = T.systemEfficiency('pwm', r.values, P, res.P_loss_W).value;
      if (total == null) { assert.equal(eff, null); continue; }                 // a point lacking a part shows dashes in BOTH
      assert.ok(Math.abs(eff - 100 * P / (P + total)) < 1e-9, `PWM identity at ${v.id} ${rpm}/${I}`);
      // TOTAL LOSS is exactly the sum of the loss tiles: motor losses + PWM extra + controller (incl. board copper)
      const [extra, ctrl] = T.lossTailTiles('pwm', r.values).map((x) => x.value);
      assert.ok(Math.abs(total - (res.P_loss_W + extra + ctrl)) < 1e-9);
      assert.ok(Math.abs(ctrl - (r.values.inv_cond_W + r.values.inv_sw_W + r.values.inv_dead_W + r.values.board_copper_W)) < 1e-9);
      assert.ok(r.values.board_copper_W > 0, 'the board copper is a real number in the pilot record');
      // the controller and the PWM extra can only lower the efficiency, never raise it
      assert.ok(eff < sineEff, 'PWM system efficiency is below the Sine one');
      checked += 1;
    }
  }
  assert.ok(checked >= 8, `only ${checked} points checked`);
});

t('the panel: EFFICIENCY is the system, the saved column and the ranking follow it, no duplicate eta drive column', () => {
  const panel = read('components/compare/ConfiguratorPanel.tsx');
  assert.match(panel, /<MetricTile label=\{tx\('configure\.efficiency'\)\} value=\{sysEff\.value\}/);
  assert.match(panel, /systemEfficiency\(driveMode, drv, result\.P_mech_W, motorLoss_W\)/);
  assert.match(panel, /motorEfficiency\(driveMode, drv, result\.P_mech_W, motorLoss_W\)/);
  assert.match(panel, /get: \(c\) => \(c\.drive\?\.mode === 'pwm' \? \(c\.drive\.eta_drive_pct \?\? NaN\) : c\.result\.efficiency \* 100\)/);
  assert.doesNotMatch(panel, /columnDriveEff/);
  assert.match(panel, /base=\{baseSysEff \?\? 0\}/);
  for (const l of ['en', 'zh-CN']) {
    const c = loc(l);
    assert.match(c.configure.efficiencyTip, /battery|电池/i);
    assert.match(c.configure.efficiencyTip, /controller|控制器/i);
  }
});

// -- the compact results bar on narrow screens (client-view batch 2026-10-14) ----------------
t('the compact bar applies below 1280 px only', () => {
  assert.equal(T.compactBarApplies(800), true);
  assert.equal(T.compactBarApplies(1279), true);
  assert.equal(T.compactBarApplies(1280), false);
  assert.equal(T.compactBarApplies(1920), false);
  assert.equal(T.compactBarApplies(0), false);
});

t('the compact bar carries torque, power, efficiency, winding and magnet, and follows the numbers', () => {
  const temps = T.tempRowTiles({ T_winding_C: 90, T_magnet_C: 60, T_housing_C: 70, air_speed_ms: 0, h_W_m2K: 5, surface_cm2: 100 },
    { winding_C: 200, magnet_C: 150 });
  const a = T.compactResultTiles({ T_Nm: 5.5, P_kW: 0.57, eff_pct: 81.3, temps });
  assert.deepEqual(a.map((x) => x.id), ['torque', 'power', 'efficiency', 'tWinding', 'tMagnet']);
  assert.equal(a[0].value, 5.5);
  assert.equal(a[3].value, 90);
  // a slider moves: the next call carries the new numbers; an overheating winding reads "> limit" in red
  const hot = T.tempRowTiles({ T_winding_C: 260, T_magnet_C: 60, T_housing_C: 70, air_speed_ms: 0, h_W_m2K: 5, surface_cm2: 100 },
    { winding_C: 200, magnet_C: 150 });
  const b = T.compactResultTiles({ T_Nm: 6.1, P_kW: 0.64, eff_pct: null, temps: hot });
  assert.equal(b[0].value, 6.1);
  assert.equal(b[2].value, null);
  assert.equal(b[3].display, '> 200');
  assert.equal(b[3].level, 'bad');
  // no thermal data yet: every temperature is blank, never invented
  const c = T.compactResultTiles({ T_Nm: 1, P_kW: 1, eff_pct: 90, temps: [] });
  assert.equal(c[3].value, null);
});

// ── the tooltips are bare (owner 2026-10-20: «пиши только столько, чтобы можно было понять, что эта переменная означает»)
const BARE_TIPS = [
  'configureDrive.sineTip',
  'configureDrive.pwmTip',
  'configureDrive.notComputedTip',
  'configureDrive.variantTip',
  'configureDrive.buildScaledTip',
  'configureDrive.noVariantTip',
  'configureDrive.buildTunedTip',
  'configureDrive.refuseEnvelopeTip',
  'configureDrive.refuseMissingPointTip',
  'configureDrive.refuseLimitTip',
  'configureDrive.refuseBusTip',
  'configureDrive.motorLossTip',
  'configureDrive.invLossTip',
  'configureDrive.readNearTip',
  'configureDrive.blankSine',
  'configureDrive.blankRefused',
  'configureDrive.blankMissing',
  'configureDrive.tjTip',
  'configureDrive.tjTipDevice',
  'configureDrive.tjBlankMissing',
  'configureDrive.pContMaxTip',
  'configureDrive.motorEffTip',
  'configureDrive.transistorTip',
  'configureDrive.frequencyTip',
  'configure.torqueTip',
  'configure.powerTip',
  'configure.massFullTip',
  'configure.massPartialTip',
  'configure.massActiveTip',
  'configure.massCopperUnscaledTip',
  'configure.efficiencyTip',
  'configure.tRippleTip',
  'configure.tPerMassTip',
  'configure.pPerMassTip',
  'configure.totalLossTip',
  'configure.ironLossTip',
  'configure.magnetLossTip',
  'configure.copperLossTip',
  'configure.copperLossTipPlain',
  'configure.lossDensityTip',
  'configure.dcBusMinTip',
  'configure.vLinePeakTip',
  'configure.vPhasePeakTip',
  'configure.vLineRmsTip',
  'configure.vPhaseRmsTip',
  'configure.currDensityTip',
  'configure.phaseSectionTip',
  'configure.fillFactorTip',
  'configure.leadCableTip',
  'configure.rPhaseTip',
  'configure.rLineLineTip',
  'configure.ldTip',
  'configure.lqTip',
  'configure.psiPmTip',
  'configure.lqLdTip',
  'configure.kvTip',
  'configure.ktTip',
  'configure.kmTip',
  'configure.kmPerMassTip',
  'configure.demagKoefTip',
  'configure.saturationKoefTip',
  'configure.totalKoefTip',
  'configure.stackCoefficientsTip',
  'configure.lossNotComputedTip',
  'configure.invalidPackTip',
  'configure.refuseSpeedPackTip',
  'configure.resetPackTip',
  'configure.motorVTip',
  'configure.compactBarTip',
  'configure.presetTip',
  'configure.presetShortTimeTip',
  'configure.resetToDefaultTip',
  'configure.restoreFallbackTip',
  'configure.applyTip',
  'configure.generatorNeedsPackTip',
  'configure.cellsSeriesTip',
  'configure.cellVTip',
  'configureLimits.stackLengthTip',
  'configureLimits.turnsPerSlotTip',
  'configureLimits.wireThicknessTip',
  'configureLimits.phaseCurrentTip',
  'configureLimits.speedTip',
  'configureLimits.connectionTip',
  'configureLimits.lHandTip',
  'configureLimits.lDefaultTip',
  'configureLimits.nFitTip',
  'configureLimits.wireFitTip',
  'configureLimits.iMotorPeakTip',
  'configureLimits.iDemagTip',
  'configureLimits.iGridTip',
  'configureLimits.iDutyTip',
  'configureLimits.iBoardMarkTip',
  'configureLimits.iNoLimitTip',
  'configureLimits.rpmEnvelopeTip',
  'configureLimits.rpmKvTip',
  'configureLimits.rpmNoBatteryTip',
  'configureLimits.rpmHandTip',
  'configureLimits.rpmBearingTip',
  'configureLimits.rpmBearingsUnsetTip',
  'configureLimits.rpmBearingsCustomerTip',
  'configure.refuseSpeedBearingTip',
  'configure.refuseSpeedHandTip',
  'configureLimits.connWarnTip',
  'configureLimits.overFitTip',
  'configureLimits.badTurnsTip',
  'configureLimits.atLimitTip',
  'configureLimits.stockTip',
  'configureLimits.rangeMinTip',
  'configureLimits.rangeMaxTip',
  'configureLimits.clearHandTip',
  'configureLimits.resetToReferenceTip',
  'configurePropeller.roboticsTip',
  'configurePropeller.stillAirTip',
  'configurePropeller.propellerAirTip',
  'configurePropeller.coolingPickerTip',
  'configurePropeller.coolingAmbientTip',
  'configurePropeller.pickerTip',
  'configurePropeller.estimatedTip',
  'configurePropeller.ambientTip',
  'configurePropeller.loadTip',
  'configurePropeller.loadPropTip',
  'configurePropeller.loadManualTip',
  'configurePropeller.currentFromPropTip',
  'configurePropeller.zoneTip',
  'configurePropeller.beyondTestedTip',
  'configurePropeller.refuseTorqueTip',
  'configurePropeller.noPropellerTip',
  'configurePropeller.tWindingTip',
  'configurePropeller.tMagnetTip',
  'configurePropeller.tHousingTip',
  'configurePropeller.airSpeedTip',
  'configurePropeller.s1CtrlTip',
  'configurePropeller.curvesTip',
  'configureApplication.tip',
];
for (const l of ['en', 'zh-CN']) {
  t(`every tile / knob / field tooltip is one short phrase (<= 120 characters) (${l})`, () => {
    const d = loc(l);
    for (const k of BARE_TIPS) {
      const v = k.split('.').reduce((a, x) => a?.[x], d);
      assert.equal(typeof v, 'string', `${l} ${k}`);
      assert.ok([...v].length <= 120, `${l} ${k} is ${[...v].length} characters: ${v}`);
      assert.equal(v.split(String.fromCharCode(10)).length, 1, `${l} ${k} is a single line`);
    }
  });
}

t('T_j: what it is, which device and the limit, and why it is empty', () => {
  for (const l of ['en', 'zh-CN']) {
    const d = loc(l).configureDrive;
    assert.match(d.tjTipDevice, /\{device\}/); assert.match(d.tjTipDevice, /\{n\}/); assert.match(d.tjTipDevice, /\{limit\}/);
    assert.ok(d.tjTip && d.tjBlankMissing && d.blankSine && d.blankRefused);
  }
  const panel = read('components/compare/ConfiguratorPanel.tsx');
  assert.match(panel, /configureDrive\.tjTipDevice/);
  assert.match(panel, /t\.id === 'tj' \? 'configureDrive\.tjBlankMissing'/);
  // the T_j tile is blank for exactly these reasons
  const blanks = (mode, drv) => T.driveRowTiles(mode, drv, 150).find((r) => r.id === 'tj').blank;
  assert.equal(blanks('sine', null), 'sine');
  assert.equal(blanks('pwm', null), 'refused');
  assert.equal(blanks('pwm', { ...FULL, tj_C: null }), 'missing');
  assert.equal(blanks('pwm', FULL), null);
});

t('the hover text wraps at about 300 px so it never covers the tiles around it', () => {
  const src = read('components/common/ResultTiles.tsx');
  const w = Number(/TIP_MAX_W = (\d+)/.exec(src)?.[1]);
  assert.ok(w >= 280 && w <= 320, `TIP_MAX_W = ${w}`);
  assert.match(src, /<Tip title=/);                                   // the tile uses it, not a native title
  assert.match(src, /whiteSpace: 'pre-line'/);
});
