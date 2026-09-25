// node --test — the Controller's Carrier box holds a REAL value (2026-09-24).
//
// Owner, on a screenshot of the greyed "Carrier 20,000 Hz" placeholder: «Это
// значение нужно задавать в контроллере; PWM нужно выкинуть из
// Electromagnetic.»  So the carrier is a normal editable field saved with the
// controller settings; a configuration with none saved starts from the value
// the backend resolved (the retired Simulation-tab carrier — migration — or the
// stated default), WRITTEN INTO the field with its origin on one line, until
// the auto-save (any edit, or the next Solve — 2026-09-25) makes it the
// Controller's own.
//
// `carrierPrefill` / `carrierOriginLine` are copied verbatim from
// `controllerApi.ts` (node cannot load the TS module — see the other tests in
// this folder); the panel source is read to pin that it uses them and shows no
// placeholder for the carrier.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const PANEL = readFileSync(join(HERE, '..', 'ControllerPanel.tsx'), 'utf8');
const API = readFileSync(join(HERE, '..', 'controllerApi.ts'), 'utf8');

/* ── verbatim copies ─────────────────────────────────────────────────────── */

function carrierPrefill(fsw, point) {
  if (fsw !== '' || !point || point.f_carrier_hz == null) {
    return { fsw, origin: null };
  }
  return { fsw: point.f_carrier_hz, origin: point.carrier_origin ?? null };
}

function carrierOriginLine(origin) {
  if (origin === 'legacy') return 'from the old Simulation-tab PWM';
  if (origin === 'default') return 'default';
  return null;
}

/* ── the copies are still the shipped code ───────────────────────────────── */

test('the copies match controllerApi.ts', () => {
  assert.ok(API.includes("if (origin === 'legacy') return 'from the old Simulation-tab PWM';"));
  assert.ok(API.includes("return { fsw: point.f_carrier_hz, origin: point.carrier_origin ?? null };"));
});

/* ── behaviour ───────────────────────────────────────────────────────────── */

// 2026-09-25: the "— Save to keep" hint is gone — ControllerPanel now
// auto-saves on every edit and on every Solve (persistSettings), so there is
// no separate step left to remind anyone about; the line only still says
// WHERE the value came from.
test('an empty box takes the migrated carrier, with its origin', () => {
  assert.deepEqual(carrierPrefill('', { f_carrier_hz: 24000, carrier_origin: 'legacy' }),
                   { fsw: 24000, origin: 'legacy' });
  assert.equal(carrierOriginLine('legacy'), 'from the old Simulation-tab PWM');
  assert.ok(!carrierOriginLine('legacy').includes('Save'));
});

test('an empty box on a machine with no history takes the stated default', () => {
  assert.deepEqual(carrierPrefill('', { f_carrier_hz: 20000, carrier_origin: 'default' }),
                   { fsw: 20000, origin: 'default' });
  assert.equal(carrierOriginLine('default'), 'default');
  assert.ok(!carrierOriginLine('default').includes('Save'));
});

test('a saved or typed carrier is never replaced, and says nothing', () => {
  assert.deepEqual(carrierPrefill(32000, { f_carrier_hz: 24000, carrier_origin: 'legacy' }),
                   { fsw: 32000, origin: null });
  assert.equal(carrierOriginLine('controller'), null);
  assert.equal(carrierOriginLine(null), null);
});

test('no resolved point yet leaves the box as it is', () => {
  assert.deepEqual(carrierPrefill('', null), { fsw: '', origin: null });
  assert.deepEqual(carrierPrefill('', { f_carrier_hz: null }), { fsw: '', origin: null });
});

/* ── the panel ───────────────────────────────────────────────────────────── */

test('the Carrier row has no placeholder and is filled from the resolved point', () => {
  const at = PANEL.indexOf('<Row label="Carrier"');
  assert.ok(at > 0);
  const row = PANEL.slice(at, PANEL.indexOf('</Row>', at));
  assert.ok(!row.includes('placeholder'), 'never a greyed placeholder for the carrier');
  assert.match(PANEL, /carrierPrefill\(fsw, point\)/);
  assert.match(PANEL, /carrierOriginLine\(fswOrigin\)/);
});
