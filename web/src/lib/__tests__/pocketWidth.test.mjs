// node --test — the computed pocket width (lib/pocketWidth.ts), the same numbers as the Python pocket_width().
import test from 'node:test';
import assert from 'node:assert/strict';

let P = null;
try { P = await import('../pocketWidth.ts'); } catch { /* old Node */ }
const t = P ? test : test.skip;

t('one strip: wire + 2 x insulation + one spacing; the stored slot width is one spacing wider', () => {
  const g = { wire_width: 7, wire_spacing_x: 0.1, insulation_thickness: 0.2, wire_split: 1, slot_width: 7.6 };
  assert.ok(Math.abs(P.pocketWidth(g) - 7.5) < 1e-9);
});
t('N strips: N x w + (N-1) x 2 x spacing + 2 x ins + spacing', () => {
  const g = { wire_width: 0.421, wire_spacing_x: 0.05218, insulation_thickness: 0.07, wire_split: 6 };
  assert.ok(Math.abs(P.pocketWidth(g) - 3.24) < 1e-4);
});
t('a missing input gives no value, never a guess', () => {
  assert.equal(P.pocketWidth({ wire_width: 3 }), null);
  assert.equal(P.pocketWidth(null), null);
});
