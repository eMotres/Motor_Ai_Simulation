import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createErrorText, createPayload, createReady } from '../createProject.ts';

const t = (key, args) => `${key}:${JSON.stringify(args ?? {})}`;

test('a customer posts only a company; the server makes the id and the name', () => {
  assert.deepEqual(createPayload(false, { title: ' 7-inch prop motor ', org: ' ', id: 'ignored' }), { customer_org: '' });
  assert.deepEqual(createPayload(false, { title: '', org: ' Acme ', id: '' }), { customer_org: 'Acme' });
});

test('staff post the id and the customer organisation as before', () => {
  assert.deepEqual(createPayload(true, { title: 'A', org: 'Acme', id: ' acme-1 ' }), { id: 'acme-1', title: 'A', customer_org: 'Acme' });
});

test('a customer can always press Create; staff need a title, an organisation and an id', () => {
  assert.equal(createReady(false, { title: '', org: '', id: '' }), true);
  assert.equal(createReady(true, { title: 'x', org: '', id: 'a' }), false);
  assert.equal(createReady(true, { title: 'x', org: 'o', id: '' }), false);
  assert.equal(createReady(true, { title: 'x', org: 'o', id: 'a' }), true);
});

test('the open-project limit is shown from the localised string, other refusals keep the server text', () => {
  const limit = Object.assign(new Error('You can have up to 5 open projects'), { status: 429, body: { detail: { code: 'project_limit', limit: 5 } } });
  assert.equal(createErrorText(limit, t), 'projectLimit:{"count":5}');
  const other = Object.assign(new Error('Project already exists'), { status: 409, body: { detail: 'Project already exists' } });
  assert.equal(createErrorText(other, t), 'Project already exists');
  assert.equal(createErrorText('boom', t), 'boom');
  assert.equal(createErrorText(Object.assign(new Error('rate'), { status: 429, body: null }), t), 'rate');
});
