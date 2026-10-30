// node --test — the strip's reading of a refused / moved "Save to <duty>" (owner 2026-10-30).
// Imports lib/buildClash.ts itself (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';

let B = null;
try { B = await import('../buildClash.ts'); } catch { /* old Node */ }
const t = B ? test : test.skip;

const DIE_REFUSAL = "the live machine's build differs from configuration 'L100-2x3ph-Al2O3-14t' "
  + '(die slot_width: 7.74 → 8.24) — a duty cannot carry its own geometry.  Load this '
  + "configuration's build first, or duplicate the configuration (or create a new one) for the build on screen.";
const CFG_REFUSAL = "the live machine's build differs from configuration 'L90' "
  + '(wire_width: 7 → 7.5; motor_length: 100 → 90; num_wires_per_slot: 14 → 15) — a duty cannot carry its own geometry.';

t('a die-level difference offers a new die, named in one line', () => {
  const c = B.buildClashOf(DIE_REFUSAL);
  assert.equal(c.kind, 'die');
  assert.equal(c.short, '✗ not saved — build differs: slot width: 7.74 → 8.24');
  assert.equal(c.full, DIE_REFUSAL);
});

t('configuration-level differences offer a new configuration', () => {
  const c = B.buildClashOf(CFG_REFUSAL);
  assert.equal(c.kind, 'config');
  assert.equal(c.short, '✗ not saved — build differs: wire width: 7 → 7.5; motor length: 100 → 90 (+1)');
});

t('any other failure is not a build clash', () => {
  assert.equal(B.buildClashOf('HTTP 500'), null);
  assert.equal(B.buildClashOf(''), null);
});

t('the moved save names where it went and what stayed', () => {
  const m = B.movedSave({ ok: true, die: 'CIANO56 400_80 vadim 6', config: 'L90-2x3ph-Al2O3-15t',
    transitioned: { die: 'CIANO56 400_80 vadim 6', config: 'L90-2x3ph-Al2O3-15t', created: false,
      from_die: 'CIANO56 400_80 vadim 5', from_config: 'L100-2x3ph-Al2O3-14t',
      diffs: [{ key: 'slot_width', label: 'slot width (wire + insulation)', die: 7.74, live: 8.240000001 }] } });
  assert.equal(m.die, 'CIANO56 400_80 vadim 6');
  assert.equal(m.config, 'L90-2x3ph-Al2O3-15t');
  assert.equal(B.movedLine(m), "another lamination (slot width (wire + insulation) 7.74 → 8.24): "
    + "moved to existing die 'CIANO56 400_80 vadim 6'; 'CIANO56 400_80 vadim 5' is untouched");
  assert.equal(B.movedSave({ ok: true, config: 'L90' }), null);
});
