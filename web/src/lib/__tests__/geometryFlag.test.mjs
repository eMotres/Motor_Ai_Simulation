// node --test — the stator outer-notch flag (2026-11-03): its reading, the
// slot-fit arithmetic without notches, and the geometry signature.
// Mirrors tests/test_stator_outer_notches.py on the server.
import test from 'node:test';
import assert from 'node:assert/strict';

let F = null, S = null, G = null;
try {
  F = await import('../geometryFlag.ts');
  S = await import('../slotMouthFit.ts');
  G = await import('../../components/common/geoSig.ts');
} catch { /* old Node */ }
const t = F && S && G ? test : test.skip;

// CIANO14 30_10 / L10 — the same machine the slotMouthFit test uses
const GEO = {
  num_seg: 2, num_slots_per_segment: 6, num_slots: 12, stator_diameter: 30, stator_outer_radius: 15,
  core_thickness: 1.9, slot_height: 4.2, tooth_width: 2.3, tooth2_width: 1.7, cut_width: 1.8,
  wire_width: 2, wire_spacing_x: 0.1, insulation_thickness: 0.05, wire_split: 1, num_wires_per_slot: 6,
};

t('a missing flag means notched; false / 0 / "off" mean a smooth OD', () => {
  assert.equal(F.flagValue(undefined), true);
  assert.equal(F.flagValue(null), true);
  assert.equal(F.flagValue(true), true);
  assert.equal(F.flagValue(false), false);
  assert.equal(F.flagValue(0), false);
  assert.equal(F.flagValue('off'), false);
  assert.equal(F.flagValue(undefined, false), false);
});

t('without notches the slot limit is the pocket wall against r_in·sin(half)', () => {
  const on = S.slotMouthLimit(GEO);
  const off = S.slotMouthLimit({ ...GEO, stator_outer_notches: false });
  assert.ok(on && off && on.feasible && off.feasible);
  // the same machine with notches set true is the notched answer
  assert.deepEqual(S.slotMouthLimit({ ...GEO, stator_outer_notches: true }), on);
  // no notches: cut_width is not needed and does not move the limit
  const noCut = { ...GEO, stator_outer_notches: false };
  delete noCut.cut_width;
  assert.deepEqual(S.slotMouthLimit(noCut), off);
  // r_in = 15 − 1.9 − 4.2 = 8.9; pocket wall = 1.15 + 0.1 + 2 + 0.1 = 3.35;
  // limit 8.9·sin(30°) = 4.45 → 1.10 mm of wire width left
  assert.ok(Math.abs(off.maxWireWidthMm - (2 + 4.45 - 3.35)) < 1e-9);
});

t('the geometry signature stamps only an OFF flag', () => {
  const on = G.geoSignature(GEO);
  assert.equal(G.geoSignature({ ...GEO, stator_outer_notches: true }), on);
  const off = G.geoSignature({ ...GEO, stator_outer_notches: false });
  assert.ok(off.includes('stator_outer_notches:0'));
  assert.ok(!on.includes('stator_outer_notches'));
  // sorted like every other key
  const keys = off.split('|').map((p) => p.split(':')[0]);
  assert.deepEqual(keys, [...keys].sort());
});
