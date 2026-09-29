/**
 * node --test — the duty-cycle editor's escape hatch out of
 * `no_electromagnetic_run` (2026-09-14).
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
 *     as `routes/thermal.py` reconstructs it, and the run body carries THAT and
 *     not the Electromagnetic tab's live fields.  The whole bug this feature
 *     exists to avoid is a six-minute run made at the wrong point, which fails
 *     by being refused a second time with the same words;
 *   • the OFFER is a state machine with exactly one run in it: offer → run →
 *     retry, cancel, stop — and never a second offer after a run has been made,
 *     because a machine that cannot be satisfied must not cost two of them.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  OFFER_IDLE, calibrationPoint, emRunBodyAt, fetchCalibrationPoint, isOffering,
  offerReduce, pointWords,
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
      `${other} must not be answered by making a run`);
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
  // both read as 120 there, so they must read as 120 here or the run would be
  // made at a temperature the lookup never asks for.
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

test('pointWords reads as the offer line promises', () => {
  assert.equal(
    pointWords(calibrationPoint('rated', entry)),
    '14.7 A, 1 000 rpm, coil 72.5 °C');
});

/* ── the fetch, mocked ───────────────────────────────────────────────────── */

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
  // A GET with no body: this call must not be able to write anything.
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

/* ── the run body carries the DUTY's point, not the tab's ────────────────── */

/** What `buildEmRunPayload` produces from the Electromagnetic tab's own
 *  persisted fields — the shape, with every field this feature must override
 *  set to a value nothing else in the test uses. */
const emTabPayload = () => ({
  restore: false,
  n_steps_per_period: 40,
  n_periods: 1,
  gamma_deg: 0,
  I_phase_rms: 461.7,
  drive: 'current',
  rpm: 23000,
  coil_temp_c: 180,
  magnet_temp_c: 163,
  mode: 'generator',
  star_delta: 'star',
  connection: '4S',
  mesh_size_mm: 4.0,
  min_size_mm: 0.3,
  outer_air_factor: 1.3,
  n_sectors: 1,
  component_mesh: '{}',
  n_frames: 40,
  run_id: 'therm-abc',
  eddy: true,
  rotor_eddy: true,
});

const cycleMesh = {
  mesh_size_mm: 2.5, min_size_mm: 0.2, outer_air_factor: 1.4, n_sectors: 4,
  component_mesh: '{"magnet":1.2}',
};

test('the orchestrator body is the CALIBRATION duty’s point, not the EM tab’s', () => {
  const point = calibrationPoint('rated 30С wire 30C NdFeB', entry);
  const body = emRunBodyAt(emTabPayload(),
    { point, n_steps_per_period: 24, mesh: cycleMesh });

  // the point itself
  assert.equal(body.I_phase_rms, 14.7);
  assert.equal(body.gamma_deg, 12.5);
  assert.equal(body.coil_temp_c, 72.5);
  assert.equal(body.rpm, 1000);
  assert.equal(body.star_delta, 'delta');
  assert.equal(body.mode, 'motor');

  // the frame count and the mesh the CYCLE REQUEST sends — the loss map is
  // looked up by both, so a run on the Electromagnetic tab's 40 steps and 4 mm
  // mesh would be refused again with the same words.
  assert.equal(body.n_steps_per_period, 24);
  assert.equal(body.n_frames, 24);
  assert.equal(body.mesh_size_mm, 2.5);
  assert.equal(body.min_size_mm, 0.2);
  assert.equal(body.outer_air_factor, 1.4);
  assert.equal(body.n_sectors, 4);
  assert.equal(body.component_mesh, '{"magnet":1.2}');

  // NOT the Electromagnetic tab's, anywhere
  assert.notEqual(body.I_phase_rms, 461.7);
  assert.notEqual(body.coil_temp_c, 180);
  assert.notEqual(body.rpm, 23000);

  // the magnet temperature the cycle request does NOT send is REMOVED: it is
  // part of the loss map's physics identity, so a run carrying the tab's value
  // could never match the lookup.
  assert.equal('magnet_temp_c' in body, false);

  // …and everything the run needs that this feature has no opinion about is
  // untouched.
  assert.equal(body.drive, 'current');
  assert.equal(body.connection, '4S');
  assert.equal(body.eddy, true);
  assert.equal(body.rotor_eddy, true);
  assert.equal(body.run_id, 'therm-abc');
  assert.equal(body.n_periods, 1);

  // the caller's payload is not mutated
  assert.equal(emTabPayload().coil_temp_c, 180);
});

