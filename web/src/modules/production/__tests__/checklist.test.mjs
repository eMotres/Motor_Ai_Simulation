// node --test — pure helpers of the production checklist panel. Imports checklistHelpers.ts (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';

let H = null;
try { H = await import('../checklistHelpers.ts'); } catch { /* old Node */ }
const t = H ? test : test.skip;

t('pick: the viewer language, else the other one', () => {
  assert.equal(H.pick({ en: 'Press', zh: '压制' }, 'zh'), '压制');
  assert.equal(H.pick({ en: 'Press', zh: '' }, 'zh'), 'Press');
  assert.equal(H.pick({ en: '', zh: '压制' }, 'en'), '压制');
});

t('groupItems: server order kept, open filter applied, categories grouped', () => {
  const it = (key, category, open) => ({ key, category, open });
  const items = [it('a', 'assembly', true), it('b', 'assembly', false), it('c', 'tests', false), it('d', 'parts', true)];
  assert.deepEqual(H.groupItems(items, false).map((g) => [g.category, g.items.map((x) => x.key)]), [['assembly', ['a', 'b']], ['tests', ['c']], ['parts', ['d']]]);
  assert.deepEqual(H.groupItems(items, true).map((g) => [g.category, g.items.map((x) => x.key)]), [['assembly', ['a']], ['parts', ['d']]]);
});
