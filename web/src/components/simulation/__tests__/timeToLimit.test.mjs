// node --test — the ONE line the coupled result shows when the machine is past
// a limit, and the duration words behind it (coupledApi.timeToLimitLine /
// fmtSecs / timeToLimitTip).
//
// The repo's convention for node tests: `node --test` cannot load the TS
// modules, so the pure functions under test are re-stated here verbatim and
// kept in sync — see couplingLine.test.mjs, which does the same for the same
// reason.
//
// WHAT IS WORTH TESTING is not string formatting; it is three rules the owner
// asked for on 2026-09-17 («если где-то выходим за лимиты, нужно посчитать
// время, за какое мотор проработает до этого лимита»):
//
//   1. a point INSIDE every limit shows NOTHING — there is no time to a limit
//      it respects, and printing one would invite planning around a number that
//      is not a constraint (the same rule as "0 W" for a machine with no
//      bearings, one line above this one on the same card);
//   2. a step response that never reaches the limit SAYS SO rather than being
//      extrapolated into a number nobody may quote;
//   3. the warm start is optional and its absence is silent, not a "—".
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

// ── verbatim from coupledApi.ts ───────────────────────────────────────────
function fmtSecs(s) {
  if (s == null || !Number.isFinite(s) || s < 0) return '—';
  if (s < 10) return `${s.toFixed(1)} s`;
  if (s < 60) return `${Math.round(s)} s`;
  const m = Math.floor(s / 60);
  return `${m} m ${String(Math.round(s - m * 60)).padStart(2, '0')} s`;
}

function timeToLimitLine(t) {
  if (!t || t.within_limits) return null;
  const part = t.limiting_part ?? 'a part';
  const lim = t.limits_c?.[part];
  const ends = `then the ${part} reaches${lim == null ? ' its limit'
    : ` ${Math.round(lim)} °C`}`;
  const runs = [];
  const cold = t.starts?.cold?.time_to_limit_s;
  const rated = t.starts?.rated?.time_to_limit_s;
  if (cold != null) runs.push(`${fmtSecs(cold)} from cold`);
  if (rated != null) runs.push(`${fmtSecs(rated)} from rated`);
  if (!runs.length) {
    return `No time to the ${part} limit: this network settles below it`;
  }
  return `Runs ${runs.join(', ')}, ${ends}`;
}

// ── fixtures ──────────────────────────────────────────────────────────────
const OVER = {
  within_limits: false,
  time_to_limit_s: 160.2,
  time_to_limit_from_rated_s: 65.0,
  limiting_part: 'winding',
  limits_c: { winding: 200, magnet: 180 },
  over_by_K: { winding: 12.4 },
  parts: [{ part: 'winding', quantity: 'the winding hot spot', limit_c: 200,
            reaches: true, time_to_limit_s: 160.2,
            note: 'the winding hot spot reaches 200 °C after 2 m 40 s' }],
  starts: {
    cold: { time_to_limit_s: 160.2, start_source: 'every node at 25 °C' },
    rated: { time_to_limit_s: 65.0, start_source: "the rated duty's own run" },
  },
};

test('the line says how long it runs and what ends the pull', () => {
  assert.equal(timeToLimitLine(OVER),
    'Runs 2 m 40 s from cold, 1 m 05 s from rated, '
    + 'then the winding reaches 200 °C');
});

test('a point inside every limit shows nothing at all', () => {
  assert.equal(timeToLimitLine({ within_limits: true, time_to_limit_s: null }),
    null);
  // …and so does a record from before this existed.
  assert.equal(timeToLimitLine(undefined), null);
  assert.equal(timeToLimitLine(null), null);
});

test('with no rated duty the warm term is absent, never an em dash', () => {
  const cold = { ...OVER, time_to_limit_from_rated_s: null,
                 starts: { cold: OVER.starts.cold } };
  const line = timeToLimitLine(cold);
  assert.equal(line,
    'Runs 2 m 40 s from cold, then the winding reaches 200 °C');
  assert.ok(!line.includes('—'), line);
  assert.ok(!line.includes('rated'), line);
});

