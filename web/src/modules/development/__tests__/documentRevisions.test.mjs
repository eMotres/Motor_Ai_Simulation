import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { groupByRevision, isGroupOpen } from '../documentRevisions.ts';

const locale = (lang) => JSON.parse(readFileSync(new URL(`../../../locales/${lang}/development.json`, import.meta.url), 'utf8'));
const d = (id, revision) => ({ id, revision });

test('groups keep first-appearance order and server order inside a group', () => {
  const g = groupByRevision([d(1, 'R06'), d(2, 'R05'), d(3, 'R06'), d(4, ''), d(5, 'R05'), d(6, '  ')]);
  assert.deepEqual(g.map((x) => x.key), ['R06', 'R05', '']);
  assert.deepEqual(g[0].docs.map((x) => x.id), [1, 3]);
  assert.deepEqual(g[1].docs.map((x) => x.id), [2, 5]);
  assert.deepEqual(g[2].docs.map((x) => x.id), [4, 6]);
});

test('revision is trimmed; missing revision joins the empty group; empty list gives no group', () => {
  assert.deepEqual(groupByRevision([d(1, ' R1 '), d(2, 'R1'), { id: 3 }]).map((x) => [x.key, x.docs.length]), [['R1', 2], ['', 1]]);
  assert.deepEqual(groupByRevision([]), []);
});

test('first group open by default, others closed, explicit toggles win', () => {
  const g = groupByRevision([d(1, 'R06'), d(2, 'R05')]);
  assert.equal(isGroupOpen(g, 'R06', {}), true);
  assert.equal(isGroupOpen(g, 'R05', {}), false);
  assert.equal(isGroupOpen(g, 'R06', { R06: false }), false);
  assert.equal(isGroupOpen(g, 'R05', { R05: true }), true);
});

test('the no-revision label exists in EN and ZH', () => {
  assert.equal(locale('en').documents.noRevision, 'No revision');
  assert.equal(locale('zh-CN').documents.noRevision, '未标注版本');
});

test('the Documents tab wires the grouping with an accessible toggle', () => {
  const src = readFileSync(new URL('../components/ProjectDocuments.tsx', import.meta.url), 'utf8');
  assert.match(src, /groupByRevision\(shown\)/);
  assert.match(src, /groups\.length < 2 \? shown\.map\(renderRow\)/);
  assert.match(src, /data-testid="documents-revision-group"/);
  assert.match(src, /aria-expanded=\{open\}/);
});
