// node --test: the side-by-side comparison rows of the specification points (owner 2026-11-25).  Made-up values.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { comparedMotors, deltaText, needsAttention, requirementRows, suggestedCount } from '../specSync.ts';

const read = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
const en = JSON.parse(read('../../../locales/en/development.json'));
const zh = JSON.parse(read('../../../locales/zh-CN/development.json'));
const req = { name: 'Cruise', torque_Nm: 10, speed_rpm: 3000, duration_s: 60, voltage_V: 48 };
const cmp = (over = {}) => ({ spec_row_id: 'r1', state: 'matched', reasons: [], duty: 'cruise', duty_id: 'd-1', link: { duty_id: 'd-1' }, requirement: req,
  checks: { torque: { status: 'ok', required: 10, result: 10.5, delta: 0.5 }, speed: { status: 'ok', required: 3000, result: 3010, delta: 10 },
            voltage: { status: 'fail', required: 48, result: 50, delta: 2 }, thermal: { status: 'ok', required: 60, result: null, delta: null, holds: 'indefinitely' } }, ...over });
const motor = (cs, extra = {}) => ({ die: 'D', config: 'C', available: true, point_comparisons: cs, ...extra });

test('every requirement is a row, with the three columns of each check', () => {
  const rows = requirementRows(motor([cmp(), cmp({ spec_row_id: 'r2', state: 'no_point', duty: null, checks: {}, link: null, suggested: 'cruise', reasons: ['not_linked'] })]));
  assert.equal(rows.length, 2);
  assert.deepEqual(rows[0].cells[0], { key: 'torque', required: 10, result: 10.5, delta: '+0.5', status: 'ok' });
  assert.equal(rows[0].cells[2].delta, '+2');
  assert.deepEqual(rows[1].cells.map((c) => c.required), [10, 3000, 48, 60]);       // an unlinked point still shows its requirement
  assert.ok(rows[1].cells.every((c) => c.result === null && c.delta === ''));
  assert.equal(rows[1].suggested, 'cruise');
});

test('attention, suggestions and which motors are compared', () => {
  assert.equal(needsAttention(cmp()), false);
  assert.equal(needsAttention(cmp({ state: 'stale' })), true);
  const m = motor([cmp({ state: 'no_point', link: null, suggested: 'cruise' }), cmp({ spec_row_id: 'r3', state: 'no_point', link: null })]);
  assert.equal(suggestedCount(m), 1);
  const list = [m, motor([]), motor([cmp()], { kind: 'history' }), motor([cmp()], { available: false }), { die: 'X', config: 'Y' }];
  assert.equal(comparedMotors(list).length, 1);
});

test('the difference text keeps its sign and drops noise', () => {
  assert.equal(deltaText({ status: 'ok', required: 1, result: 1.004, delta: 0.004 }), '0');
  assert.equal(deltaText({ status: 'ok', required: 1, result: 0.5, delta: -0.5 }), '-0.5');
  assert.equal(deltaText(undefined), '');
  assert.equal(deltaText({ status: 'unknown', required: 1, result: null, delta: null }), '');
});

test('EN and zh-CN carry the same specSync keys, and no money or time word', () => {
  const flat = (o, p = '') => Object.entries(o).flatMap(([k, v]) => (typeof v === 'object' ? flat(v, `${p}${k}.`) : [`${p}${k}`]));
  assert.deepEqual(flat(en.specSync).sort(), flat(zh.specSync).sort());
  assert.doesNotMatch(JSON.stringify(en.specSync), /\b(prices?|costs?|quotes?|budget|days?)\b/i);
  for (const s of ['no_point', 'stale', 'differs', 'matched']) assert.ok(en.specSync.stateName[s] && en.specSync.stateTip[s]);
});
