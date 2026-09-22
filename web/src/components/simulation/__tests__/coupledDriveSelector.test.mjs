// node --test — the Coupled panel's own Drive selector (2026-09-22), closing
// Stage 2's open item: `drive: "inverter"` (the Controller's bridge) used to
// be reachable only from the API; `SimulationPanel` now offers it beside
// "Solve to", gated on the active configuration having a saved controller.
//
// The repo's convention for node tests: `node --test` cannot load the TS/TSX
// modules (`import.meta.env`, React), so the pure functions under test are
// re-stated verbatim here and kept in sync (see couplingLine.test.mjs,
// emCoupledHistory.test.mjs for the same rule); everything that is wiring
// rather than logic is pinned by reading the shipped source text instead of
// re-implementing it.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const webSrc = (...p) => join(HERE, '..', '..', '..', ...p);

// ── verbatim from coupledApi.ts (repo convention, see file header) ─────────
function coupledDrive() {
  try {
    const v = JSON.parse(localStorage.getItem('sim.coupledDrive') || '"sine"');
    return v === 'inverter' ? 'inverter' : 'sine';
  } catch { return 'sine'; }
}

function coupledControllerRef() {
  try {
    const raw = localStorage.getItem('sim.coupledControllerRef');
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}

function setCoupledControllerRef(v) {
  try {
    if (v) localStorage.setItem('sim.coupledControllerRef', JSON.stringify(v));
    else localStorage.removeItem('sim.coupledControllerRef');
  } catch { /* private mode — the request falls back to the duty's own record */ }
}

// The `runCoupled` override block — restated as the same pure step it is
// inline (operates on `body`/`opts`, reads the two functions above).
function applyDriveOverride(body, opts) {
  const drv = opts.drive ?? coupledDrive();
  if (drv === 'inverter') {
    body.drive = 'inverter';
    const ref = opts.controller !== undefined ? opts.controller : coupledControllerRef();
    if (ref) body.controller = ref;
  }
  return body;
}

function withLocalStorage(store, fn) {
  const prev = globalThis.localStorage;
  const get = (k) => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null);
  globalThis.localStorage = {
    getItem: get,
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
  };
  try { return fn(); } finally { globalThis.localStorage = prev; }
}

test('coupledDrive defaults to "sine" — with nothing stored, with garbage '
   + 'JSON, and with any value that is not literally "inverter"', () => {
  withLocalStorage({}, () => assert.equal(coupledDrive(), 'sine'));
  withLocalStorage({ 'sim.coupledDrive': 'not json' },
    () => assert.equal(coupledDrive(), 'sine'));
  withLocalStorage({ 'sim.coupledDrive': '"pwm"' },
    () => assert.equal(coupledDrive(), 'sine'));
  withLocalStorage({ 'sim.coupledDrive': '"inverter"' },
    () => assert.equal(coupledDrive(), 'inverter'));
});

test('coupledControllerRef round-trips through setCoupledControllerRef, and '
   + 'null clears the key rather than storing "null"', () => {
  withLocalStorage({}, () => {
    assert.equal(coupledControllerRef(), null);
    setCoupledControllerRef({ device: 'IMCQ120R004M2H', topology: 'one_3ph' });
    assert.deepEqual(coupledControllerRef(),
      { device: 'IMCQ120R004M2H', topology: 'one_3ph' });
    setCoupledControllerRef(null);
    assert.equal(coupledControllerRef(), null);
    assert.equal(localStorage.getItem('sim.coupledControllerRef'), null);
  });
});

test('drive "sine" (the default) leaves the caller\'s own payload untouched '
   + '— no drive/controller key added at all, bit for bit what a plain '
   + 'coupled request has always sent', () => {
  withLocalStorage({}, () => {
    const body = { max_current: 30, drive: 'current' };
    applyDriveOverride(body, {});
    assert.deepEqual(body, { max_current: 30, drive: 'current' });
  });
});

test('drive "inverter" (from the stored selector) overrides body.drive and '
   + 'attaches the mirrored controller settings by reference', () => {
  withLocalStorage({
    'sim.coupledDrive': '"inverter"',
    'sim.coupledControllerRef': JSON.stringify({ device: 'IMCQ120R004M2H' }),
  }, () => {
    const body = { max_current: 30, drive: 'current' };
    applyDriveOverride(body, {});
    assert.equal(body.drive, 'inverter');
    assert.deepEqual(body.controller, { device: 'IMCQ120R004M2H' });
  });
});

test('no controller saved -> "inverter" still sends drive=inverter but no '
   + 'controller key, leaning on the duty\'s own stored solve server-side',
   () => {
  withLocalStorage({ 'sim.coupledDrive': '"inverter"' }, () => {
    const body = { drive: 'current' };
    applyDriveOverride(body, {});
    assert.equal(body.drive, 'inverter');
    assert.ok(!('controller' in body));
  });
});

test('opts.drive/opts.controller (an explicit caller) win over the stored '
   + 'selector, the same override-order every other CoupledRunOptions field '
   + 'uses (solveTo, thermalSettings, maxIter)', () => {
  withLocalStorage({ 'sim.coupledDrive': '"sine"' }, () => {
    const body = { drive: 'current' };
    applyDriveOverride(body, { drive: 'inverter', controller: { device: 'X' } });
    assert.equal(body.drive, 'inverter');
    assert.deepEqual(body.controller, { device: 'X' });
  });
  withLocalStorage({
    'sim.coupledDrive': '"inverter"',
    'sim.coupledControllerRef': JSON.stringify({ device: 'STORED' }),
  }, () => {
    const body = { drive: 'current' };
    applyDriveOverride(body, { drive: 'sine' });
    assert.equal(body.drive, 'current', '"sine" from opts must not be forced on');
  });
});

