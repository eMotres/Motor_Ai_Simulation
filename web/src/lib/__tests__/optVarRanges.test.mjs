// node --test — default ranges of the OPTIMIZATION VARIABLES (lib/optVarRanges.ts,
// loaded directly: Node strips the TS types).
//
// Owner 2026-10-15, CIANO28 85 loaded: "+ Add all geometry" gave Slot Height
// 3.9-4 at 7.4 mm, Tooth Width 0.9-1.1 at 5, Wires per Slot 10-11 at 18, Magnet
// Height 4.2-4.3 at 7 - ranges typed for ANOTHER motor, restored from the
// one-per-account stored config.  Pinned here: a default range always brackets
// the CURRENT value, a remembered range is reused only when it is not stamped
// for another motor and contains the current value, and the out-of-range
// detector finds exactly the leftovers.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  bracketRange, rangeContains, isUsableRange, reuseRemembered, identityScope,
  outOfRangeNames,
} from '../optVarRanges.ts';

const FLOAT = { type: 'float', min: 0.5, max: 40, step: 0.1 };
const INT = { type: 'int', min: 1, max: 60, step: 1 };

test('the owner’s CIANO28 85 values are all inside their default ranges', () => {
  const cases = [
    [7.4, FLOAT],   // slot height
    [5, FLOAT],     // tooth width
    [18, INT],      // wires per slot
    [7, FLOAT],     // magnet height
  ];
  for (const [cur, sch] of cases) {
    const r = bracketRange(cur, sch);
    assert.ok(r.min < cur && cur < r.max, `${cur} in ${r.min}..${r.max}`);
    assert.ok(r.max > r.min && r.step > 0);
  }
});

test('floats: about +-10 %, rounded OUTWARD to the step', () => {
  const r = bracketRange(7.4, FLOAT);                 // 6.66 .. 8.14 -> 6.6 .. 8.2
  assert.deepEqual(r, { min: 6.6, max: 8.2, step: 0.1 });
  const r5 = bracketRange(5, FLOAT);                  // 4.5 .. 5.5
  assert.deepEqual(r5, { min: 4.5, max: 5.5, step: 0.1 });
  // a coarse step (bigger than the +-10 % window) must not blow the range up
  const c = bracketRange(7.4, { type: 'float', min: 0, max: 40, step: 5 });
  assert.ok(c.min > 6 && c.max < 9);
  assert.ok(c.min <= 7.4 && 7.4 <= c.max);
});

test('integers: +-1 or +-2, whole numbers', () => {
  assert.deepEqual(bracketRange(18, INT), { min: 16, max: 20, step: 1 });   // 10 % = 1.8 -> 2
  assert.deepEqual(bracketRange(5, INT), { min: 4, max: 6, step: 1 });      // 0.5 -> at least 1
  assert.deepEqual(bracketRange(40, INT), { min: 38, max: 42, step: 1 });   // capped at 2
  for (const c of [3, 7, 12, 18, 40]) {
    const r = bracketRange(c, INT);
    assert.ok(Number.isInteger(r.min) && Number.isInteger(r.max));
    assert.ok(r.min < c && c < r.max);
  }
});

test('clamped to the schema, but never so far that the current value falls out', () => {
  const r = bracketRange(0.5, { type: 'float', min: 0.5, max: 2, step: 0.05 });
  assert.equal(r.min, 0.5);
  assert.ok(r.max > 0.5);
  const hi = bracketRange(39.9, FLOAT);
  assert.equal(hi.max, 40);
  // a current value outside the schema window still gets a range containing it
  const out = bracketRange(45, FLOAT);
  assert.ok(out.min <= 45 && 45 <= out.max);
});

test('zero, missing and non-finite current values give a usable range', () => {
  const z = bracketRange(0, { type: 'float', min: -10, max: 10, step: 0.5 });
  assert.ok(z.min < 0 && z.max > 0);
  const nan = bracketRange(NaN, FLOAT);
  assert.deepEqual(nan, { min: 0.5, max: 40, step: 0.1 });
  const bare = bracketRange(NaN, undefined);
  assert.ok(bare.max > bare.min);
  const noSchema = bracketRange(10, undefined);
  assert.ok(noSchema.min < 10 && 10 < noSchema.max);
});

