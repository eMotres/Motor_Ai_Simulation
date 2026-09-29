// node --test — the pure rules behind "follow the machine another browser
// loaded" (lib/familyFollow.adoptionDecision, lib/dutyLocalApply.machineChanged
// and .coupledTempsForNewMachine), copied verbatim (the repo's convention for
// node tests: `node --test` cannot load the TS modules, so the pure functions
// under test are re-stated here and kept in sync).
//
// What they pin, in the order it matters:
//
//   • a browser that has never applied a duty SEEDS its baseline and touches
//     nothing — an F5 must not undo the point the user drifted to on purpose;
//   • a context naming a die/config/duty this browser has not applied is
//     ADOPTED (the 2026-09-08 23:23 incident in one assertion);
//   • an ordinary user never follows the OWNER's context;
//   • a released context is remembered as released, so the ▶ that follows
//     counts as a fresh load even when it names the same duty;
//   • the coupled loop's two temperature fields are reset on a machine change —
//     the duty's own winding temperature when its summary carries one, the
//     panel default otherwise, and the magnet field always cleared.
import test from 'node:test';
import assert from 'node:assert/strict';

// ── lib/familyFollow.ts ──────────────────────────────────────────────────────
function adoptionDecision(ctx, applied) {
  if (!ctx) return 'skip';
  if (ctx.active !== true) return applied ? 'release' : 'skip';
  if (ctx.can_write !== true) return 'skip';
  if (!ctx.die || !ctx.config) return 'skip';
  if (!applied) return 'seed';
  const newer = !!(ctx.at && applied.at) && String(ctx.at) > String(applied.at);
  const older = !!(ctx.at && applied.at) && String(ctx.at) < String(applied.at);
  if (older) return 'skip';
  const changed = ctx.die !== applied.die
    || ctx.config !== applied.config
    || (ctx.duty ?? null) !== (applied.duty ?? null);
  if (!changed && !applied.released && !newer) return 'skip';
  if (!ctx.duty) return 'seed';
  return 'adopt';
}

// ── lib/dutyLocalApply.ts ────────────────────────────────────────────────────
const PANEL_COIL_TEMP_C = 120;

function machineChanged(prev, die, cfg) {
  if (!prev || !prev.die) return false;
  if (prev.die !== die) return true;
  return prev.config != null && prev.config !== cfg;
}

function coupledTempsForNewMachine(duty) {
  const s = duty?.summary ?? null;
  const raw = s ? (s.coil_temp_C ?? s.coil_temp_c) : undefined;
  const c = Number(raw);
  return {
    coilTemp: raw != null && Number.isFinite(c) ? Math.round(c * 10) / 10
                                                : PANEL_COIL_TEMP_C,
    magnetTempC: '',
  };
}

const OWNER = { active: true, can_write: true };
const applied = (die, config, duty, extra = {}) =>
  ({ die, config, duty, appliedAt: 1, ...extra });

test('a browser that never applied a duty seeds its baseline, it does not adopt', () => {
  const ctx = { ...OWNER, die: 'CILN28', config: 'G2-L40', duty: 'peak' };
  assert.equal(adoptionDecision(ctx, null), 'seed');
});

test('the same die/config/duty is already on this panel — nothing to do', () => {
  const ctx = { ...OWNER, die: 'CILN28', config: 'G2-L40', duty: 'peak' };
  assert.equal(adoptionDecision(ctx, applied('CILN28', 'G2-L40', 'peak')), 'skip');
});

test('another machine in the header than the one this panel applied — adopt', () => {
  // The 2026-09-08 23:23 incident: the panel still held the Ø200 duty.
  const ctx = { ...OWNER, die: 'CILN28', config: 'G2-L40', duty: 'peak' };
  assert.equal(adoptionDecision(ctx, applied('M200', 'S3-L200', 'cont')), 'adopt');
});

test('another DUTY of the same configuration is a different point — adopt', () => {
  const ctx = { ...OWNER, die: 'CILN28', config: 'G2-L40', duty: 'cont' };
  assert.equal(adoptionDecision(ctx, applied('CILN28', 'G2-L40', 'peak')), 'adopt');
});

