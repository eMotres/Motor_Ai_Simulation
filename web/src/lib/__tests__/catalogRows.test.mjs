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

// ---- incident 2026-10-13: clicking "L155 motor" loaded nothing (it only picked the button) ----
const l155 = {
  name: 'CIANO10 200 opt',
  configs: [
    { name: 'L155 motor', default_duty: 'rated 1x9 mm', duties: [duty('rated 1x9 mm', 267.07, 179.9, 14200, 27.559), duty('peak 1x9 mm', 480.2, 230, 20000, 27.559)] },
    { name: 'L180 gen', default_duty: 'rated 0.5x9 mm', duties: [duty('rated 0.5x9 mm', 250, 170, 14000, 29), duty('peak 0.5x9 mm', 400, 200, 20000, 29)] },
    { name: 'Empty cfg', duties: [] },
  ],
};

t('a click on L155 motor LOADS its default mode (names with spaces pass through)', () => {
  const btns = C.configButtons(l155);
  // the owner was on the 40 mm die: nothing of this die is loaded
  const plan = C.configClickPlan(btns, 'L155 motor', null, false);
  assert.deepEqual(plan, { pick: 'L155 motor', load: 'rated 1x9 mm', refuse: null, alreadyLoaded: false });
  assert.equal(C.configClickPlan(btns, 'L180 gen', 'L155 motor', false).load, 'rated 0.5x9 mm');
});

t('the loaded configuration is only picked again, and the click SAYS so; the play glyph reloads it', () => {
  const plan = C.configClickPlan(C.configButtons(l155), 'L155 motor', 'L155 motor', false);
  assert.deepEqual(plan, { pick: 'L155 motor', load: null, refuse: null, alreadyLoaded: true });
});

// ---- incident 2026-10-15: the highlighted L12 chip did nothing while CIANO28 85 / L13 was open ----
const ciano14 = { name: 'CIANO14 40 new', configs: [
  { name: 'L12', default_duty: 'rated', duties: [duty('rated', 1, 1, 1, 1), duty('peak', 1, 1, 1, 1)] },
  { name: 'L20', default_duty: 'rated', duties: [duty('rated', 1, 1, 1, 1), duty('peak', 1, 1, 1, 1)] },
] };

t('loadedMotor: an ordinary account is judged by its OWN remembered motor, never the shared server context', () => {
  const shared = { active: true, die: 'CIANO14 40 new', config: 'L12', duty: 'rated' };   // the owner's machine
  const mine = { die: 'CIANO28 85', config: 'L13', duty: 'rated' };
  assert.deepEqual(C.loadedMotor(true, mine, shared), mine);
  assert.equal(C.loadedMotor(true, null, shared), null, 'nothing remembered = nothing loaded');
  assert.deepEqual(C.loadedMotor(false, mine, shared), { die: 'CIANO14 40 new', config: 'L12', duty: 'rated' });
  assert.equal(C.loadedMotor(false, null, { active: false }), null);
});

t('the HIGHLIGHTED chip loads when the loaded motor is another one (the live bug)', () => {
  const btns = C.configButtons(ciano14);
  const loaded = C.loadedMotor(true, { die: 'CIANO28 85', config: 'L13', duty: 'rated' },
    { active: true, die: 'CIANO14 40 new', config: 'L12', duty: 'rated' });
  // this die's row: nothing of it is loaded -> what the component passes is null
  const forThisDie = loaded && loaded.die === 'CIANO14 40 new' ? loaded : null;
  const plan = C.configClickPlan(btns, 'L12', forThisDie, false);
  assert.equal(plan.load, 'rated'); assert.equal(plan.alreadyLoaded, false);
  // after loading L20 on this die, L12 (not loaded now) loads again, L20 answers "already loaded"
  const l20 = { config: 'L20', duty: 'rated' };
  assert.equal(C.configClickPlan(btns, 'L12', l20, false).load, 'rated');
  const again = C.configClickPlan(btns, 'L20', l20, false);
  assert.equal(again.load, null); assert.equal(again.alreadyLoaded, true);
});

t('a chip loads its default mode when another mode of the same configuration is loaded', () => {
  const btns = C.configButtons(ciano14);
  const plan = C.configClickPlan(btns, 'L12', { config: 'L12', duty: 'peak' }, false);
  assert.equal(plan.load, 'rated'); assert.equal(plan.alreadyLoaded, false);
  assert.equal(C.configClickPlan(btns, 'L12', { config: 'L12', duty: 'rated' }, false).alreadyLoaded, true);
});

t('a click that cannot load is never silent: it names the reason', () => {
  const btns = C.configButtons(l155);
  assert.equal(C.configClickPlan(btns, 'Empty cfg', null, false).refuse, 'noMode');
  assert.equal(C.configClickPlan(btns, 'Gone', null, false).refuse, 'noMode');
  const busy = C.configClickPlan(btns, 'L155 motor', null, true);
  assert.equal(busy.load, null); assert.equal(busy.refuse, 'busy');
});
