// node --test — the pure rule of lib/geometryApplyOutcome.ts, restated here
// verbatim (the repo's convention for node tests: `node --test` cannot load
// the TS modules, so the pure function under test is re-stated and kept in
// sync — see sweepResumeNotice.test.mjs for the same pattern).
//
// Bug this exists for (owner, 2026-09-19, CIANO14 50 / L15): the Sweep
// study's "Apply to geometry" PUT the picked point's tooth_width /
// magnet_fill_up, the backend refused with 423 (die+configuration locked —
// routes/family.py::geometry_lock_check), and motorStore.updateGeometryViaApi
// resolved that Promise normally (it never throws for a 422/423/500 — those
// are refusals, not outages).  SweepStudyPanel.applyPoint had no way to tell
// "written" from "refused, nothing changed" and reported "✓ applied" either
// way, while the variable cards correctly kept showing the UNCHANGED
// geometry (they read the live store, not a stale mount-time cache).
import test from 'node:test';
import assert from 'node:assert/strict';

function geometryApplyOutcome(status, requestedFields, namedIssues) {
  if (status >= 200 && status < 300) return { ok: true, refused: [] };
  if (status === 422 || status === 423 || status === 500) {
    const requested = new Set(requestedFields);
    const named = (namedIssues || [])
      .filter((i) => i && typeof i.field === 'string' && i.field && requested.has(String(i.field)))
      .map((i) => ({
        field: String(i.field),
        reason: String(i.reason ?? i.message ?? 'rejected'),
      }));
    const refused = named.length ? named
      : requestedFields.map((f) => ({ field: f, reason: 'rejected' }));
    return { ok: refused.length === 0, refused };
  }
  return { ok: true, refused: [] };
}

test('a 200 is always ok, regardless of what was requested', () => {
  const o = geometryApplyOutcome(200, ['tooth_width', 'magnet_fill_up'], null);
  assert.deepEqual(o, { ok: true, refused: [] });
});

test('423 with named locked fields refuses exactly those, by name', () => {
  const o = geometryApplyOutcome(423, ['tooth_width', 'magnet_fill_up'], [
    { field: 'tooth_width', reason: "locked by die 'CIANO14_50'" },
    { field: 'magnet_fill_up', reason: "locked by die 'CIANO14_50'" },
  ]);
  assert.equal(o.ok, false);
  assert.deepEqual(o.refused.map(r => r.field).sort(), ['magnet_fill_up', 'tooth_width']);
  assert.ok(o.refused.every(r => r.reason.includes('locked')));
});

test('423 with a partial lock: only the actually-refused field is named', () => {
  // A configuration lock only guards non-free keys — a request that mixes a
  // free key (e.g. wire size) with a locked one gets ONE field back.
  const o = geometryApplyOutcome(423, ['tooth_width', 'wire_width'], [
    { field: 'tooth_width', reason: "locked by die 'CIANO14_50'" },
  ]);
  assert.equal(o.ok, false);
  assert.deepEqual(o.refused.map(r => r.field), ['tooth_width']);
});

test('422 (bad value) refuses the named field with its message', () => {
  const o = geometryApplyOutcome(422, ['tooth_width'], [
    { field: 'tooth_width', message: 'must be positive' },
  ]);
  assert.equal(o.ok, false);
  assert.deepEqual(o.refused, [{ field: 'tooth_width', reason: 'must be positive' }]);
});

test('a 423/422/500 with NO per-field detail refuses every requested field, never overstates success', () => {
  for (const status of [422, 423, 500]) {
    const o = geometryApplyOutcome(status, ['tooth_width', 'magnet_fill_up'], null);
    assert.equal(o.ok, false, `status ${status}`);
    assert.deepEqual(o.refused.map(r => r.field).sort(), ['magnet_fill_up', 'tooth_width'], `status ${status}`);
  }
});

test('a 500 with no requested fields (edge case) is trivially ok — nothing to refuse', () => {
  const o = geometryApplyOutcome(500, [], null);
  assert.deepEqual(o, { ok: true, refused: [] });
});

test('an outage (502/503/504) or a thrown fetch (status 0) is NOT a refusal — applied locally, queued', () => {
  for (const status of [0, 502, 503, 504]) {
    const o = geometryApplyOutcome(status, ['tooth_width'], null);
    assert.deepEqual(o, { ok: true, refused: [] }, `status ${status}`);
  }
});

test('an empty overrides request is always ok (nothing was asked for)', () => {
  const o = geometryApplyOutcome(423, [], [{ field: '(geometry)', reason: 'locked' }]);
  // No requested field named tooth_width etc. — the generic issue does not
  // match anything the caller asked for, so nothing of THEIRS was refused.
  assert.deepEqual(o, { ok: true, refused: [] });
});
