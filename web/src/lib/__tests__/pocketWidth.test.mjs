// node --test — the computed pocket width (lib/pocketWidth.ts) against the SAME cases as the Python pocket_width()
// (tests/fixtures/pocket_width_cases.json, also read by tests/test_pocket_width_naming.py).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

let P = null;
try { P = await import('../pocketWidth.ts'); } catch { /* old Node */ }
const t = P ? test : test.skip;
const FIX = JSON.parse(readFileSync(new URL('../../../../tests/fixtures/pocket_width_cases.json', import.meta.url), 'utf8'));

t('the strip gap factor is the fixture\'s (Python reads the same number)', () => {
  assert.equal(P.STRIP_GAP_FACTOR, FIX.strip_gap_factor);
});
for (const c of FIX.cases) {
  t(`parity: ${c.name}`, () => {
    const v = P.pocketWidth(c.geo);
    if (c.expected === null) assert.equal(v, null);
    else assert.ok(Math.abs(v - c.expected) < FIX.tolerance, `${v} vs ${c.expected}`);
  });
}
t('inf / nan wire keys are missing; a non-finite split is one strip; no geometry is null', () => {
  const g = { wire_width: 7, wire_spacing_x: 0.1, insulation_thickness: 0.2 };
  assert.equal(P.pocketWidth({ ...g, wire_width: Infinity }), null);
  assert.equal(P.pocketWidth({ ...g, insulation_thickness: NaN }), null);
  assert.ok(Math.abs(P.pocketWidth({ ...g, wire_split: Infinity }) - 7.5) < 1e-9);
  assert.equal(P.pocketWidth(null), null);
});
