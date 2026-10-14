/**
 * The concept diagram's geometry and the landing's public-safe content.
 *
 *   • every labelled box sits inside the viewBox and no two overlap;
 *   • the slogan is exactly the owner's wording in EN and zh-CN;
 *   • the footer says "patent pending" (never "patented"), lists no
 *     application numbers, and the locales carry no unpublished design detail;
 *   • the developer links point at the deploy branch and name files that exist.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { VIEW, STEP_IDS, allBoxes, stepArrows, stepBoxes } from '../conceptLayout.ts';
import { DEV_LINKS, BRANCH, docUrl, CHAIN_IDS } from '../landingLinks.ts';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.resolve(HERE, '..', '..', '..', '..');
const REPO = path.resolve(WEB, '..');
const loc = (lng) => JSON.parse(fs.readFileSync(
  path.join(WEB, 'src', 'locales', lng, 'landing.json'), 'utf8'));

test('every box is inside the viewBox', () => {
  for (const b of allBoxes()) {
    assert.ok(b.x >= 0 && b.y >= 0 && b.x + b.w <= VIEW.w && b.y + b.h <= VIEW.h,
      `${b.id} leaves the viewBox`);
  }
});

test('no two boxes overlap', () => {
  const bs = allBoxes();
  for (let i = 0; i < bs.length; i++) {
    for (let j = i + 1; j < bs.length; j++) {
      const a = bs[i], b = bs[j];
      const apart = a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y;
      assert.ok(apart, `${a.id} overlaps ${b.id}`);
    }
  }
});

test('five steps in order, four arrows between them', () => {
  assert.deepEqual(stepBoxes().map((b) => b.id), [...STEP_IDS]);
  assert.equal(stepArrows().length, 4);
  assert.deepEqual(CHAIN_IDS, ['idea', ...STEP_IDS, 'product']);
});

test('the slogan is the owner\'s exact wording', () => {
  assert.equal(loc('en').hero.title, 'From idea to product, together');
  assert.equal(loc('zh-CN').hero.title, '从构想到产品，携手同行');
});

test('every id the diagram and the strip use has a label in both locales', () => {
  for (const lng of ['en', 'zh-CN']) {
    const l = loc(lng);
    for (const id of CHAIN_IDS) assert.ok(l.chain[id], `${lng}: chain.${id}`);
    for (const id of STEP_IDS) {
      assert.ok(l.concept[id].sub && l.steps[id], `${lng}: step ${id}`);
    }
    for (const id of ['open', 'agents', 'modules', 'compute']) {
      assert.ok(l.concept[id].title && l.concept[id].sub, `${lng}: tag ${id}`);
    }
  }
});

test('footer: patent pending, never patented, no application numbers', () => {
  assert.match(loc('en').footer.licence, /patent pending/);
  assert.match(loc('en').footer.licence, /Apache-2\.0/);
  assert.match(loc('en').footer.licence, /requires a licence from MOTRES/);
  for (const lng of ['en', 'zh-CN']) {
    const all = JSON.stringify(loc(lng));
    assert.doesNotMatch(all, /patented/i, `${lng}: "patented"`);
    assert.doesNotMatch(all, /PCT|WO ?20|058444|060049|053894/, `${lng}: application number`);
    assert.doesNotMatch(all, /foil|ceramic|coil.?blank|cut-?out/i, `${lng}: unpublished detail`);
  }
});

test('developer links point at the deploy branch and name existing files', () => {
  assert.equal(BRANCH, 'pre-migration-freeze-2026-09-15');
  assert.equal(DEV_LINKS.length, 5);
  for (const l of DEV_LINKS) {
    assert.ok(l.href.startsWith('https://github.com/eMotres/Motor_Ai_Simulation'));
    if (l.id !== 'github') {
      assert.ok(l.href.includes(`/blob/${BRANCH}/docs/`), l.href);
      const file = l.href.split(`/blob/${BRANCH}/`)[1];
      assert.ok(fs.existsSync(path.join(REPO, file)), `missing doc: ${file}`);
    }
  }
  assert.equal(docUrl('/docs/X.md'), `https://github.com/eMotres/Motor_Ai_Simulation/blob/${BRANCH}/docs/X.md`);
});
