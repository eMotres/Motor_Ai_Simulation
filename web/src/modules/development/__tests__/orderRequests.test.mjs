import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { activeOrderRequest, motorHasResults, orderQuantity } from '../orderRequests.ts';

const here = dirname(fileURLToPath(import.meta.url));
const read = (path) => readFileSync(join(here, path), 'utf8');

test('the button needs a computed result', () => {
  assert.equal(motorHasResults({ can_open_points: true }), true);
  assert.equal(motorHasResults({ results: [{ has_result: false }, { has_result: true }] }), true);
  assert.equal(motorHasResults({ results: [{ has_result: false }] }), false);
  assert.equal(motorHasResults({}), false);
});

test('a closed request lets the customer ask again; an open one replaces the button', () => {
  const rows = [{ id: '1', die: 'D', config: 'C', quantity: 1, note: '', by: 'a', at: 't', status: 'closed' }];
  assert.equal(activeOrderRequest(rows, 'd', 'c'), null);
  rows.push({ ...rows[0], id: '2', status: 'open' });
  assert.equal(activeOrderRequest(rows, 'd', 'c').id, '2');
  assert.equal(activeOrderRequest(rows, 'd', 'other'), null);
});

test('quantity is a whole number of at least 1', () => {
  assert.equal(orderQuantity('3'), 3);
  for (const bad of ['', '0', '1.5', '-2', 'x', '2000000']) assert.equal(orderQuantity(bad), null);
});

test('the dialog and both locales carry the contact line and no price', () => {
  const component = read('../components/OrderMotorButton.tsx');
  assert.match(component, /order\.contact/);
  assert.match(component, /minHeight: 44/);
  const en = JSON.parse(read('../../../locales/en/development.json')).order;
  const zh = JSON.parse(read('../../../locales/zh-CN/development.json')).order;
  assert.deepEqual(Object.keys(en), Object.keys(zh));
  assert.equal(en.button, 'Order this motor');
  assert.equal(zh.button, '订购此电机');
  assert.match(en.contact, /price and lead time/);
});