// ── coupledApi.ts: the exported functions and where they sit in runCoupled ──
const coupledApiSrc = readFileSync(webSrc('components', 'simulation', 'coupledApi.ts'), 'utf8');

test('coupledDrive / coupledControllerRef / setCoupledControllerRef are '
   + 'exported, and CoupledRunOptions declares drive/controller', () => {
  assert.ok(coupledApiSrc.includes("export function coupledDrive("));
  assert.ok(coupledApiSrc.includes("export function coupledControllerRef("));
  assert.ok(coupledApiSrc.includes("export function setCoupledControllerRef("));
  const start = coupledApiSrc.indexOf('export interface CoupledRunOptions');
  const end = coupledApiSrc.indexOf('\n}', start);
  const body = coupledApiSrc.slice(start, end);
  assert.ok(/drive\?:\s*'sine' \| 'inverter'/.test(body));
  assert.ok(body.includes('controller?: Record<string, unknown> | null'));
});

test('runCoupled applies the drive override AFTER solve_to is set — both '
   + 'questions (what to solve to, what to solve ON) ride the same request '
   + 'independently, so every solveTo (steady/limits/continuous) works with '
   + 'either drive', () => {
  const soloTo = coupledApiSrc.indexOf('body.solve_to = opts.solveTo');
  const drivePos = coupledApiSrc.indexOf("if (drv === 'inverter')");
  assert.ok(soloTo > 0 && drivePos > soloTo,
    'the drive override must be wired after solve_to, never replacing it');
});

// ── SimulationPanel.tsx: the selector itself ────────────────────────────────
const panelSrc = readFileSync(webSrc('components', 'simulation', 'SimulationPanel.tsx'), 'utf8');

test('the panel gates the option on a saved controller (GET /api/controller/'
   + 'settings via getControllerSettings), never on the Controller tab merely '
   + 'having been opened', () => {
  assert.ok(panelSrc.includes("import { getControllerSettings, type ControllerSettings } "
    + "from '../controller/controllerApi';"));
  assert.ok(panelSrc.includes('const controllerReady = !!ctrlSettings?.device;'));
});

test('the Drive selector offers "sine current" and "inverter (Controller)", '
   + 'the second disabled without a controller, only while Coupled thermal is '
   + 'on', () => {
  const start = panelSrc.indexOf('id="coupled-drive-label"');
  assert.ok(start > 0, 'the Drive FormControl must exist');
  const end = panelSrc.indexOf('</FormControl>', start);
  const block = panelSrc.slice(start - 400, end);
  assert.ok(block.includes('{coupled && ('), 'must render only while Coupled thermal is on');
  assert.ok(block.includes('<MenuItem value="sine">sine current</MenuItem>'));
  assert.ok(/<MenuItem value="inverter" disabled=\{!controllerReady\}/.test(block));
  assert.ok(block.includes('set up the controller in the Controller tab first'),
    'the disabled state must explain itself in the HelpTip, per the brief');
});

test('a controller that disappears (duty switch, empty settings) falls the '
   + 'selector back to "sine" rather than silently asking the backend to '
   + 'guess which duty\'s controller it meant', () => {
  assert.ok(panelSrc.includes("if (!controllerReady && coupledDrive === 'inverter') "
    + "setCoupledDrive('sine');"));
});

test('the panel mirrors the fetched settings for coupledApi to send by '
   + 'reference, never threading it through TransientCharts as a prop', () => {
  assert.ok(panelSrc.includes('setCoupledControllerRef(controllerReady'));
});

test('the controller-settings fetch re-checks when the Coupled switch is '
   + 'flipped on, not only when die/config change — this panel stays mounted '
   + 'across a tab switch, so a controller saved on the Controller tab AFTER '
   + 'this one first loaded would otherwise never be picked up (found live in '
   + 'the sandbox: save a controller, switch to Electromagnetic, the option '
   + 'stayed disabled until reload)', () => {
  assert.ok(panelSrc.includes('}, [dieCtx.die, dieCtx.config, coupled]);'),
    'the fetch effect must depend on `coupled` too');
});

// ── the backend progress phase the ring now shows (routes/coupled.py) ──────
const coupledRouteSrc = readFileSync(
  join(HERE, '..', '..', '..', '..', '..', 'src', 'motor_ai_sim', 'routes', 'coupled.py'),
  'utf8');

test('the coupled loop announces the controller step on the SAME progress '
   + 'object the strip already polls, so the ring shows the extra phase '
   + 'without any change to SolveProgressStrip (which already renders '
   + 'p.phase generically)', () => {
  const phasePos = coupledRouteSrc.indexOf(
    'phase="iteration %d/%d — controller: device losses / T_j"');
  assert.ok(phasePos > 0, 'the new phase string must be present');
  const stepPos = coupledRouteSrc.indexOf('d_tj = ctl.step(em, it=it)');
  assert.ok(stepPos > phasePos && stepPos - phasePos < 400,
    'the phase must be announced immediately before this same near-instant '
    + 'controller step, not some unrelated later one');
});