test('a limit the network never reaches says so instead of a number', () => {
  const never = { ...OVER, time_to_limit_s: null,
                  starts: { cold: { time_to_limit_s: null } } };
  assert.equal(timeToLimitLine(never),
    'No time to the winding limit: this network settles below it');
});

test('the duration words are the ones the report and the solver print', () => {
  assert.equal(fmtSecs(0.83), '0.8 s');
  assert.equal(fmtSecs(48.2), '48 s');
  assert.equal(fmtSecs(125), '2 m 05 s');
  assert.equal(fmtSecs(160.2), '2 m 40 s');
  assert.equal(fmtSecs(null), '—');
  assert.equal(fmtSecs(Number.NaN), '—');
});

// ── the flag (owner 2026-09-17) ───────────────────────────────────────────
// This is NOT a duty cycle: it reads no cycle block and needs no duty ratio, so
// it must keep working with `VITE_DUTY_CYCLE` off.  `dutyCycleFlag.test.mjs`
// pins the number of guards in coupledApi.ts at three; this pins that none of
// them is on the time-to-limit helpers, which is the same fact read the other
// way round and the one that would actually be missed.
test('the time-to-limit helpers are not behind the duty-cycle flag', () => {
  const here = dirname(fileURLToPath(import.meta.url));
  const src = readFileSync(join(here, '..', 'coupledApi.ts'), 'utf8');
  const fn = src.slice(src.indexOf('export function timeToLimitLine'),
                       src.indexOf('export function timeToLimitTip'));
  assert.ok(fn.length > 100, 'timeToLimitLine was not found in coupledApi.ts');
  assert.ok(!fn.includes('DUTY_CYCLE_ENABLED'), fn);
  const tip = src.slice(src.indexOf('export function timeToLimitTip'),
                        src.indexOf('/** One decimal'));
  assert.ok(!tip.includes('DUTY_CYCLE_ENABLED'), tip);
});

// ── SOLVE TO THE STEADY STATE, OR TO THE LIMITS (owner 2026-09-18) ─────────
// `coupledStateLine` is the one function every panel now asks: it reads the
// record's own MODE and prints the sentence that mode calls for.  Restated
// verbatim, like everything else in this file.
function coupledStateLine(c) {
  if (!c) return null;
  if (c.mode === 'limited' && c.limited?.line) return c.limited.line;
  return timeToLimitLine(c.time_to_limit);
}

const LIMITED_LINE =
  'Runs 24 s from cold (9.1 s from rated) at this power and cooling, then the '
  + 'winding reaches 200 °C — the numbers below are the machine at that moment';

test('a limited record prints its own sentence, not the time-to-limit one', () => {
  const c = { mode: 'limited', limited: { part: 'winding', line: LIMITED_LINE },
              time_to_limit: OVER };
  assert.equal(coupledStateLine(c), LIMITED_LINE);
});

test('a steady record prints the line it always printed', () => {
  assert.equal(coupledStateLine({ mode: 'steady', time_to_limit: OVER }),
               timeToLimitLine(OVER));
  // …and so does a record written before the choice existed.
  assert.equal(coupledStateLine({ time_to_limit: OVER }),
               timeToLimitLine(OVER));
});

test('a limits run that found nothing to stop at is a steady line', () => {
  // `solve_to: limits` with a machine inside every limit comes back as a
  // STEADY record — there is no moment to report — and the panel says nothing.
  assert.equal(
    coupledStateLine({ solve_to: 'limits', mode: 'steady',
                       time_to_limit: { within_limits: true } }), null);
});

test('the source keeps the mode check on the record, not on a temperature', () => {
  // The rule this file exists to protect: which sentence is printed is decided
  // by the record's own `mode`, never by comparing a temperature to a limit in
  // the browser — two places judging "is this past its class" is how the panel
  // and the PDF end up disagreeing.
  const here2 = dirname(fileURLToPath(import.meta.url));
  const src = readFileSync(join(here2, '..', 'coupledApi.ts'), 'utf8');
  const fn = src.slice(src.indexOf('export function coupledStateLine'),
                       src.indexOf('export function coupledStateTip'));
  assert.ok(fn.includes("c.mode === 'limited'"), fn);
  assert.ok(!fn.includes('>'), fn);       // no comparison of its own
});
