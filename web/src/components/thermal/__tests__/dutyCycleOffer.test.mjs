/**
 * node --test — the duty-cycle editor's read of `no_electromagnetic_run`
 * (2026-09-14; the OFFER to make the run removed 2026-09-30 — see below).
 *
 * This file IMPORTS the shipped module instead of restating it, unlike the
 * older node tests beside it: `components/thermal/dutyCycleOffer.ts` was written
 * dependency-free (no `import.meta.env`, no React, no fetch client) exactly so
 * node's own type stripping can load it, and a test that runs the real code
 * cannot drift from it.
 *
 * WHAT IS PINNED, and why each one is worth the lines:
 *
 *   • the REFUSAL is recognised by its machine-readable code and nothing else —
 *     the English sentence beside it is written for a human and will be
 *     reworded;
 *   • the POINT comes from the calibration duty's STORED entry, field for field
 *     as `routes/thermal.py` reconstructs it — so the notice this module feeds
 *     can name it, never so a run can be made at it.  This app does not launch
 *     electromagnetics from the thermal side any more (owner rule, 2026-09-07,
 *     repeated 2026-09-30): the OFFER this module used to drive (`emRunBodyAt`,
 *     `offerReduce`, the run/accept/cancel/stop state machine) is gone, and so
 *     are the tests that pinned it — there is nothing left to launch a run, and
 *     nothing here should grow back into doing it.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  calibrationPoint, fetchCalibrationPoint, pointWords,
} from '../dutyCycleOffer.ts';

/* ── the refusal, recognised by its code ─────────────────────────────────────
   Copied from `thermal/api.ts` (that module reaches `import.meta.env` through
   its imports and cannot be loaded here): the contract is the CODE. */
const NO_EM_RUN = 'no_electromagnetic_run';
const isNoEmRun = (e) => e instanceof Error && e.code === NO_EM_RUN;

const refusal = (code, message) => {
  const e = new Error(message);
  e.code = code;
  return e;
};

test('the refusal is recognised by its code, never by its sentence', () => {
  assert.equal(isNoEmRun(refusal(NO_EM_RUN, 'no run at 14.7 A, 1 000 rpm…')), true);
  // Every OTHER refusal of the same route stays the user's to read.
  for (const other of ['duty_cycle_missing', 'no_thermal_boundary',
    'duty_cycle_mixed_speed', 'no_steady_thermal_map', null]) {
    assert.equal(isNoEmRun(refusal(other, 'no electromagnetic run at all')), false,
      `${other} must not be treated as the missing-run refusal`);
  }
  // A sentence that happens to contain the words is not the contract either.
  assert.equal(isNoEmRun(new Error('no_electromagnetic_run')), false);
  assert.equal(isNoEmRun('no_electromagnetic_run'), false);
});

/* ── the point, out of the calibration duty's stored entry ───────────────── */

/** A duty as `GET /api/family/payload/{die}/{cfg}?duty=…` returns it. */
const entry = {
  name: 'rated 30С wire 30C NdFeB',
  mode: 'motor',
  rpm: 1000,
  current_arms: 14.7,
  gamma_deg: 12.5,
  star_delta: 'delta',
  summary: { coil_temp_C: 72.5, I_phase_rms_A: 99, rpm: 99, op_mode: 'generator' },
};

test('the point is the duty entry’s own, with the summary as the fallback', () => {
  const p = calibrationPoint('rated 30С wire 30C NdFeB', entry);
  assert.equal(p.rpm, 1000);
  assert.equal(p.I_phase_rms, 14.7);
  assert.equal(p.gamma_deg, 12.5);
  // …and the coil temperature has no entry-level key at all: it is the
  // SUMMARY's, which is what the route reads.
  assert.equal(p.coil_temp_c, 72.5);
  assert.equal(p.star_delta, 'delta');
  // the entry's own mode wins over the summary's
  assert.equal(p.mode, 'motor');
  assert.equal(p.duty, 'rated 30С wire 30C NdFeB');
});

