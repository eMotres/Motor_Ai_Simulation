/**
 * The report ring's state machine, tested without a browser.
 *
 * User, 2026-09-16: *"нужно сделать ещё минимальный прогресс-ринг генерации
 * отчёта, чтобы было видно, что работает, а не висит"*.  What makes that ring
 * worth having is not the circle — it is four rules:
 *
 *   1. it MOVES from the click, before any answer has arrived (indeterminate),
 *      because a second of nothing where the user pressed is the bug itself;
 *   2. it turns into a real percentage the moment the backend says a fraction;
 *   3. it never goes BACKWARDS — the build's total legitimately grows when a
 *      machine has more figures than the last one, and a ring that slides back
 *      reads as a restart;
 *   4. a failure becomes the run-notice line, one sentence, full text in the
 *      tooltip — never a ring that simply stops.
 *
 * The module is TypeScript and imports `lib/runNotice`, which `node --test`
 * cannot load, so the shipped functions are re-implemented verbatim below —
 * the repo's convention for these tests (see common/__tests__/progressStrip).
 * Changing `reportProgress.ts` has to change this file too, and that is the
 * moment somebody has to justify the new behaviour.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

/* ── verbatim copy of lib/runNotice (the error branch needs it) ──────────── */

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

/* ── verbatim copy of the shipped state machine ──────────────────────────── */

const IDLE = { runId: null, key: '', pct: null, stage: '', label: '', elapsedS: 0, notice: null };

const STAGE_WORDS = {
  records: 'reading stored results',
  figures: 'drawing figures',
  tables: 'tables',
  docx: 'writing the Word file',
  pdf: 'typesetting the PDF',
  done: 'done',
  failed: 'failed',
  unknown: 'starting…',
};

function ringStart(key, runId) {
  return { runId, key, pct: null, stage: 'unknown', label: STAGE_WORDS.unknown,
    elapsedS: 0, notice: null };
}

function ringPoll(prev, p, sinceS) {
  if (prev.runId == null) return prev;
  const next = { ...prev, elapsedS: Math.max(0, sinceS) };
  if (!p) return next;
  if (p.error) return ringFail(prev, String(p.error));
  const stage = String(p.stage || '') || prev.stage;
  next.stage = stage;
  next.label = p.phase && stage === 'figures'
    ? String(p.phase)
    : (STAGE_WORDS[stage] ?? String(p.phase || '') ?? '');
  if (p.done) { next.pct = 100; return next; }
  if (stage === 'unknown') return next;
  const frac = typeof p.frac === 'number' ? p.frac
    : (p.total ? (p.step ?? 0) / p.total : NaN);
  if (!Number.isFinite(frac)) return next;
  const pct = Math.max(0, Math.min(100, Math.round(frac * 100)));
  next.pct = prev.pct == null ? pct : Math.max(prev.pct, pct);
  return next;
}

function ringFail(prev, message) {
  return { ...prev, runId: null, pct: null, stage: 'failed',
    label: STAGE_WORDS.failed,
    notice: runNoticeFor(message) ?? { text: 'Report failed', kind: 'error', full: String(message) } };
}

function ringDone(_prev) { return IDLE; }

function ringBusy(r, key) { return r.runId != null && r.key === key; }

function ringTip(r) {
  if (r.notice) return r.notice.full;
  if (r.runId == null) return '';
  const secs = `${Math.round(r.elapsedS)} s`;
  const pct = r.pct == null ? '' : ` · ${r.pct} %`;
  return `${r.label || 'working'}${pct} · ${secs}`;
}

const KEY = 'report:CIANO10 200 opt/L180 gen';

/* ── idle → indeterminate ────────────────────────────────────────────────── */

test('nothing is drawn until a report is asked for', () => {
  assert.equal(ringBusy(IDLE, KEY), false);
  assert.equal(ringTip(IDLE), '');
});

test('the click alone makes the ring move — no answer needed', () => {
  const r = ringStart(KEY, 'rep-1');
  assert.ok(ringBusy(r, KEY), 'the button that was pressed owns the ring');
  assert.equal(ringBusy(r, 'report:pdf:CIANO10 200 opt/L180 gen'), false,
    'and only that button');
  assert.equal(r.pct, null, 'no percentage yet = the ring SPINS');
  assert.match(ringTip(r), /starting/);
});

test('a poll that never arrives keeps the clock moving, and no error', () => {
  const r = ringPoll(ringStart(KEY, 'rep-1'), null, 3.4);
  assert.equal(r.pct, null);
  assert.equal(r.notice, null, 'a lost poll is not a failed build');
  assert.equal(Math.round(r.elapsedS), 3);
});

