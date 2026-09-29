// node --test — the "Loaded from history" notice, the History popover and its
// Recompute for the Simulation tab's two solve kinds (2026-09-22): the EM
// transient (`.run_ledger`) and the coupled EM<->thermal loop (`coupled.run`,
// `motor_ai_sim.run_history`-backed). Mechanical/Thermal got the notice in
// fa79a91; this is the matching wiring for TransientCharts.tsx, plus the
// operating-point sync a loaded entry needs so the dashboard's "different
// point" verdict does not fire against itself (owner: a loaded row is the
// same point ITS OWN run was solved at, by construction).
//
// The repo's convention for node tests: `node --test` cannot load a .tsx/.ts
// module that touches `import.meta.env`/React, so the pure function under
// test is re-stated verbatim and kept in sync (see continuousRating.test.mjs
// for `applyS1AsOperatingPoint`, which this module's sibling function
// extends); everything that is wiring rather than logic is pinned by reading
// the shipped source text instead of re-implementing it.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const webSrc = (...p) => join(here, '..', '..', '..', ...p);

// ── verbatim from coupledApi.ts (repo convention, see file header) ─────────
const API = 'http://localhost:8001';
function applyLoadedOperatingPoint(point) {
  const finite = (v) => typeof v === 'number' && Number.isFinite(v);
  try {
    if (finite(point.current)) {
      localStorage.setItem('sim.current', JSON.stringify(point.current));
      localStorage.setItem('sim.current.s1AppliedAt', String(Date.now()));
    }
    if (finite(point.gamma_deg)) localStorage.setItem('sim.gamma', JSON.stringify(point.gamma_deg));
    if (finite(point.rpm)) localStorage.setItem('sim.rpm', JSON.stringify(point.rpm));
  } catch { /* best effort */ }
  try { window.dispatchEvent(new Event('sim-settings-restored')); }
  catch { /* best effort */ }
  if (finite(point.current)) {
    try {
      fetch(`${API}/api/simulation/config`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ max_current: point.current }),
      }).catch(() => {});
    } catch { /* best effort */ }
  }
}

function withGlobals(ls, win, ft, fn) {
  const prevLS = globalThis.localStorage;
  const prevWin = globalThis.window;
  const prevFetch = globalThis.fetch;
  globalThis.localStorage = ls;
  globalThis.window = win;
  globalThis.fetch = ft;
  try { fn(); } finally {
    globalThis.localStorage = prevLS;
    globalThis.window = prevWin;
    globalThis.fetch = prevFetch;
  }
}

test('a loaded point writes current, gamma AND rpm, dispatches the SAME '
   + 'restore event every persisted field re-reads on, and PATCHes the '
   + 'server for the field an F5 re-adopts (current only)', () => {
  const calls = { setItem: [], dispatched: [], fetched: [] };
  withGlobals(
    { setItem: (k, v) => calls.setItem.push([k, v]) },
    { dispatchEvent: (ev) => calls.dispatched.push(ev.type) },
    (url, init) => { calls.fetched.push({ url, init }); return { catch: () => {} }; },
    () => applyLoadedOperatingPoint({ current: 48.6, gamma_deg: 12.5, rpm: 3200 }),
  );
  assert.deepEqual(calls.setItem, [
    ['sim.current', '48.6'],
    ['sim.current.s1AppliedAt', calls.setItem[1][1]],   // a timestamp — not pinned
    ['sim.gamma', '12.5'],
    ['sim.rpm', '3200'],
  ]);
  assert.deepEqual(calls.dispatched, ['sim-settings-restored']);
  assert.equal(calls.fetched.length, 1);
  assert.equal(calls.fetched[0].url, 'http://localhost:8001/api/simulation/config');
  assert.deepEqual(JSON.parse(calls.fetched[0].init.body), { max_current: 48.6 });
});

test('each coordinate is skipped INDEPENDENTLY when the loaded summary does '
   + 'not carry it — an old entry with no gamma must not write "undefined"',
   () => {
  const calls = { setItem: [] };
  withGlobals(
    { setItem: (k, v) => calls.setItem.push([k, v]) },
    { dispatchEvent: () => {} },
    () => ({ catch: () => {} }),
    () => applyLoadedOperatingPoint({ current: 48.6, gamma_deg: undefined, rpm: NaN }),
  );
  const keys = calls.setItem.map(([k]) => k);
  assert.ok(keys.includes('sim.current'));
  assert.ok(!keys.includes('sim.gamma'), 'no gamma in the payload -> no write');
  assert.ok(!keys.includes('sim.rpm'), 'NaN rpm -> no write');
});

