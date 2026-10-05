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

const FULL = { motor_pwm_loss_W: 12.5, inv_cond_W: 3, inv_sw_W: 4, inv_dead_W: 0.5, inv_total_W: 7.5, tj_C: 71,
  eta_drive_pct: 93.2, eta_shaft_pct: 89.9, p_cont_max_W: 1450 };
const PARTIAL = { ...FULL, tj_C: null, p_cont_max_W: null, motor_pwm_loss_W: null, inv_total_W: null };
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
  assert.deepEqual([...T.DRIVE_ROW_IDS], ['tj', 'driveEff', 'shaftEffPwm', 'pContMax']);
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
  assert.deepEqual(rows, { tj: 71, driveEff: 93.2, shaftEffPwm: 89.9, pContMax: 1.45 });
  assert.deepEqual(T.lossTailTiles('pwm', FULL).map((x) => [x.id, x.value]), [['motorLoss', 12.5], ['invLoss', 7.5]]);
  for (const r of T.driveRowTiles('pwm', null, 150)) assert.deepEqual([r.value, r.blank], [null, 'refused'], r.id);
  for (const x of T.lossTailTiles('pwm', null)) assert.deepEqual([x.value, x.blank], [null, 'refused'], x.id);
  const part = Object.fromEntries(T.driveRowTiles('pwm', PARTIAL, 150).map((r) => [r.id, r.blank]));
  assert.equal(part.tj, 'missing'); assert.equal(part.pContMax, 'missing'); assert.equal(part.driveEff, null);
  assert.deepEqual(T.lossTailTiles('pwm', PARTIAL).map((x) => x.blank), ['missing', 'missing']);
});

t('T_j is coloured by the device limit only when there is a number and a limit', () => {
  const tj = (v, lim) => T.driveRowTiles('pwm', { ...FULL, tj_C: v }, lim).find((r) => r.id === 'tj').level;
  assert.equal(tj(71, 150), 'ok'); assert.equal(tj(140, 150), 'warn'); assert.equal(tj(71, null), undefined);
});

t('TOTAL LOSS = motor losses + PWM extra + controller loss (Sine: the motor losses alone)', () => {
  assert.equal(T.totalLossShown(74, 'sine', null), 74);
  assert.equal(T.totalLossShown(74, 'pwm', FULL), 74 + 12.5 + 7.5);
  assert.equal(T.totalLossShown(74, 'pwm', null), null);              // never a total that leaves a part out
  assert.equal(T.totalLossShown(74, 'pwm', PARTIAL), null);
  assert.equal(T.totalLossShown(74, 'pwm', { ...FULL, inv_total_W: null }), null);
  assert.equal(T.lossDensityShown(764, 74, 'sine', null), 764);
  assert.ok(Math.abs(T.lossDensityShown(764, 74, 'pwm', FULL) - 764 * (94 / 74)) < 1e-9);
  assert.equal(T.lossDensityShown(764, 74, 'pwm', null), null);
});

// ── the temperatures row (propeller-cooled machines) ─────────────────────────────────────────
const LIM = { winding_C: 180, magnet_C: 150 };
const TEMPS = { T_winding_C: 120, T_magnet_C: 100, T_housing_C: 60, air_speed_ms: 9.5, h_W_m2K: 61 };

t('the temperatures are ONE fixed row of five tiles, present with or without data', () => {
  const want = [...T.TEMP_ROW_IDS];
  assert.deepEqual(want, ['tWinding', 'tMagnet', 'tHousing', 'airSpeed', 'filmH']);
  assert.deepEqual(T.tempRowTiles(null, LIM).map((x) => x.id), want);
  assert.deepEqual(T.tempRowTiles(TEMPS, LIM).map((x) => x.id), want);
  assert.ok(T.tempRowTiles(null, LIM).every((x) => x.value == null && x.display == null));   // "—" while waiting
  const row = Object.fromEntries(T.tempRowTiles(TEMPS, LIM).map((x) => [x.id, x]));
  assert.equal(row.tWinding.value, 120); assert.equal(row.tWinding.level, 'ok');
  assert.equal(row.airSpeed.value, 9.5); assert.equal(row.filmH.value, 61);
});

t('over the limit a temperature tile reads "> limit" in red, never the absurd number', () => {
  const row = Object.fromEntries(T.tempRowTiles({ ...TEMPS, T_winding_C: 2263, T_magnet_C: 151 }, LIM).map((x) => [x.id, x]));
  assert.equal(row.tWinding.display, '> 180'); assert.equal(row.tWinding.level, 'bad'); assert.equal(row.tWinding.value, null);
  assert.equal(row.tMagnet.display, '> 150'); assert.equal(row.tMagnet.level, 'bad');
  // within 20 K of the limit: amber; exactly at the limit is still allowed
  const near = Object.fromEntries(T.tempRowTiles({ ...TEMPS, T_winding_C: 165, T_magnet_C: 150 }, LIM).map((x) => [x.id, x]));
  assert.equal(near.tWinding.level, 'warn'); assert.equal(near.tWinding.display, undefined);
  assert.equal(near.tMagnet.level, 'warn'); assert.equal(near.tMagnet.value, 150);
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
  // a fixed tile width: a number never resizes a tile
  assert.match(panel, /width: TILE_W/);
  assert.doesNotMatch(panel, /minWidth: 108, maxWidth: 168/);
});

t('a refusal is a fixed slot, never a block that shifts the grid', () => {
  const panel = read('components/compare/ConfiguratorPanel.tsx');
  assert.doesNotMatch(panel, /driveRefusals\.map\(\(r\) => \(\s*<Typography/);   // the old inserted lines
  assert.match(panel, /height: 34, overflow: 'hidden'/);
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
    // the title row of the panel and of the thermal block carry no method tag either
    assert.doesNotMatch(c.subtitle, /FEM|有限元/);
    assert.doesNotMatch(c.thermalTitle, /analytic|解析|lumped|集总/i);
    assert.equal(c.thermalSub, undefined);
    // the method moved into ONE tooltip line
    for (const k of ['ktTip3d', 'ktTipFlux', 'ktTip2d', 'tRippleTip', 'totalLossTip']) assert.ok(c[k], k);
    // the drive tiles are named for what they are in BOTH modes (no "(PWM)" tag on a tile that exists in Sine)
    const d = loc(l).configureDrive;
    assert.doesNotMatch(d.shaftEffPwm, /PWM/, `${l} shaftEffPwm`);
    assert.match(d.invLoss, /Controller|控制器/);
  });
}

t('the source no longer prints the basis, the "instant" tag or the thermal tag', () => {
  const panel = read('components/compare/ConfiguratorPanel.tsx');
  assert.doesNotMatch(panel, /KT_BASIS_LABEL|basis3d|basis2d/);
  assert.doesNotMatch(panel, /<Typography[^>]*>\{tx\('configure\.instant'\)\}/);
  assert.doesNotMatch(read('components/compare/ConfiguratorThermal.tsx'), /thermalSub/);
});

t('the drive facts are not printed: no variant line under the Drive title, tooltip only', () => {
  const panel = read('components/compare/ConfiguratorPanel.tsx');
  assert.doesNotMatch(panel, /driveFactsLine/);
  assert.match(panel, /title=\{`\$\{tx\('configureDrive\.transistorTip'\)\}\\n\$\{driveFactsTip\}`\}/);
});
