// node --test — a die with TWO coolings (owner 2026-10-05, the Ø85 CIANO28 85 20SW1200: a robot joint
// in still air OR a propeller drive): the selector shows only when a die offers more than one, the
// pick is remembered per machine, a single-option die behaves exactly as before, and the still-air
// film mirrors the backend's cooling_models.outer_still.
import test from 'node:test';
import assert from 'node:assert/strict';

let P = null, E = null, T = null;
try {
  P = await import('../configuratorPropeller.ts');
  E = await import('../thermalEstimate.ts');
  T = await import('../configuratorTiles.ts');
} catch { /* old Node */ }
const t = P ? test : test.skip;

const D85 = { restricted: true, cooling_options: ['robotics', 'propeller_air'], die: 'CIANO28 85 20SW1200', config: 'L13',
  propellers: ['tmotor_g36x11_5', 'tmotor_g40x13_1'] };
const D40 = { restricted: true, cooling_options: ['propeller_air'], die: 'CIANO14 40 new', config: 'L12' };
const FREE = { restricted: false, cooling_options: null };

t('selector only for a die with more than one cooling', () => {
  assert.equal(P.showCoolingSelector(D85), true);
  assert.equal(P.showCoolingSelector(D40), false);
  assert.equal(P.showCoolingSelector(FREE), false);
  assert.equal(P.showCoolingSelector(null), false);
});

t('the first listed option is in force until the user picks; a pick the die does not offer is ignored', () => {
  assert.equal(P.effectiveCooling(D85, {}), 'robotics');
  assert.equal(P.effectiveCooling(D85, { cooling: 'propeller_air' }), 'propeller_air');
  assert.equal(P.effectiveCooling(D85, { cooling: 'liquid' }), 'robotics');
  assert.equal(P.effectiveCooling(FREE, { cooling: 'robotics' }), null);
  assert.equal(P.isRoboticsCooled(D85, {}), true);
  assert.equal(P.isPropellerCooled(D85, {}), false);
  assert.equal(P.isPropellerCooled(D85, { cooling: 'propeller_air' }), true);
});

t('a propeller-only die is still propeller-cooled with no choice at all (the Ø40 behaviour)', () => {
  assert.equal(P.isPropellerCooled(D40), true);
  assert.equal(P.isPropellerCooled(D40, { cooling: 'robotics' }), true);   // not offered: ignored
  assert.equal(P.isRoboticsCooled(D40), false);
  assert.equal(P.isPropellerCooled(FREE), false);
});

t('the pick is remembered per machine and survives the other fields', () => {
  let raw = P.writeCoolChoice(null, 'cat:85', { cooling: 'propeller_air' });
  raw = P.writeCoolChoice(raw, 'cat:85', { propId: 'tmotor_g40x13_1' });
  assert.deepEqual(P.readCoolChoice(raw, 'cat:85'), { cooling: 'propeller_air', propId: 'tmotor_g40x13_1' });
  assert.deepEqual(P.readCoolChoice(raw, 'cat:40'), {});
});

t('still-air film = Churchill–Chu + radiation (backend outer_still, Ø85 housing)', () => {
  // backend docstring: on the Ø85 joint at ΔT 60 K over 40 °C air, h_conv ≈ 6 and h_rad ≈ 8.3
  const f = E.outerStillH(100, 40, 0.085, 0.9);
  assert.ok(Math.abs(f.h_conv - 6.0) < 0.8, `h_conv ${f.h_conv}`);
  assert.ok(Math.abs(f.h_rad - 8.3) < 0.3, `h_rad ${f.h_rad}`);
  assert.equal(E.outerStillH(100, 40, 0.085, 0).h_rad, 0);           // emissivity 0: convection only
});

t('the still-air estimate is self-consistent at its own housing temperature and hotter than a breeze', () => {
  const g = { statorOD_mm: 85, stackLength_mm: 13, numSlots: 24, slotHeight_mm: 7.4, slotWidth_mm: 3.6,
    insulation_mm: 0.05, coreThickness_mm: 2.4, airGap_mm: 0.3, magnetOD_mm: 84.4 };
  const l = { P_cu_W: 20, P_fe_W: 1, P_mag_W: 0.5 };
  const s = E.estimateThermalStill(g, l, 40);
  const h = E.outerStillH(s.T_housing_C, 40, 0.085).h;
  assert.ok(Math.abs(h - s.h_Wm2K) / h < 0.01, `h ${h} vs ${s.h_Wm2K}`);
  const air = E.estimateThermal(g, l, { h_Wm2K: 60, ambient_C: 40 });
  assert.ok(s.T_winding_C > air.T_winding_C);
});

t('temperature tiles take the still-air tips when the robot-joint cooling is in force', () => {
  const row = T.tempRowTiles({ T_winding_C: 90, T_magnet_C: 70, T_housing_C: 80, air_speed_ms: 0, h_W_m2K: 14 },
    { winding_C: 180, magnet_C: 150 }, true);
  assert.equal(row.find((x) => x.id === 'tHousing').tipKey, 'configureCooling.tHousingTip');
  assert.equal(row.find((x) => x.id === 'airSpeed').tipKey, 'configureCooling.airSpeedTip');
  const prop = T.tempRowTiles(null, { winding_C: 180, magnet_C: 150 });
  assert.equal(prop.find((x) => x.id === 'tHousing').tipKey, 'configurePropeller.tHousingTip');
});

t('the panel: a cooling selector for a two-cooling die, the still-air row, the Thermal block only without a cooling row', async () => {
  const { readFileSync } = await import('node:fs');
  const { join, dirname } = await import('node:path');
  const { fileURLToPath } = await import('node:url');
  const panel = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'components', 'compare', 'ConfiguratorPanel.tsx'), 'utf8');
  assert.match(panel, /\{showCoolingSelector\(ctx\?\.cooling\) && \(/);
  assert.match(panel, /updateCool\(\{ cooling: v \}\)/);
  assert.match(panel, /estimateThermalStill\(thermalGeom/);
  assert.match(panel, /\{ctxDone && !tempOn && \(/);
});
