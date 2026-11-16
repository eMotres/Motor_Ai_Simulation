// node --test: reading a motor designation (lib/motorDesignation.ts); the same grammar as motor_ai_sim.motor_designation.parse.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { connectionParts, meaningKey, parseDesignation } from '../motorDesignation.ts';

test('the owner table parses', () => {
  for (const s of ['CILN28 200_40 24F4SY', 'CIANO28 80_12 60R4SY', 'CIANDO56 400_90 15F4S2PY', 'CIANO14 40_12 7F2SY', 'CILN28 200_40 24F4SY Rev B']) {
    assert.ok(parseDesignation(s), s);
  }
  const f = parseDesignation('CIANDO56 400_90 15F4S2PY');
  assert.deepEqual([f.winding, f.rotor, f.cooling, f.steel, f.double, f.open, f.poles, f.od, f.length, f.turns, f.wire, f.connection, f.star_delta],
    ['C', 'I', 'A', 'N', 'D', 'O', '56', '400', '90', '15', 'F', '4S2P', 'Y']);
  assert.equal(parseDesignation('CILN28 200_40 24F4SY').open, '');
  assert.equal(parseDesignation('CILN28 200_40 24F4SY Rev B').rev, 'B');
});

test('unknown letters still parse; junk does not', () => {
  assert.ok(parseDesignation('CI????28 200_40 24F4SY'));
  for (const bad of ['', 'CILN28', 'L12-F475', 'ciln28 200_40 24F4SY', 'CILN28 200_40 24X4SY']) assert.equal(parseDesignation(bad), null, bad);
});

test('meaning keys and the connection in words', () => {
  assert.equal(meaningKey('cooling', 'L'), 'designation.meaning.cooling.L');
  assert.equal(meaningKey('open', ''), 'designation.meaning.open.none');
  assert.equal(meaningKey('turns', '24'), 'designation.meaning.turns.n');
  assert.equal(meaningKey('steel', '?'), 'designation.meaning.unknown');
  assert.deepEqual(connectionParts('4S'), { series: 4, parallel: 1 });
  assert.deepEqual(connectionParts('2S2P'), { series: 2, parallel: 2 });
  assert.deepEqual(connectionParts('4P'), { series: 1, parallel: 4 });
});
