import test from 'node:test';
import assert from 'node:assert/strict';

const T = await import('../configuratorReferenceTemperature.ts');
const R = await import('../referenceCardProvenance.ts');

test('catalog parsing forwards only the reference candidate provenance pin', () => {
  const candidate = T.D85_REFERENCE_CANDIDATE_SHA256;
  assert.deepEqual(R.referenceCandidateProvenance({
    candidate_sha256: candidate,
    qualification_claim: false,
    unrelated: 'ignored',
  }), { candidate_sha256: candidate });
  assert.deepEqual(R.referenceCandidateProvenance({ candidate_sha256: 42 }), { candidate_sha256: null });
  assert.equal(R.referenceCandidateProvenance(null), null);
});

test('the exact D85 candidate exposes its pinned scalar solve temperatures, not a cold set', () => {
  const basis = T.configuratorReferenceTemperatureBasis(T.D85_REFERENCE_CANDIDATE_SHA256);
  assert.deepEqual(basis, {
    windingC: 180,
    magnetC: 150,
    steelC: null,
    stackLengthMm: 13,
    rpm: 1000,
    currentA: 25.88,
    sourceRecord: 'lchk_rated_72steps',
    sourceSha256: T.D85_REFERENCE_LEDGER_SHA256,
    coldConstantsAvailable: false,
  });
});

test('an unrelated or malformed provenance hash never inherits D85 temperatures', () => {
  assert.equal(T.configuratorReferenceTemperatureBasis(null), null);
  assert.equal(T.configuratorReferenceTemperatureBasis('not-a-hash'), null);
  assert.equal(T.configuratorReferenceTemperatureBasis('0'.repeat(64)), null);
  assert.equal(T.configuratorReferenceTemperatureBasis({ candidate_sha256: T.D85_REFERENCE_CANDIDATE_SHA256 }), null);
});

test('resistance basis labels standard extraction at 120°C, audited D85 at 180°C, and unknown imports as unknown', () => {
  assert.equal(T.configuratorResistanceReferenceTemperature(null, 2, null), 120);
  assert.equal(T.configuratorResistanceReferenceTemperature(T.D85_REFERENCE_CANDIDATE_SHA256, 2, 120), 180);
  assert.equal(T.configuratorResistanceReferenceTemperature(null, 1, null), null);
  assert.equal(T.configuratorResistanceReferenceTemperature(null, null, null), null);
  assert.equal(T.configuratorResistanceReferenceTemperature(null, 1, 95), 95);
});
