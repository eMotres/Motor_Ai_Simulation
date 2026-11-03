// node --test - a configuration's OWN cooling (its `thermal_defaults:`, server: thermal_settings.configure_cooling) drives
// Configure's temperature row (2026-11-03: a jacket-cooled generator, h = 1060 W/m²K at 41 °C, was shown in the page's
// default 8 m/s air, every temperature red).  Node >= 22.18 / 24; else skipped.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

let C = null; let T = null; let E = null;
try {
  C = await import('../configureCooling.ts'); T = await import('../configuratorTiles.ts'); E = await import('../thermalEstimate.ts');
} catch { /* old Node */ }
const t = C ? test : test.skip;

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const read = (f) => readFileSync(join(SRC, f), 'utf8');
const loc = (l) => JSON.parse(read(`locales/${l}/controller.json`));

// the page's own laws (components/thermal/api.ts), copied: that module pulls in the auth layer
function airH(v, D) {
  const nu = 1.56e-5, ka = 0.0263, Pr = 0.707;
  if (!(v > 0) || !(D > 0)) return 7;
  const Re = v * D / nu;
  const Nu = 0.3 + (0.62 * Math.sqrt(Re) * Math.cbrt(Pr)) / Math.pow(1 + Math.pow(0.4 / Pr, 2 / 3), 0.25)
    * Math.pow(1 + Math.pow(Re / 282000, 5 / 8), 4 / 5);
  return Math.max(Nu * ka / D, 7);
}
const liqH = (q) => Math.min(8000, Math.max(600, 1800 * Math.pow(Math.max(q, 0.1) / 8, 0.8)));

const D = 0.2;   // housing OD [m]
const FILM = { kind: 'film', h_W_m2K: 1060, ambient_C: 41, source: 'config' };
const GEOM = { statorOD_mm: 200, stackLength_mm: 40, numSlots: 24, slotHeight_mm: 20, slotWidth_mm: 8, insulation_mm: 0.25,
  coreThickness_mm: 8, airGap_mm: 1, magnetOD_mm: 150 };
const BASE = { P_cu_W: 900, P_fe_W: 300, P_mag_W: 60, stator_W: 1200, rotor_W: 60 };
const LIM = { winding_C: 200, magnet_C: 180 };

t('a stated film is the film: h at the coolant temperature, no air over the housing', () => {
  assert.deepEqual(C.configCooling(FILM, D, airH, liqH), { h_Wm2K: 1060, ambient_C: 41, air_speed_ms: null, film: true });
  const liq = C.configCooling({ kind: 'liquid', flow_lpm: 8, fluid: 'water', ambient_C: 30 }, D, airH, liqH);
  assert.deepEqual(liq, { h_Wm2K: liqH(8), ambient_C: 30, air_speed_ms: null, film: true });
  const air = C.configCooling({ kind: 'air', air_speed_ms: 12, ambient_C: 35 }, D, airH, liqH);
  assert.deepEqual(air, { h_Wm2K: airH(12, D), ambient_C: 35, air_speed_ms: 12, film: false });
});

t('no stated cooling (or one that cannot be used) = null: the page keeps its own typed cooling', () => {
  for (const s of [null, undefined, {}, { kind: 'film', h_W_m2K: 0, ambient_C: 41 }, { kind: 'liquid', flow_lpm: 0, ambient_C: 41 },
    { kind: 'robotics', ambient_C: 30 }, { kind: 'film', h_W_m2K: 1060 }]) {
    assert.equal(C.configCooling(s, D, airH, liqH), null, JSON.stringify(s));
  }
});

t('the film gives far lower temperatures than the default typed air (8 m/s, 25 °C)', () => {
  const film = C.configCooling(FILM, D, airH, liqH);
  const air = { h_Wm2K: airH(8, D), ambient_C: 25 };
  const a = E.configurePointThermal('sine', true, BASE, 0, air, GEOM, LIM).thermal;
  const f = E.configurePointThermal('sine', true, BASE, 0, { h_Wm2K: film.h_Wm2K, ambient_C: film.ambient_C }, GEOM, LIM).thermal;
  assert.ok(f.h_Wm2K === 1060 && f.ambient_C === 41);
  assert.ok(f.T_housing_C < a.T_housing_C - 50, `${f.T_housing_C} vs ${a.T_housing_C}`);
  assert.ok(f.T_winding_C < a.T_winding_C && f.T_magnet_C < a.T_magnet_C);
});

t('the row shows "Coolant film" (h, coolant °C) instead of "Cooling air" for a film; unchanged otherwise', () => {
  const TEMPS = { T_winding_C: 120, T_magnet_C: 100, T_housing_C: 60, air_speed_ms: null, h_W_m2K: 1060, surface_cm2: 900, ambient_C: 41 };
  const film = T.tempRowTiles(TEMPS, LIM, true);
  assert.deepEqual(film.map((x) => x.id), [...T.TEMP_ROW_IDS_FILM]);
  const f = film[3];
  assert.equal(f.id, 'coolantFilm'); assert.equal(f.value, 1060); assert.equal(f.unit, 'W/m²K, 41 °C'); assert.equal(f.d, 0);
  assert.ok(!film.some((x) => x.id === 'airSpeed'));
  // the default (no film) is exactly the old row
  assert.deepEqual(T.tempRowTiles({ ...TEMPS, air_speed_ms: 8 }, LIM).map((x) => x.id), [...T.TEMP_ROW_IDS]);
  assert.deepEqual(T.tempRowTiles({ ...TEMPS, air_speed_ms: 8 }, LIM), T.tempRowTiles({ ...TEMPS, air_speed_ms: 8 }, LIM, false));
  assert.equal(T.tempRowTiles(null, LIM, true)[3].value, null);        // "—" while waiting
  for (const l of ['en', 'zh-CN']) {
    assert.equal(typeof loc(l).configurePropeller.coolantFilm, 'string');
    assert.equal(typeof loc(l).configurePropeller.coolantFilmTip, 'string');
  }
});

t('Configure reads the preset\'s cooling before the typed one and hides the typed controls for it', () => {
  const panel = read('components/compare/ConfiguratorPanel.tsx');
  assert.match(panel, /configCooling\(basePreset\?\.cooling_defaults/);
  assert.match(panel, /if \(cfgCooling\) return cfgCooling;\s*if \(coolPayload\)/);
  assert.match(panel, /!roboticsOnly && !cfgCooling && \(\s*<CoolingControls/);
  assert.match(panel, /tLimits, !!cfgCooling\?\.film\)/);
});
