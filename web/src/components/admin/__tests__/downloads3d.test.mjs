/** Pure helpers of the Admin · 3D downloads panel (`downloads3d.ts` has no runtime imports). */
import test from 'node:test';
import assert from 'node:assert/strict';
import { idsInHeader, traceText, ndaLabel, shortHash } from '../downloads3d.ts';

const HEAD = "ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('x','Download ID: d20261014-a1b2c3d4e5f6'),'2;1');\nENDSEC;\nDATA;\n#1=X('d20261015-ffffffffffff');\n";

test('the id is read from the header only', () => {
  assert.deepEqual(idsInHeader(HEAD), ['d20261014-a1b2c3d4e5f6']);
  assert.deepEqual(idsInHeader('pasted line d20261014-a1b2c3d4e5f6 and again d20261014-a1b2c3d4e5f6'), ['d20261014-a1b2c3d4e5f6']);
  assert.deepEqual(idsInHeader('no id here'), []);
});

test('the trace text carries the id and the hash', () => {
  const h = 'ab'.repeat(32);
  assert.equal(traceText(HEAD, h), 'd20261014-a1b2c3d4e5f6\n' + h);
  assert.equal(traceText('FreeCAD Model', h), h);
});

test('NDA label per basis', () => {
  const base = { iso: '', user: 'u', die: '', config: '', motor_id: '', kind: 'step', file: '', sha256: '', bytes: 0 };
  assert.equal(ndaLabel({ ...base, nda_basis: { type: 'online', ref: 'v1.0 record n1', version: 'v1.0' } }), 'v1.0');
  assert.equal(ndaLabel({ ...base, nda_basis: { type: 'admin', ref: 'admin-flag 2026-10-01', date: '2026-10-01' } }), 'admin flag 2026-10-01');
  assert.equal(ndaLabel({ ...base, nda_basis: { type: 'internal', ref: 'internal' } }), 'internal');
  assert.equal(ndaLabel({ ...base, nda: false }), 'internal');
  assert.equal(ndaLabel({ ...base, nda: true }), '—');
  assert.equal(shortHash('0123456789abcdef'), '0123456789…');
});