test('rangeContains / isUsableRange', () => {
  assert.ok(rangeContains({ min: 6, max: 8 }, 7.4));
  assert.ok(!rangeContains({ min: 3.9, max: 4 }, 7.4));      // the owner’s stale one
  assert.ok(rangeContains({ min: 7.4, max: 8 }, 7.4));       // edge counts
  assert.ok(!rangeContains({ min: 'x', max: 8 }, 7.4));
  assert.ok(!rangeContains(undefined, 7.4));
  assert.ok(isUsableRange({ min: 1, max: 2, step: 0.1 }));
  assert.ok(!isUsableRange({ min: 5, max: 5, step: 0.1 }));  // the schema’s degenerate seed
  assert.ok(!isUsableRange({ min: 1, max: 2, step: 0 }));
});

test('re-adding: a range from another motor is NOT reused, the bracket is', () => {
  const here = identityScope({ stator_diameter: 85, num_seg: 1, num_slots_per_segment: 12, num_poles_per_segment: 10 }, 'CIANO28', '85');
  const other = identityScope({ stator_diameter: 30, num_seg: 1, num_slots_per_segment: 12, num_poles_per_segment: 10 }, 'CIANO14', '30');
  assert.notEqual(here, other);
  const stale = { min: 3.9, max: 4, step: 0.1, scope: other };
  assert.equal(reuseRemembered(stale, 7.4, here), false);                  // other motor
  // stamped for ANOTHER motor, even if it happens to contain the value
  assert.equal(reuseRemembered({ min: 7, max: 8, step: 0.1, scope: other }, 7.4, here), false);
  // unstamped legacy leftover that misses the value -> rejected by containment
  assert.equal(reuseRemembered({ min: 3.9, max: 4, step: 0.1 }, 7.4, here), false);
  // the user’s own range for this motor, containing the value -> kept
  assert.equal(reuseRemembered({ min: 6, max: 9, step: 0.2, scope: here }, 7.4, here), true);
  // unstamped but containing -> kept (legacy configs of the same motor)
  assert.equal(reuseRemembered({ min: 6, max: 9, step: 0.2 }, 7.4, here), true);
  // same motor but the range does not contain the value any more -> bracket
  assert.equal(reuseRemembered({ min: 3.9, max: 4, step: 0.1, scope: here }, 7.4, here), false);
  // degenerate schema seed (min === max) -> bracket
  assert.equal(reuseRemembered({ min: 7.4, max: 7.4, step: 0.1 }, 7.4, here), false);
  assert.equal(reuseRemembered(undefined, 7.4, here), false);
});

test('identityScope: die + configuration + lamination keys', () => {
  const g = { stator_diameter: 85, num_seg: 2, num_slots_per_segment: 6, num_poles_per_segment: 7, slot_height: 7.4 };
  assert.equal(identityScope(g, 'D', 'C'), 'D|C|85|2|6|7');
  assert.equal(identityScope(g, null, null), '||85|2|6|7');
  // a non-identity edit (slot height) does not change the scope
  assert.equal(identityScope({ ...g, slot_height: 9 }, 'D', 'C'), identityScope(g, 'D', 'C'));
  // another configuration of the same die does
  assert.notEqual(identityScope(g, 'D', 'C2'), identityScope(g, 'D', 'C'));
  assert.equal(identityScope(undefined, undefined, undefined), '|||||');
});

test('outOfRangeNames: only selected geometry variables that miss the current value', () => {
  const geometry = { slot_height: 7.4, tooth_width: 5, wires: 18, magnet_height: 7, gamma_deg: 0 };
  const vars = {
    slot_height:   { mode: 'optimize', min: 3.9, max: 4 },     // stale
    tooth_width:   { mode: 'optimize', min: 4.5, max: 5.5 },   // fine
    wires:         { mode: 'sweep',    min: 10,  max: 11 },    // stale
    magnet_height: { mode: 'fixed',    min: 4.2, max: 4.3 },   // not selected
    gamma_deg:     { mode: 'optimize', min: 20,  max: 40 },    // operating point, skipped
    unknown_var:   { mode: 'optimize', min: 1,   max: 2 },     // no geometry value
  };
  assert.deepEqual(outOfRangeNames(vars, geometry).sort(), ['slot_height', 'wires']);
  assert.deepEqual(outOfRangeNames({}, geometry), []);
  assert.deepEqual(outOfRangeNames(vars, undefined), []);
});
