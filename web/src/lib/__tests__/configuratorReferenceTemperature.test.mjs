import test from 'node:test';
import assert from 'node:assert/strict';

const T = await import('../configuratorReferenceTemperature.ts');
const R = await import('../referenceCardProvenance.ts');
const G = await import('../coldConstantsGuard.mjs');
const S = await import('../stackCoefficientBasis.mjs');

// The audited facts live on the SERVER (reference_basis in the references answer); these are
// synthetic values with the same shape, so the bundle and its tests carry no real machine data.
const BASIS = {
  windingC: 170, magnetC: 140, steelC: null, stackLengthMm: 20, rpm: 1500, currentA: 12.5,
  sourceRecord: 'synthetic_record', sourceSha256: 'a'.repeat(64), coldConstantsAvailable: false,
};

test('catalog parsing forwards only the reference candidate provenance pin', () => {
  const candidate = 'b'.repeat(64);
  assert.deepEqual(R.referenceCandidateProvenance({
    candidate_sha256: candidate,
    qualification_claim: false,
    unrelated: 'ignored',
  }), { candidate_sha256: candidate });
  assert.deepEqual(R.referenceCandidateProvenance({ candidate_sha256: 42 }), { candidate_sha256: null });
  assert.equal(R.referenceCandidateProvenance(null), null);
});

test('the server basis is parsed field by field and frozen', () => {
  const a = T.parseReferenceBasisAnswer({ required: true, basis: BASIS });
  assert.equal(a.required, true);
  assert.deepEqual(a.basis, BASIS);
  assert.ok(Object.isFrozen(a.basis));
  assert.deepEqual(T.configuratorReferenceTemperatureBasis(a), BASIS);
});

test('a missing or malformed server basis is null and never invented', () => {
  assert.equal(T.parseReferenceBasisAnswer(null), null);
  assert.equal(T.parseReferenceBasisAnswer('x'), null);
  assert.equal(T.parseReferenceBasisAnswer({ required: true, basis: null }).basis, null);
  for (const bad of [{ ...BASIS, windingC: '180' }, { ...BASIS, rpm: NaN }, { ...BASIS, steelC: 'x' },
    { ...BASIS, coldConstantsAvailable: 'no' }, { ...BASIS, sourceRecord: 3 }, [BASIS]]) {
    assert.equal(T.parseReferenceBasisAnswer({ required: true, basis: bad }).basis, null);
  }
  assert.equal(T.configuratorReferenceTemperatureBasis(null), null);
  assert.equal(T.configuratorReferenceTemperatureBasis(undefined), null);
});

test('resistance basis labels standard extraction at 120°C, audited cards at their own winding temperature, and unknown imports as unknown', () => {
  const a = { required: true, basis: BASIS };
  assert.equal(T.configuratorResistanceReferenceTemperature(null, 2, null), 120);
  assert.equal(T.configuratorResistanceReferenceTemperature(a, 2, 120), 170);
  assert.equal(T.configuratorResistanceReferenceTemperature(null, 1, null), null);
  assert.equal(T.configuratorResistanceReferenceTemperature(null, null, null), null);
  assert.equal(T.configuratorResistanceReferenceTemperature(null, 1, 95), 95);
});

test('an audited card (server flag) fails closed; an ordinary card only hides what its basis says is unavailable', () => {
  assert.equal(G.coldConstantsUnavailableForCard(true, null), true);
  assert.equal(G.coldConstantsUnavailableForCard(true, BASIS), true);
  assert.equal(G.coldConstantsUnavailableForCard(true, { ...BASIS, coldConstantsAvailable: true }), false);
  assert.equal(G.coldConstantsUnavailableForCard(false, null), false);
  assert.equal(G.coldConstantsUnavailableForCard(undefined, null), false);
  assert.equal(G.coldConstantsUnavailableForCard(false, BASIS), true);
  assert.equal(S.stackCoefficientReferenceOnly(false, 99, null), false);
  assert.equal(S.stackCoefficientReferenceOnly(true, 20, BASIS), false);
  assert.equal(S.stackCoefficientReferenceOnly(true, 21, BASIS), true);
  assert.equal(S.stackCoefficientReferenceOnly(true, 20, null), true);
  assert.equal(S.stackCoefficientReferenceOnly(true, NaN, BASIS), true);
});
