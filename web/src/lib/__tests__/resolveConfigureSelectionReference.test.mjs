import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

let M = null;
try { M = await import('../resolveConfigureSelectionReference.ts'); } catch { /* unsupported Node */ }
const t = M ? test : test.skip;

const current = { ref_id: null, die: 'CIANO28 85 20SW1200', config: 'L13', duty: 'peak' };
const candidate = {
  id: 'cat:candidate_d85_ciano28_85_20sw1200_l13',
  card: { die: 'CIANO28 85 20SW1200', config: 'L13', date: '2026-10-08' },
  geo: { numSlots: 24, numPoles: 28, statorOR_mm: 42.5, magnetHeight_mm: 7.0 },
};
const unrelated = {
  id: 'cat:cat_ciano14_40_new',
  card: { die: 'CIANO14 40 new', config: 'L12', date: '2026-10-05' },
  geo: { numSlots: 12, numPoles: 14, statorOR_mm: 20.0, magnetHeight_mm: 3.0 },
};
const loadedD85 = { num_slots: 24, num_poles: 28, stator_outer_radius: 42.5, magnet_height: 7.0 };
const staleD40 = { num_slots: 12, num_poles: 14, stator_outer_radius: 20.0, magnet_height: 3.0 };

t('actual ordinary D85/L13 peak selection refuses the only granted CIANO14 card', () => {
  assert.equal(M.resolveConfigureSelectionReference(current, [unrelated], loadedD85), undefined);
  assert.equal(M.resolveConfigureSelectionReference(current, [unrelated, candidate], loadedD85), candidate);
});

t('null reference id resolves only the unique exact accessible die/config card', () => {
  assert.equal(M.resolveConfigureSelectionReference(current, [unrelated, candidate], loadedD85), candidate);
  assert.equal(M.resolveConfigureSelectionReference(current, [unrelated], loadedD85), undefined);
  assert.equal(M.resolveConfigureSelectionReference(current, [candidate, {
    ...candidate, id: 'cat:d85_duplicate',
  }], loadedD85), undefined);
});

t('null-id exact family card is refused when live geometry is stale or unavailable', () => {
  assert.equal(M.resolveConfigureSelectionReference(current, [candidate], staleD40), undefined);
  assert.equal(M.resolveConfigureSelectionReference(current, [candidate], null), undefined);
  assert.equal(M.resolveConfigureSelectionReference(current, [candidate], {
    ...loadedD85, magnet_height: 7.4,
  }), undefined);
});

t('explicit reference id retains exact id plus same-die behavior', () => {
  const selection = { ...current, ref_id: 'candidate_d85_ciano28_85_20sw1200_l13' };
  assert.equal(M.resolveConfigureSelectionReference(selection, [candidate], staleD40), candidate);
  assert.equal(M.resolveConfigureSelectionReference(selection, [{
    ...candidate, id: 'cat:other',
  }], loadedD85), undefined);
  assert.equal(M.resolveConfigureSelectionReference(selection, [{
    ...candidate, card: { ...candidate.card, die: 'CIANO14 40 new' },
  }], loadedD85), undefined);
});

t('missing, malformed, or unlisted selections fail closed without geometry fallback', () => {
  assert.equal(M.resolveConfigureSelectionReference({ ...current, ref_id: undefined }, [candidate], loadedD85), undefined);
  assert.equal(M.resolveConfigureSelectionReference({ ...current, die: '' }, [candidate], loadedD85), undefined);
  assert.equal(M.resolveConfigureSelectionReference({ ...current, config: 'L12' }, [candidate], loadedD85), undefined);
  assert.equal(M.resolveConfigureSelectionReference(current, [{ id: 'ref-similar-geometry' }], loadedD85), undefined);
});

t('ordinary Configure resolution uses only accessible catalog cards for loaded selections', () => {
  const panel = readFileSync(new URL('../../components/compare/ConfiguratorPanel.tsx', import.meta.url), 'utf8');
  assert.match(panel, /resolveConfigureSelectionReference\(lastMotor\.selection, catalogRefs, g\)/);
  assert.match(panel, /const m = savedReference \?\? \(isOrdinaryAccount \? null : pickReference/);
});
