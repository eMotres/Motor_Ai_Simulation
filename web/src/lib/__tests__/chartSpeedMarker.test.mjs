// node --test - the SPEED MARKER on the Configure load chart (owner 2026-10-19: «нужно сделать при нажатии на графике, чтобы
// оставалась полоска в точке нажатия и было видно, где эта точка; при нажатии на другое место она должна смещаться в другое
// место»).  The marker is always the CURRENT speed (knobs.rpm): a click sets the speed there and the marker follows; a second
// click moves it; the slider, a typed rpm and a preset load move it the same way.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

let C = null;
try { C = await import('../propellerLoadChart.ts'); } catch { /* old Node */ }
const t = C ? test : test.skip;
const chart = readFileSync(new URL('../../components/compare/PropellerLoadChart.tsx', import.meta.url), 'utf8');
const panel = readFileSync(new URL('../../components/compare/ConfiguratorPanel.tsx', import.meta.url), 'utf8');

const rows = (k) => [3000, 6000, 9000, 12000, 15000].map((rpm, i) => ({ rpm, thrust_N: k * (i + 1) * 9.80665, ok: rpm <= 12000 }));
const line = (id, color, k) => ({ id, color, ...C.loadLine(rows(k), 'thrust') });

/** the chart's own loop: a click at pixel x sets the speed (onRpmChange -> knobs.rpm), the marker reads knobs.rpm */
function chartLoop() {
  const lines = [line('hq1050', '#3b82f6', 0.2), line('fpv10x5', '#f59e0b', 0.3)];
  const W = 600;
  const knobs = { rpm: 9000 };
  const click = (px) => { const r = C.rpmAtChartX(px, W, 3000, 15000); if (r != null) knobs.rpm = r; };
  const slide = (rpm) => { knobs.rpm = rpm; };
  return { lines, W, knobs, click, slide, marker: () => C.speedMarker(lines, knobs.rpm) };
}

t('a click leaves a marker at that speed, with a dot on every line there', () => {
  const s = chartLoop();
  s.click(C.PLOT_LEFT_PX + 0.25 * (s.W - C.PLOT_LEFT_PX - C.PLOT_RIGHT_PX));       // a quarter across: 6000 rpm
  const m = s.marker();
  assert.equal(m.x, 6000);
  assert.deepEqual(m.dots.map((d) => d.id), ['hq1050', 'fpv10x5']);
  assert.ok(Math.abs(m.dots[0].y - 0.4) < 1e-9 && Math.abs(m.dots[1].y - 0.6) < 1e-9);
});

t('a second click moves the marker; the slider / a typed rpm moves it too', () => {
  const s = chartLoop();
  s.click(C.PLOT_LEFT_PX + 0.25 * (s.W - C.PLOT_LEFT_PX - C.PLOT_RIGHT_PX));
  assert.equal(s.marker().x, 6000);
  s.click(C.PLOT_LEFT_PX + 0.75 * (s.W - C.PLOT_LEFT_PX - C.PLOT_RIGHT_PX));
  assert.equal(s.marker().x, 12000);
  s.slide(7500);
  const m = s.marker();
  assert.equal(m.x, 7500);
  assert.ok(Math.abs(m.dots[0].y - 0.5) < 1e-9);                                       // between the 6000 and 9000 rows
  // a click outside the plot area changes nothing; a speed off the axis shows no marker
  s.click(5);
  assert.equal(s.marker().x, 7500);
  s.slide(20000);
  assert.equal(s.marker(), null);
  assert.equal(C.speedMarker(s.lines, null), null);
  assert.equal(C.speedMarker([], 6000), null);
});

t('the chart draws the marker from knobs.rpm, solid and labelled, distinct from the dashed S1 lines', () => {
  assert.match(panel, /<PropellerLoadChart [^>]*speedRpm=\{knobs\.rpm\}[^>]*onRpmChange=\{setRpm\}/s);
  assert.match(chart, /const marker = speedMarker\(lines, speedRpm\)/);
  assert.match(chart, /\{marker && <ReferenceLine x=\{marker\.x\} stroke="var\(--text-1\)" strokeWidth=\{1\.25\}/);
  const markerLine = chart.slice(chart.indexOf('{marker && <ReferenceLine'), chart.indexOf('{marker?.dots.map'));
  assert.ok(!/strokeDasharray/.test(markerLine), 'the speed marker is solid');
  assert.match(chart, /<ReferenceDot key=\{`speed-\$\{d\.id\}`\} x=\{marker\.x\} y=\{d\.y\}/);
  // the click / drag still sets the speed; the hover tooltip stays
  assert.match(chart, /onMouseDown=\{\(_s: unknown, ev: \{ clientX\?: number \}\) => \{ dragging\.current = true; setFrom\(ev\); \}\}/);
  assert.match(chart, /<RcTooltip /);
});
