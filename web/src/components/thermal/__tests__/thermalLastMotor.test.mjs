import test from 'node:test';
import assert from 'node:assert/strict';
import { selectionMatchesActiveFamily } from '../thermalLastMotor.ts';

const d85 = { ref_id: null, die: 'CIANO28 85 20SW1200', config: 'L13', duty: 'rated' };

test('admin idle hydration accepts the actual family selection with a null catalog reference', () => {
  assert.equal(selectionMatchesActiveFamily(d85, {
    active: true, die: d85.die, config: d85.config, duty: d85.duty,
  }), true);
});

test('hydration refuses a different loaded family, configuration, duty or released context', () => {
  for (const context of [
    { active: true, die: 'OTHER', config: 'L13', duty: 'rated' },
    { active: true, die: d85.die, config: 'L20', duty: 'rated' },
    { active: true, die: d85.die, config: 'L13', duty: 'peak' },
    { active: false, die: d85.die, config: 'L13', duty: 'rated' },
    null,
  ]) assert.equal(selectionMatchesActiveFamily(d85, context), false);
});

test('hydration refuses malformed identities even when omitted fields appear equal', () => {
  const context = { active: true, die: d85.die, config: d85.config, duty: d85.duty };
  for (const selection of [
    { config: d85.config, duty: d85.duty, ref_id: null },
    { ...d85, die: '' },
    { ...d85, config: 42 },
    { ...d85, duty: undefined },
    { ...d85, ref_id: undefined },
    { ...d85, ref_id: '' },
  ]) assert.equal(selectionMatchesActiveFamily(selection, context), false);
});