test('no current at all -> no server PATCH, but gamma/rpm still land locally',
   () => {
  const calls = { setItem: [], fetched: [] };
  withGlobals(
    { setItem: (k, v) => calls.setItem.push([k, v]) },
    { dispatchEvent: () => {} },
    (url) => { calls.fetched.push(url); return { catch: () => {} }; },
    () => applyLoadedOperatingPoint({ current: null, gamma_deg: 5, rpm: 3000 }),
  );
  assert.equal(calls.fetched.length, 0);
  assert.deepEqual(calls.setItem, [['sim.gamma', '5'], ['sim.rpm', '3000']]);
});

test('never throws — private window (storage/dispatch blocked) or offline '
   + '(fetch throws)', () => {
  withGlobals(
    { setItem() { throw new Error('blocked'); } },
    { dispatchEvent() { throw new Error('blocked'); } },
    () => { throw new Error('no network'); },
    () => assert.doesNotThrow(
      () => applyLoadedOperatingPoint({ current: 1, gamma_deg: 1, rpm: 1 })),
  );
});

// ── the wiring lives in TransientCharts.tsx ─────────────────────────────────
const transientSrc = readFileSync(
  webSrc('components', 'simulation', 'TransientCharts.tsx'), 'utf8');

test('the EM notice is the SHARED "Loaded from history" line (Mechanical/'
   + 'Thermal\'s wording), not a bespoke one, with Recompute forcing a real '
   + 'solve past the ledger', () => {
  assert.ok(transientSrc.includes('historyNoticeFor(data)'),
    'must read the shared notice off the transient payload');
  assert.match(transientSrc, /historyNoticeFor\(data\)![^\n]*\.text/,
    'must render the shared notice\'s own text, not a re-derived string');
  assert.ok(transientSrc.includes("run(false, true)"),
    'Recompute must call run() with freshOnce=true — the same "past the ledger" path');
});

test('the COUPLED half gets its own notice, fed from the top-level '
   + '`served_from_history`/`computed_at` of the /coupled/run response '
   + '(NOT nested under res.coupling, which carries no stamp of its own)',
   () => {
  assert.ok(transientSrc.includes('setCoupledHistory('),
    'must capture served_from_history off the coupled response');
  assert.ok(transientSrc.includes('res.served_from_history && res.computed_at'),
    'must gate on served_from_history being true, not merely computed_at present');
  assert.ok(transientSrc.includes('coupledHistory && !busy'),
    'the coupled notice must render from state, hidden while a solve is running');
});

test('BOTH History popovers are wired: EM ledger (its own dedicated store) '
   + 'and coupled (the generic run_history-backed /api/history)', () => {
  assert.ok(transientSrc.includes('<HistoryPopover') , 'HistoryPopover must be used');
  assert.ok(transientSrc.includes('list={listLedgerHistory}')
    && transientSrc.includes('onLoad={loadLedgerHistory}')
    && transientSrc.includes('onDelete={deleteLedgerHistory}'),
    'the EM popover must be wired to the ledger endpoints');
  assert.ok(transientSrc.includes('list={listCoupledHistory}')
    && transientSrc.includes('onLoad={loadCoupledHistory}')
    && transientSrc.includes('onDelete={deleteCoupledHistory}'),
    'the coupled popover must be wired to the generic history endpoints');
  assert.ok(transientSrc.includes('/api/simulation/ledger/recent')
    && transientSrc.includes('/api/simulation/ledger/${encodeURIComponent(key)}/load')
    && transientSrc.includes("method: 'DELETE'"),
    'the ledger popover must list/load/delete through the three new endpoints');
  assert.ok(transientSrc.includes('/api/history?kind=coupled.run')
    && transientSrc.includes('/api/history/${encodeURIComponent(key)}/load?kind=coupled.run'),
    'the coupled popover must go through the generic, kind-scoped /api/history');
});

