// node --test: the catalog's project chip menu carries the three "Priority: ..." items for admins only, and they write through the
// owner queue's own route (ownerQueueApi.setPriority -> POST /api/owner-queue/priority), then re-read the Overview.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('../CatalogByDie.tsx', import.meta.url), 'utf8');
const api = readFileSync(new URL('../../../modules/overview/decisions/ownerQueueApi.ts', import.meta.url), 'utf8');
const locale = (lng) => JSON.parse(readFileSync(new URL(`../../../locales/${lng}/common.json`, import.meta.url), 'utf8'));

test('the menu has the three priority items, admin only, calling the one route and re-reading', () => {
  assert.match(src, /isAdmin \? \(\['urgent', 'normal', 'low'\] as const\)\.map/);
  assert.match(src, /data-testid=\{`project-priority-\$\{p\}`\}/);
  assert.match(src, /ownerQueueApi\.setPriority\(id, p\)[\s\S]{0,200}setOverviewGen/);
  assert.match(api, /setPriority:[^\n]*'POST', '\/priority', \{ project_id, priority \}/);
});

test('the item text exists in EN and zh-CN for all three values', () => {
  for (const lng of ['en', 'zh-CN']) {
    const q = locale(lng).ownerQueue;
    assert.equal(typeof q.priorityMenu, 'string');
    for (const p of ['urgent', 'normal', 'low']) assert.equal(typeof q.priority[p], 'string');
  }
});
