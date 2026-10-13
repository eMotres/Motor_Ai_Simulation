// node --test - the Motors catalog is ONE ROW PER MOTOR (owner 2026-10-13): configuration
// buttons inside the row, the numbers outside are the default mode's.
// Imports lib/catalogRows.ts (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';

let C = null;
try { C = await import('../catalogRows.ts'); } catch { /* old Node */ }
const t = C ? test : test.skip;

const duty = (name, kw, nm, rpm, kg) => ({ name, power_kw: kw, torque_nm: nm, rpm, result: { mass_kg: kg } });
const die = {
  name: 'CIANO14 40',
  configs: [
    { name: 'L12', card_date: '2026-10-01', duties: [duty('peak', 2, 1.4, 14400, 0.31), duty('rated', 0.8, 0.6, 13000, 0.31)] },
    { name: 'L20', default_duty: 'peak', duties: [duty('rated', 1.2, 0.9, 12000, 0.42), duty('peak', 3, 2, 14000, 0.42)] },
    { name: 'L30', duties: [duty('S1 prop', 0.5, 0.3, 9000, 0.5)] },
    { name: 'L40', duties: [] },
  ],
};

t('one row: a button per configuration, in the die order', () => {
  assert.deepEqual(C.configButtons(die).map((b) => b.config), ['L12', 'L20', 'L30', 'L40']);
});

t('the default mode is "rated" unless the flag says otherwise', () => {
  const b = Object.fromEntries(C.configButtons(die).map((x) => [x.config, x]));
  assert.equal(b.L12.duty, 'rated');
  assert.equal(b.L12.power_kw, 0.8); assert.equal(b.L12.torque_nm, 0.6); assert.equal(b.L12.mass_kg, 0.31);
  assert.equal(b.L20.duty, 'peak');                      // flag honoured
  assert.equal(b.L20.power_kw, 3);
});

t('a flag pointing at a missing duty falls back; no duties = no numbers', () => {
  const c = { name: 'X', default_duty: 'gone', duties: [duty('peak', 1, 1, 1, 1), duty('rated 1x9 mm', 2, 2, 2, 2)] };
  assert.equal(C.defaultDutyOf(c).name, 'rated 1x9 mm');
  assert.equal(C.defaultDutyOf({ name: 'Y', duties: [duty('a', 1, 1, 1, 1)] }).name, 'a');
  const b = C.configButtons(die).find((x) => x.config === 'L40');
  assert.equal(b.duty, null); assert.equal(b.power_kw, null); assert.equal(b.modes, 0);
});

t('the card badge date is the configuration\'s', () => {
  assert.equal(C.configButtons(die)[0].card_date, '2026-10-01');
  assert.equal(C.configButtons(die)[1].card_date, null);
});

t('selection: a remembered pick, else the loaded configuration, else the first', () => {
  const bs = C.configButtons(die);
  assert.equal(C.selectedConfig(bs, 'L20', null), 'L20');
  assert.equal(C.selectedConfig(bs, 'L20', 'L30'), 'L30');
  assert.equal(C.selectedConfig(bs, 'nope', 'nope2'), 'L12');
  assert.equal(C.selectedConfig([], null, null), null);
});

t('header dropdown: the modes of the loaded configuration, rated then peak then the rest', () => {
  assert.deepEqual(C.modeNames([die], 'CIANO14 40', 'L20'), ['rated', 'peak']);
  assert.deepEqual(C.modeNames([die], 'CIANO14 40', 'L12'), ['rated', 'peak']);
  assert.deepEqual(C.modeNames([die], 'CIANO14 40', 'L30'), ['S1 prop']);
  assert.deepEqual(C.modeNames([die], 'CIANO14 40', 'nope'), []);
  assert.deepEqual(C.modeNames([die], 'other', 'L12'), []);
});