test('an id the server has not opened yet still spins', () => {
  // The first poll routinely beats the build's first line: the backend answers
  // the idle shape with stage "unknown", and snapping to 0 % there would make
  // the ring look stuck before it ever started.
  const r = ringPoll(ringStart(KEY, 'rep-1'),
    { stage: 'unknown', running: false, frac: 0 }, 1);
  assert.equal(r.pct, null);
});

/* ── indeterminate → determinate ─────────────────────────────────────────── */

test('the first real fraction turns the ring into a percentage', () => {
  let r = ringStart(KEY, 'rep-1');
  r = ringPoll(r, { stage: 'records', running: true, frac: 0.04,
    phase: 'reading stored results' }, 1);
  assert.equal(r.pct, 4);
  assert.match(ringTip(r), /reading stored results · 4 % · 1 s/);
});

test('the figure stage counts k / N in the tooltip', () => {
  let r = ringStart(KEY, 'rep-1');
  r = ringPoll(r, { stage: 'figures', running: true, frac: 0.33,
    phase: 'figure 7 / 20' }, 12);
  assert.equal(r.pct, 33);
  assert.equal(r.label, 'figure 7 / 20',
    'the backend names the figure; the client never guesses the count');
  assert.match(ringTip(r), /figure 7 \/ 20 · 33 % · 12 s/);
});

test('step / total is used when the backend sends no frac', () => {
  const r = ringPoll(ringStart(KEY, 'rep-1'),
    { stage: 'figures', running: true, step: 5, total: 20 }, 9);
  assert.equal(r.pct, 25);
});

test('the ring never goes backwards when the build grows its total', () => {
  let r = ringStart(KEY, 'rep-1');
  r = ringPoll(r, { stage: 'figures', running: true, frac: 0.60 }, 30);
  assert.equal(r.pct, 60);
  // More figures than budgeted: the honest fraction drops, the ring does not.
  r = ringPoll(r, { stage: 'figures', running: true, frac: 0.52 }, 33);
  assert.equal(r.pct, 60, 'a ring that slides back reads as a restart');
});

/* ── determinate → done ──────────────────────────────────────────────────── */

test('the last stages are named, and done reads 100 %', () => {
  let r = ringStart(KEY, 'rep-1');
  r = ringPoll(r, { stage: 'tables', running: true, frac: 0.9 }, 48);
  assert.equal(r.label, 'tables');
  r = ringPoll(r, { stage: 'docx', running: true, frac: 0.95 }, 50);
  assert.equal(r.label, 'writing the Word file');
  r = ringPoll(r, { stage: 'done', running: false, done: true, frac: 1 }, 52);
  assert.equal(r.pct, 100);
  // …and when the FILE has arrived the button gets its caption back.
  assert.deepEqual(ringDone(r), IDLE);
  assert.equal(ringBusy(ringDone(r), KEY), false);
});

test('the pdf build names its own last stage', () => {
  const r = ringPoll(ringStart('report:pdf:D/C', 'rep-2'),
    { stage: 'pdf', running: true, frac: 0.97 }, 70);
  assert.equal(r.label, 'typesetting the PDF');
});

/* ── the error branch ────────────────────────────────────────────────────── */

test('a failed build becomes one short sentence, full text in the tooltip', () => {
  const long = 'report build failed: ' + 'the thermal store holds a result of a '
    + 'different machine and the section refuses to quote it, '.repeat(4);
  const r = ringFail(ringStart(KEY, 'rep-1'), long);
  assert.equal(r.runId, null, 'the ring stops being a ring');
  assert.equal(r.pct, null);
  assert.ok(r.notice, 'the failure is a notice');
  assert.equal(r.notice.kind, 'error');
  assert.ok(r.notice.text.length <= LINE, 'one line, never a wall of text');
  assert.ok(r.notice.text.endsWith('…'), 'the rest is in the tooltip');
  assert.equal(ringTip(r), r.notice.full);
  assert.equal(ringBusy(r, KEY), false, 'and the button is clickable again');
});

test('a refusal the POLL saw ends the ring exactly the same way', () => {
  const r = ringPoll(ringStart(KEY, 'rep-1'),
    { stage: 'failed', done: true, error: "duty 'peak' is not in this configuration" }, 8);
  assert.ok(r.notice);
  assert.equal(r.notice.text, "duty 'peak' is not in this configuration");
  assert.equal(r.runId, null);
});

test('a poll answering about an older run is ignored once the ring is idle', () => {
  const r = ringPoll(IDLE, { stage: 'figures', frac: 0.5 }, 5);
  assert.deepEqual(r, IDLE);
});
