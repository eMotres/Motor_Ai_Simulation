/**
 * `formatProgressLine`, tested without a browser.
 *
 * The strip is now shared by Simulation, Mechanical and Thermal (2026-09-07), so
 * a clamp lost in a refactor no longer misprints one tab — it misprints three.
 * What is pinned here is the SHAPE the helper must keep: the counter never runs
 * past its total, a total of 0 never divides, an unknown kind never leaks a raw
 * identifier at the user, and `frac` only steps in when there is no step count.
 *
 * The function is re-implemented verbatim below rather than imported: the module
 * is TypeScript and lives beside components that pull in `import.meta.env`,
 * which `node --test` cannot load (same reason as thermal/coolingPayload.test).
 * Changing `progressLine.ts` therefore has to change this file too — and that is
 * the moment someone has to justify the new behaviour.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

/* ── verbatim copy of the shipped helper ─────────────────────────────────── */

function formatProgressLine(p, unit = 'points', kindLabels) {
  const total = Math.max(1, Number(p.total) || 0);
  const step = Math.min(Math.max(Number(p.step) || 0, 0), total);
  const byStep = (100 * step) / total;
  const pct = (!Number(p.total) && typeof p.frac === 'number')
    ? Math.min(100, Math.max(0, p.frac * 100))
    : Math.min(100, byStep);
  const named = p.kind ? kindLabels?.[p.kind] : undefined;
  return { prefix: named ? `${named} ·` : 'Computing', step, total, unit, pct };
}

/* ── the counter ─────────────────────────────────────────────────────────── */

test('a plain running solve prints its step over its total', () => {
  const l = formatProgressLine({ step: 37, total: 144 });
  assert.equal(l.prefix, 'Computing');
  assert.equal(l.step, 37);
  assert.equal(l.total, 144);
  assert.equal(l.unit, 'points');
  assert.ok(Math.abs(l.pct - 25.694) < 0.01);
});

test('a step past the total is clamped, not printed as 150 / 144', () => {
  // The backend counts the settle frames too; an off-by-one there must not
  // produce a bar wider than the strip.
  const l = formatProgressLine({ step: 150, total: 144 });
  assert.equal(l.step, 144);
  assert.equal(l.pct, 100);
});

test('a total of 0 never divides by zero', () => {
  const l = formatProgressLine({ step: 0, total: 0 });
  assert.equal(l.total, 1);
  assert.equal(l.pct, 0);
  assert.ok(Number.isFinite(l.pct));
});

test('a negative or missing step reads as 0', () => {
  assert.equal(formatProgressLine({ step: -3, total: 10 }).step, 0);
  assert.equal(formatProgressLine({ total: 10 }).step, 0);
});

/* ── the fraction fallback ───────────────────────────────────────────────── */

test('frac drives the bar only when there is no step count', () => {
  // A nonlinear contact solve does not know its iteration count in advance.
  const noTotal = formatProgressLine({ step: 0, total: 0, frac: 0.42 });
  assert.equal(noTotal.pct, 42);
  // With a real total the step count wins — the transient's bar must not change
  // behaviour because a backend started sending frac as well.
  const withTotal = formatProgressLine({ step: 72, total: 144, frac: 0.9 });
  assert.equal(withTotal.pct, 50);
});

test('a frac outside 0…1 is clamped', () => {
  assert.equal(formatProgressLine({ step: 0, total: 0, frac: 1.4 }).pct, 100);
  assert.equal(formatProgressLine({ step: 0, total: 0, frac: -0.2 }).pct, 0);
});

/* ── naming the running solve ────────────────────────────────────────────── */

const MECH = {
  rotor_stress: 'Rotor stress',
  modes: 'Modal analysis',
  critical_speeds: 'Critical speeds',
  mesh: 'Mesh build',
};

test('a known kind names the solve', () => {
  const l = formatProgressLine({ step: 4, total: 12, kind: 'rotor_stress' }, 'steps', MECH);
  assert.equal(l.prefix, 'Rotor stress ·');
  assert.equal(l.unit, 'steps');
});

test('an unknown kind falls back to Computing, never a raw identifier', () => {
  const l = formatProgressLine({ step: 1, total: 3, kind: 'flux_weakening_probe' }, 'steps', MECH);
  assert.equal(l.prefix, 'Computing');
});

test('no kind and no labels is the Simulation tab, unchanged', () => {
  const l = formatProgressLine({ step: 1, total: 3 }, 'points', undefined);
  assert.equal(l.prefix, 'Computing');
});

/* ── the queue line (backend Stage 4) ────────────────────────────────────── */
/* Same rule as above: a verbatim copy of the shipped helper, so changing
 * `progressLine.ts` forces someone to justify the change here too. */

function formatQueueLine(p) {
  if (!p.queued) return null;
  const n = Number(p.position) || 0;
  return n > 0 ? `Queued · position ${n}` : 'Queued';
}

test('a run that is not queued prints no queue line', () => {
  assert.equal(formatQueueLine({ running: true, step: 3, total: 9 }), null);
  assert.equal(formatQueueLine({}), null);
});

test('a queued run names its place', () => {
  assert.equal(formatQueueLine({ queued: true, position: 3 }),
    'Queued · position 3');
});

test('queued with no position still says it is queued', () => {
  // The backend omits `position` for a job it has a record of but no place for
  // (one already admitted between the poll and the answer).  "Queued" alone is
  // still the truth; "position 0" would not be.
  assert.equal(formatQueueLine({ queued: true }), 'Queued');
  assert.equal(formatQueueLine({ queued: true, position: 0 }), 'Queued');
});

/* ── the Simulation tab's transient-vs-coupled visibility rule ──────────── */
/* Same rule as above: a verbatim copy of the shipped helper, so changing
 * `progressLine.ts` forces someone to justify the change here too.  Pinned
 * 2026-09-21: the owner saw two identical progress bars — one "steps", one
 * "points" — because both the coupled orchestrator strip and the plain
 * transient strip were mounted unconditionally while a coupled run's EM
 * sub-step IS the transient solve. */

function showsTransientStrip(coupled, coupledStripActive) {
  return !(coupled && coupledStripActive);
}

test('a plain (non-coupled) Simulation run always keeps its own strip', () => {
  assert.equal(showsTransientStrip(false, false), true);
  // Even if some stray "active" signal were true, the toggle being off means
  // there is no orchestrator strip to defer to.
  assert.equal(showsTransientStrip(false, true), true);
});

test('coupled toggle on but the orchestrator strip not (yet) running: transient shows', () => {
  assert.equal(showsTransientStrip(true, false), true);
});

test('coupled toggle on and the orchestrator strip running: transient hides', () => {
  // This is the exact bug: both endpoints report the same running solve at
  // once, so only the orchestrator's strip (it names "S1 verification 1/2")
  // may show.
  assert.equal(showsTransientStrip(true, true), false);
});
