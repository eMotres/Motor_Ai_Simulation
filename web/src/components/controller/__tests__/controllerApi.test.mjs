/**
 * The Controller tab's pure helpers, tested without a browser.
 *
 * `polyline` is what draws the waveform card, and a chart that silently
 * collapses is worse than no chart: what is pinned here is that a flat trace
 * still draws (it must not divide by a zero span), that the polyline spans the
 * full width, and that the y axis is INVERTED the way SVG needs — the largest
 * value nearest the top. `fmt`/`pct` are pinned on the one thing a client-
 * facing table must never do: print a made-up number where there is none.
 *
 * The helpers are re-implemented verbatim below rather than imported: the
 * module is TypeScript and reads `import.meta.env`, which `node --test` cannot
 * load (the same reason as common/__tests__/progressStrip.test.mjs). Changing
 * `controllerApi.ts` therefore has to change this file too — and that is the
 * moment someone has to justify the new behaviour.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

/* ── verbatim copies of the shipped helpers ──────────────────────────────── */

function polyline(values, w, h, pad = 2) {
  if (!values.length) return '';
  let lo = Infinity; let hi = -Infinity;
  for (const v of values) { if (v < lo) lo = v; if (v > hi) hi = v; }
  if (!isFinite(lo) || !isFinite(hi)) return '';
  const span = hi - lo || 1;
  const n = values.length;
  return values.map((v, i) => {
    const x = (i / Math.max(n - 1, 1)) * w;
    const y = pad + (1 - (v - lo) / span) * (h - 2 * pad);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
}

const fmt = (v, digits = 1, dash = '—') =>
  (v === null || v === undefined || Number.isNaN(Number(v)))
    ? dash : Number(v).toLocaleString(undefined, { maximumFractionDigits: digits,
                                                   minimumFractionDigits: digits });

const pct = (v, digits = 2) =>
  (v === null || v === undefined) ? '—' : `${(Number(v) * 100).toFixed(digits)} %`;

function statusLine(res, err) {
  if (err) return err;
  return res?.solved_for || null;
}

/* ── polyline ────────────────────────────────────────────────────────────── */

test('polyline spans the full width and inverts y for SVG', () => {
  const pts = polyline([0, 1], 600, 100).split(' ').map(p => p.split(',').map(Number));
  assert.equal(pts.length, 2);
  assert.equal(pts[0][0], 0);
  assert.equal(pts[1][0], 600);
  // 0 is the smallest value, so it sits at the BOTTOM (largest y).
  assert.ok(pts[0][1] > pts[1][1]);
});

test('polyline keeps the trace inside the padded box', () => {
  const pts = polyline([-5, 0, 5], 100, 50, 2).split(' ').map(p => p.split(',').map(Number));
  for (const [, y] of pts) {
    assert.ok(y >= 2 - 1e-9 && y <= 48 + 1e-9, `y ${y} escaped the box`);
  }
});

test('a flat trace still draws — no division by a zero span', () => {
  const s = polyline([7, 7, 7], 300, 60);
  assert.ok(s.length > 0);
  assert.ok(!s.includes('NaN'));
});

test('an empty or non-finite series draws nothing rather than NaN', () => {
  assert.equal(polyline([], 300, 60), '');
  assert.equal(polyline([NaN, NaN], 300, 60), '');
});

test('one sample does not divide by zero', () => {
  const s = polyline([3], 300, 60);
  assert.ok(s.startsWith('0.0,'));
  assert.ok(!s.includes('NaN'));
});

/* ── fmt / pct ───────────────────────────────────────────────────────────── */

test('a missing number prints an em dash, never a zero', () => {
  assert.equal(fmt(null), '—');
  assert.equal(fmt(undefined), '—');
  assert.equal(fmt('not a number'), '—');
  assert.equal(pct(null), '—');
  assert.equal(pct(undefined), '—');
});

test('fmt keeps the digits it is asked for', () => {
  assert.equal(fmt(0, 0), '0');
  assert.equal(fmt(2625.6, 0), '2,626');
  assert.equal(fmt(0.07, 3), '0.070');
});

test('pct is a fraction turned into per cent', () => {
  assert.equal(pct(0.99045), '99.05 %');
  assert.equal(pct(1), '100.00 %');
  assert.equal(pct(0.9771, 3), '97.710 %');
});

/* ── statusLine ──────────────────────────────────────────────────────────── */
// Owner 2026-09-22 production bug: "Error: p_ac_W is required" with no clue
// where to type it. The backend now refuses with one plain sentence instead,
// and a successful solve says which point of the duty it is FOR (the S1
// point beats out the setpoint that was typed on the Simulation tab).

test('an error always wins the status line, even with a stale solved_for', () => {
  const res = { solved_for: 'solved for the duty’s steady point: 10.0 A rms · 1.00 kW in' };
  assert.equal(statusLine(res, 'run the Simulation/coupled solve for this duty first'),
    'run the Simulation/coupled solve for this duty first');
});

test('a solved duty prints which point it was solved for', () => {
  const res = { solved_for: 'solved for the S1 point: 48.6 A rms · 1.99 kW in' };
  assert.equal(statusLine(res, null), 'solved for the S1 point: 48.6 A rms · 1.99 kW in');
});

test('nothing solved yet and no error is a blank status line, never "null"', () => {
  assert.equal(statusLine(null, null), null);
  assert.equal(statusLine({}, null), null);
});
