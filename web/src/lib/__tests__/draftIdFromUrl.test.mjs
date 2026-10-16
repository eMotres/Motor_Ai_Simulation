// node --test — the `?design=` URL-param parsing of lib/agentDrafts.ts
// `draftIdFromUrl`, copied verbatim (the repo's convention for node tests:
// `node --test` cannot load the TS modules — agentDrafts.ts also pulls in
// './localAuth' without an extension, which the native TS loader cannot
// resolve — so the pure function under test is re-stated here and kept in
// sync). `search` is Node's global URLSearchParams input, exactly what the
// real function is called with once `window.location.search` is read.
import test from 'node:test';
import assert from 'node:assert/strict';

function draftIdFromUrl(search = '') {
  try {
    const id = new URLSearchParams(search).get('design');
    return id && /^d-[0-9a-f]{12}$/.test(id) ? id : null;
  } catch { return null; }
}

test('a well-formed design id round-trips', () => {
  assert.equal(draftIdFromUrl('?tab=configure&design=d-cb862c265588'), 'd-cb862c265588');
});

test('no design param -> null', () => {
  assert.equal(draftIdFromUrl('?tab=configure'), null);
  assert.equal(draftIdFromUrl(''), null);
});

test('a malformed id is rejected, not passed through — never fetch on faith', () => {
  assert.equal(draftIdFromUrl('?design=not-a-draft-id'), null);
  assert.equal(draftIdFromUrl('?design=d-tooshort'), null);
  assert.equal(draftIdFromUrl('?design=d-CB862C265588'), null);   // hex is lowercase only
  assert.equal(draftIdFromUrl('?design=d-cb862c2655889999'), null); // too long
});

test('other query params do not interfere', () => {
  assert.equal(draftIdFromUrl('?tab=configure&design=d-cb862c265588&foo=bar'), 'd-cb862c265588');
});
