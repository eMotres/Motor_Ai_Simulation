/**
 * node --test — THE DUTY-CYCLE FEATURE FLAG (owner, 2026-09-17: «давай пока
 * уберём duty cycle из Thermal, оставим только стандартный каплинг»).
 *
 * Two things are worth pinning and nothing else here is:
 *
 *   • the flag is OFF unless a build turns it on.  A hidden feature that comes
 *     back because an unset variable read as truthy is the one failure this
 *     module can have;
 *   • with it off NOTHING duty-cycle-shaped renders — the Thermal tab does not
 *     mount the editor, the catalog's chip is `null`, and no coupled answer
 *     prints an ED term.
 *
 * The second half is asserted on the SOURCE of the three call sites.  This repo
 * has no React test renderer (every web test here is `node --test` over pure
 * functions — see `dutyCycleBlock.test.mjs` and `couplingLine.test.mjs`), and a
 * gate that is present in the module but absent from the JSX is exactly the
 * regression worth catching; the python side pins the same rule with
 * `inspect.getsource` (tests/test_coupled_impulse_duty).
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { DUTY_CYCLE_ENABLED, dutyCycleEnabled, gatedDutyCycleChip }
  from '../dutyCycleFlag.ts';

const src = (rel) =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8');

// ── the flag itself ──────────────────────────────────────────────────────────

test('off unless a build says otherwise', () => {
  // node has no `import.meta.env` at all, which is the same answer an unset
  // VITE_DUTY_CYCLE gives in a real build: OFF.
  assert.equal(DUTY_CYCLE_ENABLED, false);
  for (const v of [undefined, null, '', '0', 'off', 'no', 'false', ' ', 'maybe']) {
    assert.equal(dutyCycleEnabled(v), false, `${JSON.stringify(v)} is not on`);
  }
});

test('on for the values a build writes', () => {
  for (const v of ['1', 'true', 'TRUE', 'yes', 'on', ' 1 ']) {
    assert.equal(dutyCycleEnabled(v), true, `${JSON.stringify(v)} is on`);
  }
});

// ── what it hides ────────────────────────────────────────────────────────────

test('the catalog chip is null while the feature is off', () => {
  assert.equal(gatedDutyCycleChip('S3 ED 25 % · 60 s', false), null);
  assert.equal(gatedDutyCycleChip(null, false), null);
  // …and untouched when it is on — the gate adds nothing of its own
  assert.equal(gatedDutyCycleChip('S3 ED 25 % · 60 s', true), 'S3 ED 25 % · 60 s');
  assert.equal(gatedDutyCycleChip(null, true), null);
  // the DEFAULT argument is this build's flag, i.e. off
  assert.equal(gatedDutyCycleChip('S3 ED 25 %'), null);
});

test('the Thermal tab mounts the editor only behind the flag', () => {
  const panel = src('../../components/thermal/ThermalPanel.tsx');
  assert.match(panel, /\{DUTY_CYCLE_ENABLED && <DutyCycleEditor \/>\}/);
  // exactly one mount, and no unguarded one anywhere else in the tab
  assert.equal((panel.match(/<DutyCycleEditor\s*\/>/g) || []).length, 1);
});

test('the catalog chip goes through the gate', () => {
  const cat = src('../../components/catalog/FamilyCatalog.tsx');
  const calls = (cat.match(/(?<!gated)dutyCycleChip\(/g) || []).length;
  const gated = (cat.match(/gatedDutyCycleChip\(dutyCycleChip\(/g) || []).length;
  assert.ok(calls > 0, 'the catalog no longer chips a duty at all');
  assert.equal(calls, gated, 'a duty chip that skips the gate');
});

test('a coupled answer prints no ED term while the feature is off', () => {
  const api = src('../../components/simulation/coupledApi.ts');
  // the three readers of a coupled regime: the summary term, the run notice and
  // the tooltip rows.  A restored record may still CARRY one — it must not show.
  assert.equal((api.match(/!DUTY_CYCLE_ENABLED/g) || []).length, 3);
});
