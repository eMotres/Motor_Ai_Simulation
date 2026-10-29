// node --test — the rule that decides what the Sweep tab may draw as its result.
// Owner 2026-10-29: after a sweep run the tab showed a stale result whose points
// had no point ids.  Loads the TS module directly (Node >= 22.6 type stripping).
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  judgeResult, pointsHaveIds, sameMachine, machineLabel, previousRunLabel,
  partialFromProgress, isPersistable, packForStore, unpackFromStore, notShownText,
} from '../sweepRestore.ts';

const M_A = { fingerprint: 'fp-A', die: 'CIANO56', config: 'L100', duty: 'S1' };
const M_B = { fingerprint: 'fp-B', die: 'G2', config: 'L40' };
const pt = (g, o) => ({ geom_id: g, op_index: o, feasible: true });
const res = (over = {}) => ({ run_id: 'r1', points: [pt(0, 0), pt(1, 0)], machine: M_A, ...over });

test('points are identified only when EVERY point has geom_id and op_index', () => {
  assert.equal(pointsHaveIds([pt(0, 0), pt(1, 2)]), true);
  assert.equal(pointsHaveIds([pt(0, 0), { feasible: true }]), false);
  assert.equal(pointsHaveIds([{ geom_id: 0 }]), false);
  assert.equal(pointsHaveIds([{ geom_id: '0', op_index: 0 }]), false);
  assert.equal(pointsHaveIds([]), false);
  assert.equal(pointsHaveIds(undefined), false);
});

test('a result with idless points is never drawn - not as current, not as previous', () => {
  const bad = res({ points: [{ T_em_Nm: 1 }] });
  assert.deepEqual(judgeResult(bad, { wantRunId: 'r1', machineNow: M_A }), { show: false, why: 'no-ids' });
  assert.deepEqual(judgeResult(bad, { wantRunId: '', machineNow: M_A }), { show: false, why: 'no-ids' });
  assert.deepEqual(judgeResult(bad, { wantRunId: '', machineNow: M_A, userChose: true }), { show: false, why: 'no-ids' });
});

test('the run we are waiting for is current; any other run is not an answer', () => {
  assert.deepEqual(judgeResult(res(), { wantRunId: 'r1', machineNow: M_A }),
    { show: true, kind: 'current', machine: 'CIANO56 / L100 / S1', foreign: false });
  assert.deepEqual(judgeResult(res({ run_id: 'old' }), { wantRunId: 'r1', machineNow: M_A }),
    { show: false, why: 'other-run' });
});

test('a result with no run id is refused', () => {
  assert.deepEqual(judgeResult(res({ run_id: '' }), { wantRunId: '', machineNow: M_A }),
    { show: false, why: 'no-run-id' });
});

test('on mount a same-machine result is shown as the PREVIOUS run, never as current', () => {
  const v = judgeResult(res({ restored_from_disk: true }), { wantRunId: '', machineNow: M_A });
  assert.equal(v.show, true);
  assert.equal(v.kind, 'previous');
  assert.equal(v.foreign, false);
  assert.equal(previousRunLabel(v), 'Previous run of CIANO56 / L100 / S1');
});

test('another machine is hidden on mount, but shown - labelled - when the user reopened it', () => {
  const r = res({ machine: M_B });
  assert.deepEqual(judgeResult(r, { wantRunId: '', machineNow: M_A }), { show: false, why: 'foreign-machine' });
  const v = judgeResult(r, { wantRunId: '', machineNow: M_A, userChose: true });
  assert.equal(v.show, true);
  assert.equal(v.foreign, true);
  assert.equal(previousRunLabel(v), 'Previous run of G2 / L40 — not the machine loaded now');
});

test('a run that finished on another machine than the one on screen is not shown as current', () => {
  assert.deepEqual(judgeResult(res({ machine: M_B }), { wantRunId: 'r1', machineNow: M_A }),
    { show: false, why: 'foreign-machine' });
});

test('an unstamped result is hidden unless the user chose it, then it says machine unknown', () => {
  const r = res({ machine: { unattributed: true, fingerprint: '' } });
  assert.deepEqual(judgeResult(r, { wantRunId: '', machineNow: M_A }), { show: false, why: 'unstamped' });
  const v = judgeResult(r, { wantRunId: '', machineNow: M_A, userChose: true });
  assert.equal(v.show, true);
  assert.equal(v.machine, 'machine unknown');
  assert.equal(v.foreign, true);
  assert.deepEqual(judgeResult(res({ machine: undefined }), { wantRunId: '', machineNow: M_A }),
    { show: false, why: 'unstamped' });
});

test('fingerprints compare as strings and an empty one never matches', () => {
  assert.equal(sameMachine({ machine: { fingerprint: 'x' } }, { fingerprint: 'x' }), true);
  assert.equal(sameMachine({ machine: { fingerprint: '' } }, { fingerprint: '' }), false);
  assert.equal(sameMachine({}, { fingerprint: 'x' }), false);
  assert.equal(machineLabel({}), 'machine unknown');
});

test('live partial points are wrapped with the run and machine and are not persistable', () => {
  const p = partialFromProgress({ run_id: 'r9', points: [pt(0, 0)], machine: M_A });
  assert.equal(p.partial, true);
  assert.equal(p.run_id, 'r9');
  assert.equal(isPersistable(p), false);
  assert.equal(judgeResult(p, { wantRunId: 'r9', machineNow: M_A }).show, true);
  // a partial that does not belong to the awaited run is not drawn
  assert.deepEqual(judgeResult(p, { wantRunId: 'other', machineNow: M_A }), { show: false, why: 'other-run' });
});

test('the browser copy: only a finished, stamped, identified result is kept; old-format blobs are ignored', () => {
  assert.equal(isPersistable(res()), true);
  assert.equal(isPersistable(res({ machine: undefined })), false);
  assert.equal(isPersistable(res({ points: [{ T_em_Nm: 1 }] })), false);
  assert.deepEqual(unpackFromStore(packForStore(res())), res());
  // what the old code wrote: a bare partial `{ points }`
  assert.equal(unpackFromStore(JSON.stringify({ points: [pt(0, 0)] })), null);
  assert.equal(unpackFromStore('not json'), null);
  assert.equal(unpackFromStore(null), null);
});

test('every refusal has a sentence', () => {
  for (const why of ['no-ids', 'no-run-id', 'unstamped', 'foreign-machine', 'other-run']) {
    assert.ok(notShownText(why, res({ machine: M_B })).length > 10, why);
  }
  assert.ok(notShownText('foreign-machine', res({ machine: M_B })).includes('G2 / L40'));
});

test('an in-flight batch of the awaited run is drawn even if its stamp differs from machine_now', () => {
  const p = partialFromProgress({ run_id: 'r9', points: [pt(0, 0)], machine: M_B });
  assert.equal(judgeResult(p, { wantRunId: 'r9', machineNow: M_A }).show, true);
  // but a FINISHED result for another machine is still refused
  assert.equal(judgeResult(res({ run_id: 'r9', machine: M_B }), { wantRunId: 'r9', machineNow: M_A }).show, false);
});
