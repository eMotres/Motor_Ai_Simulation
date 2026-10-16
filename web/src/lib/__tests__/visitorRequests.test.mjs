// node --test — the pure rules of lib/visitorRequests.ts, restated here verbatim
// (the repo's convention for node tests: `node --test` cannot load the TS
// modules, so the pure functions under test are re-stated and kept in sync).
//
// What they are for: the Admin tab carries a badge with the number of NEW
// visitor requests, and that number is computed from a PARSED HTTP BODY. On a
// 401/403 that body is an error object, not a list — `newRequestCount` has to
// answer 0 rather than throw inside a render, which is the first test below.
//
// The other rule worth stating is the summary fallback. A filed request has a
// distilled `note` most of the time, but the backend files one the moment it has
// contact details, note or not — and then the row must still say something the
// admin can act on, which is the last thing the visitor typed.
import test from 'node:test';
import assert from 'node:assert/strict';

const REQUEST_STATUSES = ['new', 'contacted', 'invited', 'declined'];

const SUMMARY_MAX = 120;

function newRequestCount(requests) {
  if (!Array.isArray(requests)) return 0;
  let n = 0;
  for (const r of requests) if (r && r.status === 'new') n += 1;
  return n;
}

function clip(s, max) {
  const t = s.trim().replace(/\s+/g, ' ');
  if (t.length <= max) return t;
  const cut = t.slice(0, max - 1);
  const sp = cut.lastIndexOf(' ');
  return `${(sp > max * 0.6 ? cut.slice(0, sp) : cut).trimEnd()}…`;
}

function requestSummary(r) {
  const note = typeof r?.note === 'string' ? r.note.trim() : '';
  if (note) return clip(note, SUMMARY_MAX);
  const turns = Array.isArray(r?.transcript) ? r.transcript : [];
  for (let i = turns.length - 1; i >= 0; i -= 1) {
    const t = turns[i];
    if (t && t.role === 'user' && typeof t.content === 'string' && t.content.trim()) {
      return clip(t.content, SUMMARY_MAX);
    }
  }
  return '(no message)';
}

function contactLine(r) {
  const parts = [r?.name, r?.company]
    .map((s) => (typeof s === 'string' ? s.trim() : ''))
    .filter((s) => s.length > 0);
  if (parts.length) return parts.join(' · ');
  const email = typeof r?.email === 'string' ? r.email.trim() : '';
  return email || '(no contact)';
}

function fmtWhen(ts) {
  if (typeof ts !== 'number' || !Number.isFinite(ts) || ts <= 0) return '—';
  return new Date(ts * 1000).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' });
}

const req = (over = {}) => ({
  id: 'req_ab12cd34', ts: 1758100000, updated: 1758100500,
  name: 'Jane Doe', company: 'ACME Robotics', email: 'jane@acme.com',
  note: 'wants a 40 mm robot joint motor, needs a quote',
  status: 'new', ip_hash: '9f2ac41b7e05', ua: 'Mozilla/5.0', merged: 1,
  transcript: [],
  ...over,
});

// ── newRequestCount — what the badge shows ──────────────────────────────────

test('only the `new` ones are counted, in a mixed list', () => {
  const list = [
    req({ id: 'a', status: 'new' }),
    req({ id: 'b', status: 'contacted' }),
    req({ id: 'c', status: 'new' }),
    req({ id: 'd', status: 'invited' }),
    req({ id: 'e', status: 'declined' }),
  ];
  assert.equal(newRequestCount(list), 2);
});

test('an empty inbox is 0, and so is a list with nothing new', () => {
  assert.equal(newRequestCount([]), 0);
  assert.equal(newRequestCount(REQUEST_STATUSES.slice(1).map((s) => req({ status: s }))), 0);
});

test('garbage never throws — a 401 body is just "no new requests"', () => {
  for (const junk of [null, undefined, {}, { detail: 'admin only' }, 'nope', 0, NaN, [null, undefined]]) {
    assert.equal(newRequestCount(junk), 0, `input ${JSON.stringify(junk)}`);
  }
});

