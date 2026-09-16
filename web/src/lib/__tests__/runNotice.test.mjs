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

const HTML_BODY = /<!doctype\s|<html[\s>]/i;
const GATEWAY_WORDS = /bad gateway|gateway time-?out|service (temporarily )?unavailable/i;
const GATEWAY_STATUS =
  /^(?:error:\s*)?(?:httpexception:\s*)?(?:http\s*)?(50[234])\b/i;

const RESTARTING = 'server is restarting — try again in a minute';

function gatewayCode(raw, stripped) {
  for (const s of [raw, stripped]) {
    const m = GATEWAY_STATUS.exec(s.trim());
    if (m) return m[1];
    const w = /\b(50[234])\b[^0-9]{0,24}(bad gateway|gateway time-?out|service (temporarily )?unavailable)/i.exec(s);
    if (w) return w[1];
  }
  return null;
}

function isGatewayOutage(raw, stripped) {
  return HTML_BODY.test(raw) || HTML_BODY.test(stripped)
    || GATEWAY_WORDS.test(raw) || GATEWAY_WORDS.test(stripped)
    || GATEWAY_STATUS.test(raw.trim()) || GATEWAY_STATUS.test(stripped.trim());
}

function runNoticeFor(raw) {
  if (raw == null) return null;
  const original = String(raw).trim();
  let m = original;
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
  if (isGatewayOutage(original, m)) {
    const code = gatewayCode(original, m);
    return {
      text: RESTARTING,
      full: `The API did not answer this run: the gateway replied `
        + `${code ? `${code} ` : 'with an error page '}`
        + `instead, which is what a redeploy or a restart looks like from here. `
        + `Nothing was solved and nothing was changed — press Run again in a `
        + `minute.`,
      kind: 'info',
    };
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

// ── the gateway, not the solver (production, 2026-09-16) ────────────────────
// An API redeploy was under way and nginx answered the run with its own page:
// the notice printed `⚠ not solved — <html><head><title>502 Bad Gateway…`
// verbatim, under a Run button, to an engineer reading it for a motor.

const NGINX_502 = '<html>\r\n<head><title>502 Bad Gateway</title></head>\r\n'
  + '<body>\r\n<center><h1>502 Bad Gateway</h1></center>\r\n'
  + '<hr><center>nginx/1.25.3</center>\r\n</body>\r\n</html>\r\n';

test('an nginx 502 page becomes one sentence, and never markup', () => {
  const n = runNoticeFor(NGINX_502);
  assert.equal(n.text, 'server is restarting — try again in a minute');
  assert.equal(n.kind, 'info', 'the outage path retries — this is progress');
  assert.ok(!/</.test(n.text), 'no markup on the line');
  assert.ok(!/<html|<head|<title|<center/i.test(n.full), 'no markup in the tooltip');
  assert.ok(n.full.includes('502'), 'the tooltip still names the code');
});

test('a DOCTYPE page is caught the same way', () => {
  const n = runNoticeFor('<!DOCTYPE html><html><body>503 Service Unavailable</body></html>');
  assert.equal(n.text, 'server is restarting — try again in a minute');
  assert.equal(n.kind, 'info');
  assert.ok(!/</.test(n.full));
});

test('a bare 502/503/504 status, with no body at all', () => {
  for (const code of ['502', '503', '504']) {
    const n = runNoticeFor(`HTTPException: ${code}: `);
    assert.equal(n.text, 'server is restarting — try again in a minute', code);
    assert.equal(n.kind, 'info', code);
    assert.ok(n.full.includes(code), `the tooltip names ${code}`);
  }
  assert.equal(runNoticeFor('Error: HTTP 504 Gateway Time-out').kind, 'info');
  assert.equal(runNoticeFor('502 Bad Gateway').text,
    'server is restarting — try again in a minute');
});

test('…but a 502 inside a solver sentence is still the solver talking', () => {
  const n = runNoticeFor('Error: the winding reached 502 A before the Newton '
    + 'step converged — reduce the current');
  assert.equal(n.kind, 'error', 'a number in a sentence is not a status code');
  assert.ok(n.text.startsWith('the winding reached 502 A'));
});

test('a 500 is the API itself failing, and is reported as a failure', () => {
  const n = runNoticeFor('HTTPException: 500: no conducting region in the mesh');
  assert.equal(n.kind, 'error');
  assert.equal(n.text, 'no conducting region in the mesh');
});

test('…and a duty cycle with no feasible ratio is still an answer', () => {
  const n = runNoticeFor('Duty cycle: no duty ratio is allowable at this '
    + 'operating point: even the shortest pull the search tries puts the winding '
    + 'over 200 °C. One pull from 40 °C ambient lasts 19.8 s.');
  assert.equal(n.kind, 'info');
  assert.ok(n.full.includes('One pull from 40 °C ambient lasts 19.8 s.'));
});