test('loading EITHER history row re-syncs the panel\'s operating point — the '
   + 'dashboard\'s stale/"different point" verdict must not fire against a '
   + 'row that is the same point by construction', () => {
  const loadFns = ['loadLedgerHistory', 'loadCoupledHistory'];
  for (const fn of loadFns) {
    const start = transientSrc.indexOf(`const ${fn} = useCallback`);
    assert.ok(start >= 0, `${fn} must exist`);
    const end = transientSrc.indexOf('}, []);', start);
    const body = transientSrc.slice(start, end);
    assert.ok(body.includes('applyLoadedOperatingPoint('),
      `${fn} must sync the operating point through the shared setter`);
    assert.ok(body.includes('I_terminal_rms_A ?? d.summary?.I_phase_rms_A'),
      `${fn} must prefer the terminal setpoint, same as SummaryTable's own opStale`);
  }
});

// ── coupledApi.ts: the new setter and the response fields it reads ─────────
const coupledApiSrc = readFileSync(
  webSrc('components', 'simulation', 'coupledApi.ts'), 'utf8');

test('CoupledRunResult carries the history stamp so the panel can tell a '
   + 'loaded loop from a solved one', () => {
  const start = coupledApiSrc.indexOf('export interface CoupledRunResult');
  const end = coupledApiSrc.indexOf('\n}', start);
  const body = coupledApiSrc.slice(start, end);
  for (const field of ['served_from_history', 'computed_at', 'history_key']) {
    assert.ok(body.includes(field), `CoupledRunResult must declare ${field}`);
  }
});

test('applyLoadedOperatingPoint is exported and connection is deliberately '
   + 'left untouched (a winding mismatch is real and must keep flagging)',
   () => {
  assert.ok(coupledApiSrc.includes('export function applyLoadedOperatingPoint('));
  const start = coupledApiSrc.indexOf('export function applyLoadedOperatingPoint(');
  const end = coupledApiSrc.indexOf('\n}', start);
  const body = coupledApiSrc.slice(start, end);
  assert.ok(!/connection/i.test(body),
    'must never write a connection/star-delta field — that mismatch is real');
});

// ── HistoryPopover.tsx: the shared presentational component ────────────────
const popoverSrc = readFileSync(
  webSrc('components', 'common', 'HistoryPopover.tsx'), 'utf8');

test('HistoryPopover takes list/onLoad/onDelete as props (one component, '
   + 'plugged into two different backends) and renders each row through the '
   + 'shared historyRowLabel formatter', () => {
  assert.ok(/list:\s*\(\)\s*=>\s*Promise<HistoryRow\[\]>/.test(popoverSrc));
  assert.ok(popoverSrc.includes('onLoad: (key: string) => Promise<void>'));
  assert.ok(popoverSrc.includes('onDelete: (key: string) => Promise<void>'));
  assert.ok(popoverSrc.includes("from '../../lib/historyNotice'"));
  assert.ok(popoverSrc.includes('historyRowLabel(r)'));
});

// ── the backend endpoints the EM ledger popover depends on ──────────────────
const simRouteSrc = readFileSync(
  join(here, '..', '..', '..', '..', '..', 'src', 'motor_ai_sim', 'routes',
       'simulation.py'), 'utf8');

test('routes/simulation.py exposes the three ledger History verbs the web '
   + 'popover calls', () => {
  assert.ok(simRouteSrc.includes('@router.get("/ledger/recent")'));
  assert.ok(simRouteSrc.includes('@router.post("/ledger/{key}/load")'));
  assert.ok(simRouteSrc.includes('@router.delete("/ledger/{key}")'));
  assert.ok(simRouteSrc.includes('out["served_from_history"] = True'));
});

const authSrc = readFileSync(
  join(here, '..', '..', '..', '..', '..', 'src', 'motor_ai_sim', 'auth.py'),
  'utf8');

test('the three new ledger endpoints are gated the same as the whole-ledger '
   + 'view/clear they sit beside — the shared machine\'s store, not a '
   + 'per-workspace one', () => {
  assert.ok(authSrc.includes('"/api/simulation/ledger/recent"'));
  assert.ok(authSrc.includes('"/api/simulation/ledger/"'));
});
