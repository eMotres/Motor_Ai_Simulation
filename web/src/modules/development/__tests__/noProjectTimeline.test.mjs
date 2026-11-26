// node --test: the project page has no Timeline tab any more (owner 2026-11-25: the project timeline was never used; the schedule
// lives in Production).  The server store and routes stay; the web tab, its editor ("Create from cost estimate", "Freeze baseline")
// and its locale keys are gone.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join, dirname } from 'node:path';

const dev = join(dirname(fileURLToPath(import.meta.url)), '..');
const web = join(dev, '..', '..', '..');
const read = (p) => readFileSync(p, 'utf8');

test('the project page offers no Timeline view', () => {
  const page = read(join(dev, 'components', 'DevelopmentPage.tsx'));
  assert.ok(!/'timeline'/.test(page), 'no timeline view id');
  assert.ok(!/ProjectTimeline/.test(page), 'no ProjectTimeline component');
  assert.ok(!existsSync(join(dev, 'components', 'ProjectTimeline.tsx')));
  assert.ok(!existsSync(join(dev, 'timeline.ts')));
});

test('no timeline calls in the development api and no timeline keys in either locale', () => {
  assert.ok(!/timeline/i.test(read(join(dev, 'api.ts'))));
  for (const lang of ['en', 'zh-CN']) {
    const d = JSON.parse(read(join(web, 'src', 'locales', lang, 'development.json')));
    assert.equal(d.tabs.timeline, undefined, lang);
    assert.equal(d.timeline, undefined, lang);
  }
});