test('a duty with no speed drops the key instead of borrowing the tab’s', () => {
  const point = calibrationPoint('d', { current_arms: 5, summary: { coil_temp_C: 40 } });
  const body = emRunBodyAt(emTabPayload(),
    { point, n_steps_per_period: 24, mesh: null });
  // 23 000 rpm from the Electromagnetic tab would be a run of a different
  // machine entirely; absent, the backend falls back to the shared
  // configuration — the same speed its own loss-map probe resolves.
  assert.equal('rpm' in body, false);
  // a mesh that was not handed over leaves the payload's own
  assert.equal(body.mesh_size_mm, 4.0);
  assert.equal(body.n_sectors, 1);
});

test('a duty that names no connection keeps the payload’s', () => {
  const point = calibrationPoint('d', { rpm: 1000, current_arms: 5 });
  const body = emRunBodyAt(emTabPayload(),
    { point, n_steps_per_period: 12, mesh: null });
  assert.equal(body.star_delta, 'star');
  // …and an unstated mode is the catalogue's own default, never the tab's.
  assert.equal(body.mode, 'motor');
});

/* ── the offer: offer → run → retry, cancel, stop ────────────────────────── */

const POINT = calibrationPoint('rated 30С wire 30C NdFeB', entry);
const refused = { type: 'refused', why: 'no electromagnetic run at …', point: POINT };

test('offer → run → retry', () => {
  let s = OFFER_IDLE;
  assert.equal(isOffering(s), false);

  s = offerReduce(s, { type: 'run' });          // the Run press
  s = offerReduce(s, refused);                  // the route refused
  assert.equal(s.phase, 'offered');
  assert.equal(isOffering(s), true);
  assert.equal(s.point.coil_temp_c, 72.5);
  assert.equal(s.why, 'no electromagnetic run at …');
  // NOTHING runs on the refusal alone — that is the whole point of the offer.
  assert.equal(s.attempted, false);

  s = offerReduce(s, { type: 'accept' });       // [Make the run]
  assert.equal(s.phase, 'running');
  assert.equal(s.stopping, false);
  assert.equal(s.attempted, true);
  assert.equal(isOffering(s), false);

  s = offerReduce(s, { type: 'settled' });      // the run ended
  assert.equal(s.phase, 'idle');
  assert.equal(s.attempted, true);
});

test('one run per press: a second refusal is a refusal, not a second offer', () => {
  let s = offerReduce(offerReduce(OFFER_IDLE, { type: 'run' }), refused);
  s = offerReduce(s, { type: 'accept' });
  s = offerReduce(s, { type: 'settled' });
  // The retry was refused again — the run did not produce the map the route
  // looks for.  Offering a SECOND run would cost the user another six minutes
  // for the same answer.
  s = offerReduce(s, refused);
  assert.equal(s.phase, 'idle');
  assert.equal(isOffering(s), false);
  assert.equal(s.why, 'no electromagnetic run at …');

  // …but the NEXT Run press starts over.
  s = offerReduce(s, { type: 'run' });
  assert.deepEqual(s, OFFER_IDLE);
  s = offerReduce(s, refused);
  assert.equal(s.phase, 'offered');
});

test('cancel declines the offer and leaves the refusal standing', () => {
  let s = offerReduce(offerReduce(OFFER_IDLE, { type: 'run' }), refused);
  s = offerReduce(s, { type: 'cancel' });
  assert.equal(s.phase, 'idle');
  assert.equal(s.attempted, false);        // nothing was spent
  assert.equal(s.why, 'no electromagnetic run at …');  // shown as the error
  // A cancel that arrives when nothing is offered changes nothing.
  assert.deepEqual(offerReduce(s, { type: 'cancel' }), s);
});

