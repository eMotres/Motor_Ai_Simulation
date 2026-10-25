import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { latestCurrentMotor, motorLabel, stripContextFor, stripKindForTab, visibleChips } from '../stripContext.ts';
import { projectStripContext } from '../../modules/development/projectStrip.ts';

const M = (config, status, extra = {}) => ({ die: 'DIE', config, status, ...extra });
const project = { id: 'p1', title: 'Drone arm', motors: [M('opt1', 'current'), M('opt2', 'current'), M('opt3', 'trial')] };
const at = (tab, extra = {}) => stripContextFor({ tab, project: null, production: null, ...extra });

test('machine-bound tabs keep the live machine', () => {
  for (const tab of ['compare', 'model3d', 'simulation', 'thermal', 'mechanical', 'controller', 'geometry', 'sweep', 'motors', 'materials', 'mesh', 'static3d', 'cost', 'comparePoints', 'schemas']) {
    assert.deepEqual(at(tab), { kind: 'live' }, tab);
    // a stale project or production never leaks onto them
    assert.deepEqual(at(tab, { project, production: { die: 'D', config: 'c' } }), { kind: 'live' }, tab);
  }
});

test('an unknown tab id falls back to the live machine, never to another motor', () => {
  assert.equal(stripKindForTab('something-new'), 'live');
});

test('Development and Production show no strip for now (owner 2026-10-26)', () => {
  for (const tab of ['development', 'production']) {
    assert.equal(stripKindForTab(tab), 'none', tab);
    assert.deepEqual(at(tab), { kind: 'none' }, tab);
    assert.deepEqual(at(tab, { project, production: { die: 'D', config: 'c' } }), { kind: 'none' }, tab);
  }
});

test('Admin and Designer have no motor context', () => {
  assert.deepEqual(at('admin', { project }), { kind: 'none' });
  assert.deepEqual(at('designer', { production: { die: 'D', config: 'c' } }), { kind: 'none' });
});

test('Configure is the live machine even for a project motor: one load path (owner 2026-10-26)', () => {
  assert.deepEqual(at('compare', { project }), { kind: 'live' });
  assert.deepEqual(at('compare'), { kind: 'live' });
  assert.deepEqual(at('simulation', { project }), { kind: 'live' });
});

test('latestCurrentMotor: last non-trial, else last, else null', () => {
  assert.equal(latestCurrentMotor([M('a', 'current'), M('b', 'trial')]).config, 'a');
  assert.equal(latestCurrentMotor([M('a'), M('b')]).config, 'b');
  assert.equal(latestCurrentMotor([M('a', 'trial'), M('b', 'trial')]).config, 'b');
  assert.equal(latestCurrentMotor([]), null);
});

test('chips: trials hidden unless selected, overflow folds into +N and keeps the selection on screen', () => {
  const many = Array.from({ length: 8 }, (_, i) => M(`c${i}`, 'current'));
  assert.equal(visibleChips([M('a', 'current'), M('b', 'trial')], null).shown.length, 1);
  assert.equal(visibleChips([M('a', 'current'), M('b', 'trial')], { die: 'DIE', config: 'b' }).shown.length, 2);
  const newest = visibleChips(many, { die: 'DIE', config: 'c7' }, 5);
  assert.deepEqual(newest.shown.map((m) => m.config), ['c3', 'c4', 'c5', 'c6', 'c7']);
  assert.equal(newest.hidden.length, 3);
  const old = visibleChips(many, { die: 'DIE', config: 'c1' }, 5);
  assert.ok(old.shown.some((m) => m.config === 'c1'));
  assert.equal(old.shown.length + old.hidden.length, 8);
  assert.equal(motorLabel({ die: 'D', config: 'c' }), 'D / c');
});

test('projectStripContext: linked motors stand in until the motors list is loaded, then status and points flags apply', () => {
  assert.equal(projectStripContext(null, []), null);
  const summary = { id: 'p1', title: 'Drone arm', linked_motors: [{ die: 'DIE', config: 'opt1' }, { die: 'DIE', config: 'opt2' }] };
  const early = projectStripContext(summary, []);
  assert.deepEqual(early.motors, [{ die: 'DIE', config: 'opt1' }, { die: 'DIE', config: 'opt2' }]);
  const loaded = projectStripContext(summary, [
    { die: 'DIE', config: 'opt1', status: 'current', can_open_points: true },
    { die: 'DIE', config: 'opt2', status: 'trial' },
    { die: 'DIE', config: 'snap', kind: 'history' },
  ]);
  assert.deepEqual(loaded.motors.map((m) => [m.config, m.status, m.pointsReady]), [['opt1', 'current', true], ['opt2', 'trial', false]]);
});

test('the strip never writes the remembered views (viewMemory keeps working) and the live line is only drawn for kind live', () => {
  for (const f of ['../stripContext.ts', '../openProjectMotor.ts', '../../stores/tabContextStore.ts']) {
    const src = readFileSync(new URL(f, import.meta.url), 'utf8');
    assert.ok(!/writeView|clearView|localStorage/.test(src), f);
  }
  const strip = readFileSync(new URL('../../components/common/ActiveFamilyStrip.tsx', import.meta.url), 'utf8');
  assert.match(strip, /stripCtx\.kind !== 'live'/);
  const view = readFileSync(new URL('../../components/common/TabContextStrip.tsx', import.meta.url), 'utf8');
  assert.ok(!/variant="caption"|helperText=/.test(view));
});
