// node --test - Configure never blanks on a knob move and the load chart sets the speed (owner 2026-10-19).
//   * a moved build keeps PWM: the loaded build's extra loss carried with the ripple flux (configuratorDrive);
//   * the load chart: a click / drag sets the speed under the pointer, and a request in flight is "loading", never
//     "no data" (propellerLoadChart);
//   * the wiring: the chart's pointer handlers call onRpmChange, the panel passes setRpm, setRpm writes knobs.rpm and
//     drops the held rated speed, and no build-moved refusal blanks the tiles any more.
// Imports lib/configuratorDrive.ts + lib/propellerLoadChart.ts (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

let D = null; let L = null;
try { D = await import('../configuratorDrive.ts'); L = await import('../propellerLoadChart.ts'); } catch { /* old Node */ }
const t = D && L ? test : test.skip;
const panel = readFileSync(new URL('../../components/compare/ConfiguratorPanel.tsx', import.meta.url), 'utf8');
const chart = readFileSync(new URL('../../components/compare/PropellerLoadChart.tsx', import.meta.url), 'utf8');

t('a moved build carries the PWM extra loss with the ripple flux: (N_s0 / N_s)^2 x (L0 / L)', () => {
  const loaded = { N: 7, L_mm: 12, nP: 1 };
  assert.equal(D.pwmBuildScale(loaded, loaded), 1);
  assert.ok(Math.abs(D.pwmBuildScale(loaded, { N: 7, L_mm: 13, nP: 1 }) - 12 / 13) < 1e-12);    // 40 L12 -> 13 mm
  assert.ok(Math.abs(D.pwmBuildScale(loaded, { N: 14, L_mm: 12, nP: 1 }) - 0.25) < 1e-12);      // twice the turns
  assert.ok(Math.abs(D.pwmBuildScale(loaded, { N: 14, L_mm: 12, nP: 2 }) - 1) < 1e-12);         // ... in two paths
  assert.equal(D.pwmBuildScale(loaded, { N: 0, L_mm: 12, nP: 1 }), 1);                            // nonsense: unchanged
  const r = { motor_pwm_loss_W: 5, inv_cond_W: 1, inv_sw_W: 2, inv_dead_W: 0.1, inv_total_W: 3.1, board_copper_W: 0.5,
    tj_C: 60, eta_drive_pct: null, eta_shaft_pct: null, p_cont_max_W: null };
  const s = D.transferPwmReading(r, 12 / 13);
  assert.ok(Math.abs(s.motor_pwm_loss_W - 5 * 12 / 13) < 1e-12);
  assert.equal(s.inv_total_W, 3.1);                                  // the bridge's own losses stay the variant's
});

t('the load chart maps a click to the speed under the pointer, inside the plot only', () => {
  const w = 600, x0 = L.PLOT_LEFT_PX, x1 = w - L.PLOT_RIGHT_PX;
  assert.equal(L.rpmAtChartX(x0, w, 2000, 14000), 2000);
  assert.equal(L.rpmAtChartX(x1, w, 2000, 14000), 14000);
  assert.equal(L.rpmAtChartX((x0 + x1) / 2, w, 2000, 14000), 8000);
  assert.equal(L.rpmAtChartX(x0 - 5, w, 2000, 14000), null);         // on the y axis: nothing
  assert.equal(L.rpmAtChartX(100, 0, 2000, 14000), null);
  assert.equal(L.rpmAtChartX(100, w, 5000, 5000), null);
});

t('a request in flight is "loading"; "no data" only when every answer came back empty', () => {
  assert.equal(L.chartState(['a'], {}), 'loading');
  assert.equal(L.chartState(['a', 'b'], { a: null }), 'loading');
  assert.equal(L.chartState(['a', 'b'], { a: null, b: [] }), 'nodata');
  assert.equal(L.chartState(['a', 'b'], { a: undefined, b: [{ rpm: 1 }] }), 'data');
});

t('the wiring: chart pointer -> onRpmChange -> setRpm -> knobs.rpm; a moved build is never a refusal', () => {
  assert.match(chart, /onMouseDown=\{\(_s: unknown, ev: \{ clientX\?: number \}\) => \{ dragging\.current = true; setFrom\(ev\); \}\}/);
  assert.match(chart, /const r = rpmAtChartX\(ev\.clientX - rect\.left, rect\.width, xMin, xMax\);\s*if \(r != null\) onRpmChange\(r\);/);
  assert.match(chart, /state === 'loading' \?/);
  assert.match(panel, /<PropellerLoadChart [^>]*onRpmChange=\{setRpm\}/s);
  assert.match(panel, /const setRpm = \(v: number\) => \{\s*clampToS1Ref\.current = null;[\s\S]*?setKnobs\(\(s\) => \(\{ \.\.\.s, rpm: v \}\)\);/);
  assert.doesNotMatch(panel, /tx\('configureDrive\.buildTuned'\)/);     // the refusal that blanked every tile is gone
  assert.match(panel, /transferPwmReading\(r\.values, pwmScale\)/);
});