test('an ordinary user never follows the owner’s context', () => {
  const ctx = { active: true, can_write: false, die: 'CILN28', config: 'G2-L40', duty: 'peak' };
  assert.equal(adoptionDecision(ctx, applied('M200', 'S3-L200', 'cont')), 'skip');
});

test('a failed poll says nothing', () => {
  assert.equal(adoptionDecision(null, applied('M200', 'S3-L200', 'cont')), 'skip');
  assert.equal(adoptionDecision(undefined, null), 'skip');
});

test('a released context is remembered as released; the next ▶ is then a fresh load', () => {
  const rel = { active: false, can_write: true, released_from: 'M200' };
  assert.equal(adoptionDecision(rel, applied('M200', 'S3-L200', 'cont')), 'release');
  assert.equal(adoptionDecision(rel, null), 'skip');
  const ctx = { ...OWNER, die: 'M200', config: 'S3-L200', duty: 'cont' };
  assert.equal(
    adoptionDecision(ctx, applied('M200', 'S3-L200', 'cont', { released: true })),
    'adopt');
});

test('a configuration activated with NO duty carries no operating point', () => {
  const ctx = { ...OWNER, die: 'CILN28', config: 'G2-L40', duty: null };
  assert.equal(adoptionDecision(ctx, applied('M200', 'S3-L200', 'cont')), 'seed');
});

test('a context OLDER than the one applied is never adopted', () => {
  const ctx = { ...OWNER, die: 'M200', config: 'S3-L200', duty: 'cont',
                at: '2026-09-08T22:00:00' };
  const a = applied('CILN28', 'G2-L40', 'peak', { at: '2026-09-08T23:05:00' });
  assert.equal(adoptionDecision(ctx, a), 'skip');
  // …and the same context, newer, is
  assert.equal(
    adoptionDecision({ ...ctx, at: '2026-09-08T23:23:00' }, a), 'adopt');
});

test('machineChanged: die, configuration, and the unknown previous machine', () => {
  assert.equal(machineChanged({ die: 'M200', config: 'S3-L200' }, 'CILN28', 'G2-L40'), true);
  assert.equal(machineChanged({ die: 'CILN28', config: 'G2-L40' }, 'CILN28', 'G2-L60'), true);
  // another DUTY of the same build is the same machine — its temperatures are
  // restored by the duty's own overlay, not reset
  assert.equal(machineChanged({ die: 'CILN28', config: 'G2-L40' }, 'CILN28', 'G2-L40'), false);
  // nothing remembered: the numbers on the panel are the user's own work
  assert.equal(machineChanged(null, 'CILN28', 'G2-L40'), false);
  assert.equal(machineChanged({ die: null, config: null }, 'CILN28', 'G2-L40'), false);
  // a die remembered without a configuration still decides on the die alone
  assert.equal(machineChanged({ die: 'CILN28', config: null }, 'CILN28', 'G2-L40'), false);
});

test('the coupled fields on a machine change: the duty’s own coil temp, no magnet temp', () => {
  assert.deepEqual(coupledTempsForNewMachine({ summary: { coil_temp_C: 96.44 } }),
                   { coilTemp: 96.4, magnetTempC: '' });
  // the panel's own convention, for a duty whose summary predates the field
  assert.deepEqual(coupledTempsForNewMachine({ summary: { rpm: 3000 } }),
                   { coilTemp: 120, magnetTempC: '' });
  assert.deepEqual(coupledTempsForNewMachine({}),
                   { coilTemp: 120, magnetTempC: '' });
  assert.deepEqual(coupledTempsForNewMachine(null),
                   { coilTemp: 120, magnetTempC: '' });
  // a garbage value is not a temperature
  assert.deepEqual(coupledTempsForNewMachine({ summary: { coil_temp_C: 'hot' } }),
                   { coilTemp: 120, magnetTempC: '' });
  // 0 °C is a real answer and must survive the ?? / falsy trap
  assert.deepEqual(coupledTempsForNewMachine({ summary: { coil_temp_C: 0 } }),
                   { coilTemp: 0, magnetTempC: '' });
});
