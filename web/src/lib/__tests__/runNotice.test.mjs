// node --test — the pure rule of lib/runNotice.ts, restated here verbatim (the
// repo's convention for node tests: `node --test` cannot load the TS modules,
// so the pure function under test is re-stated and kept in sync).
//
// The case that made it exist: on production 2026-09-16 a coupled Run was
// refused 422 ("duty 'peak' is an S3 duty …") and the panel showed NOTHING
// where the user clicked — the sentence rendered far below the fold and the
// Run button just flicked back to "Re-run Simulation".
//
// That refusal is gone since the same afternoon (the coupled loop FINDS the
// duty ratio now), and what arrives in its place is the loop's OUTCOME when the
// ratio the duty asks for does not fit.  It is an answer, not a failure, so it
// carries the `Duty cycle:` prefix `coupledApi.coupledRegimeNotice` writes and
// is classified INFO — the last test below is that contract.
import test from 'node:test';
import assert from 'node:assert/strict';

const LINE = 160;

function runNoticeFor(raw) {
  if (raw == null) return null;
  let m = String(raw).trim();
  if (!m) return null;
  if (/^cancelled\.?$/i.test(m)) return null;
  m = m.replace(/^Error:\s*/i, '').replace(/^HTTPException:\s*\d+:\s*/i, '').trim();
  if (m.startsWith('{')) {
    try {
      const d = JSON.parse(m)?.detail;
      const inner = typeof d === 'string' ? d : d?.error;
      if (typeof inner === 'string' && inner.trim()) m = inner.trim();
    } catch { /* not JSON after all */ }
  }
  if (!m) return null;
  const kind = /reconnecting and re-solving/i.test(m) || /^duty cycle:/i.test(m)
    ? 'info' : 'error';
  const text = m.length > LINE ? `${m.slice(0, LINE - 1).trimEnd()}…` : m;
  return { text, full: m, kind };
}

const S3 = "duty 'peak' is an S3 duty — 25.0 % of a 60.0 s cycle, not a point the "
  + 'machine sits at. The coupled loop iterates the electromagnetic run and the '
  + 'thermal solve until they SETTLE.';

test('a refusal reaches the button, sentence intact', () => {
  const n = runNoticeFor(`Error: ${S3}`);
  assert.ok(n, 'a refusal must produce a notice');
  assert.equal(n.kind, 'error');
  assert.equal(n.full, S3, 'the full sentence is kept for the tooltip');
  assert.ok(n.text.startsWith("duty 'peak' is an S3 duty"),
    'the line starts with the reason, not with "Error:"');
  assert.ok(!/^Error:/.test(n.text), 'the Error envelope is stripped');
});

test('a long refusal is trimmed to one line but never lost', () => {
  const n = runNoticeFor(`Error: ${S3}`);
  assert.ok(n.text.length <= LINE, `line is ${n.text.length}, cap is ${LINE}`);
  assert.ok(n.text.endsWith('…'), 'a trimmed line says it was trimmed');
  assert.ok(n.full.length > n.text.length, 'the tooltip carries the rest');
});

test('a short refusal is shown whole, with no ellipsis', () => {
  const n = runNoticeFor('Error: solve refused — no conducting region');
  assert.equal(n.text, 'solve refused — no conducting region');
  assert.equal(n.text, n.full);
});

test('the backend HTTPException envelope is stripped too', () => {
  assert.equal(runNoticeFor('HTTPException: 422: single frame cannot iterate').text,
    'single frame cannot iterate');
});

test('a raw JSON body shows the sentence inside it, not the JSON', () => {
  assert.equal(runNoticeFor('{"detail": "the loop needs more than one frame"}').text,
    'the loop needs more than one frame');
  assert.equal(runNoticeFor('{"detail": {"error": "nothing is cooled"}}').text,
    'nothing is cooled');
});

test('a body that only looks like JSON is left alone', () => {
  const n = runNoticeFor('{not json after all');
  assert.equal(n.text, '{not json after all');
  assert.equal(n.kind, 'error');
});

test('the user pressing Stop is not an error line', () => {
  assert.equal(runNoticeFor('Cancelled.'), null);
  assert.equal(runNoticeFor('cancelled'), null);
});

test('nothing to say stays nothing', () => {
  assert.equal(runNoticeFor(null), null);
  assert.equal(runNoticeFor(undefined), null);
  assert.equal(runNoticeFor(''), null);
  assert.equal(runNoticeFor('   '), null);
});

test('a retry in flight reads as progress, not as failure', () => {
  const n = runNoticeFor('Backend connection lost — the running solve died with it; '
    + 'reconnecting and re-solving (attempt 2/9)…');
  assert.equal(n.kind, 'info');
});

test('a dead backend after the retries are spent IS a failure', () => {
  const n = runNoticeFor('Backend connection lost mid-solve — the run DIED with a '
    + 'server restart and nothing was updated. Press Re-run Simulation.');
  assert.equal(n.kind, 'error');
});

test('a duty cycle that does not fit is an ANSWER, not a failure', () => {
  const n = runNoticeFor('Duty cycle: 21.6 % of a 60 s cycle (13 s on) is '
    + 'allowable, limited by the winding at 200 °C; the 25 % asked for does NOT '
    + 'fit under it.');
  assert.equal(n.kind, 'info', 'the loop solved the machine — this is its answer');
  assert.ok(n.text.startsWith('Duty cycle: 21.6 %'));
});

test('…and a duty cycle with no feasible ratio is still an answer', () => {
  const n = runNoticeFor('Duty cycle: no duty ratio is allowable at this '
    + 'operating point: even the shortest pull the search tries puts the winding '
    + 'over 200 °C. One pull from 40 °C ambient lasts 19.8 s.');
  assert.equal(n.kind, 'info');
  assert.ok(n.full.includes('One pull from 40 °C ambient lasts 19.8 s.'));
});