test('a duty that states nothing at entry level falls through to the summary', () => {
  const p = calibrationPoint('peak', {
    summary: { rpm: 2200, I_phase_rms_A: 588.3, gamma_deg: 30,
               coil_temp_C: 149.2, op_mode: 'generator' },
  });
  assert.equal(p.rpm, 2200);
  assert.equal(p.I_phase_rms, 588.3);
  assert.equal(p.gamma_deg, 30);
  assert.equal(p.coil_temp_c, 149.2);
  assert.equal(p.mode, 'generator');
  assert.equal(p.star_delta, null);
});

test('the route’s own defaults are mirrored, including the 120 °C `or`', () => {
  // `float(cal_summary.get("coil_temp_C") or 120.0)` — a 0 and an absent key
  // both read as 120 there, so they must read as 120 here or the notice would
  // name a temperature the lookup never asked for.
  assert.equal(calibrationPoint('d', {}).coil_temp_c, 120);
  assert.equal(calibrationPoint('d', { summary: { coil_temp_C: 0 } }).coil_temp_c, 120);
  assert.equal(calibrationPoint('d', { summary: { coil_temp_C: null } }).coil_temp_c, 120);
  assert.equal(calibrationPoint('d', null).rpm, 0);
  assert.equal(calibrationPoint('d', null).I_phase_rms, 0);
  assert.equal(calibrationPoint('d', null).gamma_deg, 0);
});

test('an unreadable entry value does NOT quietly borrow the summary’s', () => {
  // The route's `_pt` breaks out of its search when `float()` raises, so a duty
  // whose rpm is a word answers with the default — not with another number that
  // was never the point of this duty.
  const p = calibrationPoint('d', { rpm: 'fast', summary: { rpm: 3000 } });
  assert.equal(p.rpm, 0);
});

test('pointWords reads as the notice line promises', () => {
  assert.equal(
    pointWords(calibrationPoint('rated', entry)),
    '14.7 A, 1 000 rpm, coil 72.5 °C');
});

/* ── the fetch, mocked — a LOOKUP, and only a lookup ─────────────────────── */

const withFetch = async (impl, body) => {
  const prev = globalThis.fetch;
  globalThis.fetch = impl;
  try { return await body(); } finally { globalThis.fetch = prev; }
};

test('fetchCalibrationPoint asks for the CALIBRATION duty, and only reads', async () => {
  let seen = null;
  const p = await withFetch(async (url, init) => {
    seen = { url: String(url), init };
    return { ok: true, status: 200, json: async () => ({ duty: entry }) };
  }, () => fetchCalibrationPoint('http://localhost:8001/',
    'CIANO28 85 20SW1200', 'L13', 'rated 30С wire 30C NdFeB'));
  assert.match(seen.url, /^http:\/\/localhost:8001\/api\/family\/payload\//);
  assert.match(seen.url, /CIANO28%2085%2020SW1200\/L13\?duty=/);
  // The duty name is the CALIBRATION one, url-encoded (Cyrillic С included —
  // the catalogue has duties with it, and the lookalike is a standing trap).
  assert.ok(seen.url.endsWith(
    `?duty=${encodeURIComponent('rated 30С wire 30C NdFeB')}`));
  // A GET with no body: this call must not be able to write anything — it is
  // read for the notice's sentence, never to launch a run.
  assert.equal(seen.init?.method, undefined);
  assert.equal(seen.init?.body, undefined);
  assert.equal(p.coil_temp_c, 72.5);
});

test('fetchCalibrationPoint refuses rather than inventing a point', async () => {
  await assert.rejects(
    withFetch(async () => ({ ok: false, status: 404, json: async () => ({}) }),
      () => fetchCalibrationPoint('http://x', 'd', 'c', 'rated')),
    /could not be read/);
  await assert.rejects(
    withFetch(async () => ({ ok: true, status: 200, json: async () => ({}) }),
      () => fetchCalibrationPoint('http://x', 'd', 'c', 'rated')),
    /no stored entry/);
});