test('stop only exists while a run is in flight, and only once', () => {
  let s = offerReduce(offerReduce(OFFER_IDLE, { type: 'run' }), refused);
  // No run yet — Stop must not claim to cancel anything.
  assert.equal(offerReduce(s, { type: 'stop' }).stopping, false);

  s = offerReduce(s, { type: 'accept' });
  const stopping = offerReduce(s, { type: 'stop' });
  assert.equal(stopping.stopping, true);
  assert.equal(stopping.phase, 'running');
  // A second press is not a second cancel.
  assert.equal(offerReduce(stopping, { type: 'stop' }), stopping);
  // The loop winds down and answers; the editor goes back to idle.
  assert.equal(offerReduce(stopping, { type: 'settled' }).phase, 'idle');
});

test('a Run press cannot restart a run that is in flight', () => {
  let s = offerReduce(offerReduce(OFFER_IDLE, { type: 'run' }), refused);
  s = offerReduce(s, { type: 'accept' });
  assert.equal(offerReduce(s, { type: 'run' }), s);
  // …and neither can a stale refusal arriving from somewhere else.
  assert.equal(offerReduce(s, refused), s);
});

/* ═══════════════════════════════════════════════════════════════════════════
 * `record: false` — the run is not the LOADED duty's answer (2026-09-15)
 *
 * Measured that morning: the run this escape hatch makes is solved at the
 * CALIBRATION duty's point (14.7 A, 1 000 rpm, coil 30 °C) while the editor has
 * a different duty loaded (the L13 `peak 200С wire 120C NdFeB`, 45.96 A at
 * 200 °C), and the backend filed it as that duty's answer — its `em` and
 * `thermal` field sidecars came back as 14.7 A / 30 °C maps and
 * `/api/thermal/last` as 59 °C instead of 409 °C.  The body now carries
 * `record: false`, which suppresses the FILING and nothing else (the field
 * snapshot the cycle route looks the loss map up in is still written).
 *
 * Read off the SOURCE rather than exercised: the flag is added in
 * `stores/thermalStore.ts`, which reaches `import.meta.env`, React and zustand
 * through its imports and cannot be loaded by `node --test` — the same reason
 * `coolingPayload.test.mjs` beside this file does not import `thermal/api.ts`.
 * What is pinned is the DIFFERENCE that matters and the one a refactor would
 * lose: the flag rides the `at` branch (the calibration point) and the Thermal
 * tab's own Solve fallback, which IS the loaded duty's point, still sends a
 * plain body.
 * ═══════════════════════════════════════════════════════════════════════════ */

const storeSrc = await (async () => {
  const { readFile } = await import('node:fs/promises');
  const { fileURLToPath } = await import('node:url');
  const path = await import('node:path');
  const here = path.dirname(fileURLToPath(import.meta.url));
  return readFile(path.resolve(here, '../../../stores/thermalStore.ts'), 'utf8');
})();

/** The one statement that builds the orchestrator body, source text and all. */
function payloadStatement() {
  const i = storeSrc.indexOf('const payload = at');
  assert.notEqual(i, -1, 'thermalStore no longer builds the run body here — '
    + 'move this test to wherever it does, do not delete it');
  const j = storeSrc.indexOf('\n    const res = await runCoupled(', i);
  assert.ok(j > i, 'the body is still handed straight to runCoupled');
  return storeSrc.slice(i, j);
}

test('the calibration run tells the backend not to file it', () => {
  const stmt = payloadStatement();
  // The flag itself, on the branch that carries ANOTHER duty's point.
  assert.match(stmt, /emRunBodyAt\(base, at\)/);
  assert.match(stmt, /record:\s*false/);
  // …and the Thermal tab's own Solve — the point THIS tab is showing, which is
  // the loaded duty's — is still a plain body that gets filed as always.
  assert.match(stmt, /:\s*base;?\s*$/);
});

test('the Solve fallback is NOT marked as somebody else’s errand', () => {
  // `record` is SENT from exactly one place in the whole store (the prose that
  // explains it does not count): a second occurrence is either the fallback
  // branch marking itself — the Thermal tab would stop remembering its own
  // maps — or a stray.
  const code = storeSrc.split('\n')
    .filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l)).join('\n');
  const all = code.match(/\brecord:/g) || [];
  assert.equal(all.length, 1, 'record: is sent from exactly one place');
  assert.ok(payloadStatement().includes('record: false'));
});