test('every status the contract names is accepted, and only `new` counts', () => {
  assert.deepEqual(REQUEST_STATUSES, ['new', 'contacted', 'invited', 'declined']);
  for (const s of REQUEST_STATUSES) {
    assert.equal(newRequestCount([req({ status: s })]), s === 'new' ? 1 : 0, s);
  }
});

// ── requestSummary — note → last user message → placeholder ─────────────────

test('the note is the summary when there is one', () => {
  assert.equal(requestSummary(req()), 'wants a 40 mm robot joint motor, needs a quote');
});

test('no note falls back to the LAST thing the visitor typed', () => {
  const r = req({
    note: '',
    transcript: [
      { role: 'user', content: 'what is this site?' },
      { role: 'assistant', content: 'AeroStator Core is…' },
      { role: 'user', content: 'can I get access? I design drone motors' },
      { role: 'assistant', content: 'Leave your e-mail and we will write.' },
    ],
  });
  assert.equal(requestSummary(r), 'can I get access? I design drone motors');
});

test('an assistant-only transcript is not a summary', () => {
  const r = req({ note: '   ', transcript: [{ role: 'assistant', content: 'Hello.' }] });
  assert.equal(requestSummary(r), '(no message)');
});

test('neither note nor transcript reads "(no message)", never blank or undefined', () => {
  assert.equal(requestSummary(req({ note: '', transcript: [] })), '(no message)');
  assert.equal(requestSummary(req({ note: undefined, transcript: undefined })), '(no message)');
  assert.equal(requestSummary(null), '(no message)');
});

test('a long note is cut to ~120 chars and ends in an ellipsis', () => {
  const long = 'we are evaluating a 40 mm axial flux joint motor for a collaborative robot arm and would like a quote for ten engineering samples plus the datasheet';
  const out = requestSummary(req({ note: long }));
  assert.ok(out.length <= SUMMARY_MAX, `summary is ${out.length} chars`);
  assert.ok(out.endsWith('…'), out);
  assert.ok(long.startsWith(out.slice(0, 40)), 'the kept part is the start of the note');
});

test('a note exactly at the limit is NOT cut', () => {
  const exact = 'x'.repeat(SUMMARY_MAX);
  assert.equal(requestSummary(req({ note: exact })), exact);
});

test('whitespace and newlines are flattened into one line', () => {
  assert.equal(requestSummary(req({ note: '  need\n\na  quote\t please ' })), 'need a quote please');
});

// ── contactLine ─────────────────────────────────────────────────────────────

test('name and company are joined with a middle dot', () => {
  assert.equal(contactLine(req()), 'Jane Doe · ACME Robotics');
});

test('a missing part is skipped, not left as an empty half', () => {
  assert.equal(contactLine(req({ company: '' })), 'Jane Doe');
  assert.equal(contactLine(req({ name: '' })), 'ACME Robotics');
  assert.equal(contactLine(req({ name: '  ', company: undefined })), 'jane@acme.com');
});

test('with no name and no company the e-mail IS the contact line', () => {
  assert.equal(contactLine(req({ name: '', company: '', email: ' jane@acme.com ' })), 'jane@acme.com');
});

test('a record with nothing at all still renders something', () => {
  assert.equal(contactLine({}), '(no contact)');
  assert.equal(contactLine(null), '(no contact)');
});

// ── fmtWhen — SECONDS, not milliseconds ─────────────────────────────────────

test('no timestamp is an em dash, never "Jan 1, 1970"', () => {
  for (const bad of [0, NaN, undefined, null, -1, 'x']) {
    assert.equal(fmtWhen(bad), '—', `input ${String(bad)}`);
  }
});

test('a real timestamp is read as SECONDS', () => {
  // 1758100000 s = 2025-09-17; read as ms it would be 1970.
  assert.ok(!fmtWhen(1758100000).includes('1970'), fmtWhen(1758100000));
  assert.equal(fmtWhen(1758100000), new Date(1758100000000).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }));
});
