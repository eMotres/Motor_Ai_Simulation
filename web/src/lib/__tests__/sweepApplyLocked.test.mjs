// node --test - Apply of a sweep point onto a locked family configuration
// (owner 2026-10-30: PUT /api/geometry 423 twice, the button "did nothing").
import test from 'node:test';
import assert from 'node:assert/strict';
import { planLockedApply, refusedText, newDieNotice } from '../sweepApplyLocked.ts';

const CTX = { active: true, die: 'CIANO56 400_80 vadim 6', config: 'L90-2x3ph-Al2O3-15t', duty: 'S1',
              die_locked: true, config_locked: false };
const REFUSED = [{ field: 'tooth_width', reason: "locked by die 'CIANO56 400_80 vadim 6'" },
                 { field: 'cut_width', reason: "locked by die 'CIANO56 400_80 vadim 6'" }];
const OK = { sweepRunning: false, canWrite: true };

test('a locked die + a writer + no sweep running: the point goes onto a new die', () => {
  const p = planLockedApply(CTX, REFUSED, OK);
  assert.equal(p.kind, 'new-die');
  assert.equal(p.from, 'CIANO56 400_80 vadim 6 / L90-2x3ph-Al2O3-15t');
  assert.equal(p.config, 'L90-2x3ph-Al2O3-15t');
  assert.equal(p.duty, 'S1');
});

test('a locked configuration alone is a lock too', () => {
  const p = planLockedApply({ ...CTX, die_locked: false, config_locked: true }, REFUSED, OK);
  assert.equal(p.kind, 'new-die');
});

test('a running sweep blocks it, and the text says why and what to do', () => {
  const p = planLockedApply(CTX, REFUSED, { ...OK, sweepRunning: true });
  assert.equal(p.kind, 'blocked');
  assert.match(p.text, /locked/);
  assert.match(p.text, /tooth_width, cut_width/);
  assert.match(p.text, /still running/);
  assert.match(p.text, /Stop the sweep/);
});

test('a user who cannot write the catalog is told so', () => {
  const p = planLockedApply(CTX, REFUSED, { ...OK, canWrite: false });
  assert.equal(p.kind, 'blocked');
  assert.match(p.text, /may write the motor catalog/);
});

test('a refusal that is not a lock keeps the server reason', () => {
  const p = planLockedApply({ active: false }, [{ field: 'tooth_width', reason: 'out of range' }], OK);
  assert.equal(p.kind, 'blocked');
  assert.match(p.text, /tooth_width: out of range/);
});

test('an unlocked active die is not a lock', () => {
  const p = planLockedApply({ ...CTX, die_locked: false, config_locked: false }, REFUSED, OK);
  assert.equal(p.kind, 'blocked');
});

test('refusedText names fields, drops the placeholder one', () => {
  assert.equal(refusedText(REFUSED), "tooth_width: locked by die 'CIANO56 400_80 vadim 6'; cut_width: locked by die 'CIANO56 400_80 vadim 6'");
  assert.equal(refusedText([{ field: '(geometry)', reason: 'locked' }]), 'locked');
  assert.equal(refusedText(null), '');
});

test('the notice names both dies', () => {
  const t = newDieNotice('A / L1', 'A 2', 'L1', 'S1');
  assert.ok(t.includes("NEW die 'A 2'") && t.includes('A / L1 is untouched'));
});
