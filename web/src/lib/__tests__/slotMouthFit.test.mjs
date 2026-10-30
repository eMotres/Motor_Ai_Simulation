// node --test — the cross-section panel's state for a wire wider than the slot mouth
// (owner report 2026-10-15).  Mirrors tests/test_slot_mouth_infeasible.py on the server.
import test from 'node:test';
import assert from 'node:assert/strict';

let S = null;
try { S = await import('../slotMouthFit.ts'); } catch { /* old Node */ }
const t = S ? test : test.skip;

// CIANO14 30_10 / L10 (tooth2_width 1.7 = the stored 1.6 in the Fusion definition, 2026-10-30)
const GEO = {
  num_seg: 2, num_slots_per_segment: 6, num_slots: 12, stator_diameter: 30, stator_outer_radius: 15,
  core_thickness: 1.9, slot_height: 4.2, tooth_width: 2.3, tooth2_width: 1.7, cut_width: 1.8,
  wire_width: 2, wire_spacing_x: 0.1, insulation_thickness: 0.05, wire_split: 1, num_wires_per_slot: 6,
};

t('the die as built is feasible and the panel draws it', () => {
  assert.equal(S.crossSectionState(GEO).kind, 'ok');
  for (const w of [0.5, 1, 2, 2.25]) assert.equal(S.crossSectionState({ ...GEO, wire_width: w }).kind, 'ok');
});

t('a wire past the slot mouth is infeasible, with the exact maximum', () => {
  const st = S.crossSectionState({ ...GEO, wire_width: 2.48 });
  assert.equal(st.kind, 'infeasible');
  assert.ok(Math.abs(st.limit.maxWireWidthMm - 2.30) < 1e-9);
  assert.equal(S.slotMouthMessage(st.limit), 'wire too wide for this slot: max wire width 2.30 mm at 6 turns');
});

t('the limit is exact: just under is drawn, just over is refused', () => {
  assert.equal(S.crossSectionState({ ...GEO, wire_width: 2.29 }).kind, 'ok');
  assert.equal(S.crossSectionState({ ...GEO, wire_width: 2.31 }).kind, 'infeasible');
});

t('a split names the strip count', () => {
  const st = S.crossSectionState({ ...GEO, wire_split: 2, wire_width: 1.6 });
  assert.equal(st.kind, 'infeasible');
  assert.match(S.slotMouthMessage(st.limit), /^wire split too wide for this slot: max 1 strip/);
});

t('a partial geometry is not judged', () => {
  assert.equal(S.crossSectionState({ wire_width: 9 }).kind, 'ok');
  assert.equal(S.crossSectionState(null).kind, 'ok');
});

t("the server's 422 is recognised", () => {
  assert.equal(S.refusedReason({ detail: { error: 'infeasible geometry - wire too wide for this slot: max wire width 2.30 mm at 6 turns' } }),
    'wire too wide for this slot: max wire width 2.30 mm at 6 turns');
  const lobe = 'stator outline not symmetric (construction failed) - the 6 slot-pair sectors differ by 18.7 % in area';
  assert.equal(S.refusedReason({ detail: { error: lobe } }), lobe);
  assert.equal(S.refusedReason({ detail: 'boom' }), null);
  assert.equal(S.refusedReason(null), null);
});
