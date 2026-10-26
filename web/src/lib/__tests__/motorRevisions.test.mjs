// node --test: product revisions Rev A, Rev B ... (owner 2026-10-27) - the pure helpers, the wiring of the label (catalogue row, project
// variant header, production strip / title / list) and of the buttons (Assign Rev A / New revision), EN + ZH texts.  Synthetic names only.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { buildRevisionMap, motorTitleWithRevision, nextRevLetter, revisionAction, revisionFor, revisionKey } from '../motorRevisions.ts';
import { stripContextFor } from '../stripContext.ts';

const read = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');
const row = (die, config, rev, over = {}) => ({ die, config, rev, lineage: 'l1', latest: true, frozen: false, ...over });

test('letters: A..Z, then AA (the server counts the same way)', () => {
  assert.deepEqual(['A', 'B', 'Y', 'Z', 'AZ', 'ZZ'].map(nextRevLetter), ['B', 'C', 'Z', 'AA', 'BA', 'AAA']);
  assert.equal(nextRevLetter(''), '');
  assert.equal(nextRevLetter('a'), '');
  assert.equal(nextRevLetter('A1'), '');
});

test('the map matches a motor case-insensitively and by its canonical die', () => {
  const map = buildRevisionMap([row('DieX 40', 'L40', 'A', { latest: false, frozen: true }), row('DieX 40', 'L40-B', 'B'), { die: 1 }, null, row('D', 'c', 'a')]);
  assert.equal(map.size, 2);                                                      // the malformed rows and the lower-case letter are dropped
  assert.equal(revisionFor(map, { die: 'diex 40', config: 'l40' })?.rev, 'A');
  assert.equal(revisionFor(map, { die: 'Alias', canonical_die: 'DieX 40', config: 'L40-B' })?.rev, 'B');
  assert.equal(revisionFor(map, { die: 'DieX 40', config: 'L55' }), null);
  assert.equal(revisionFor(null, { die: 'x', config: 'y' }), null);
  assert.equal(revisionFor(new Map(), { die: 'x', config: 'y' }), null);
  assert.equal(revisionKey('A', 'b'), revisionKey('a', 'B'));
});

test('the title reads "die · configuration · Rev A" and stays plain without a revision', () => {
  assert.equal(motorTitleWithRevision('DieX 400_80', 'L100', 'A'), 'DieX 400_80 · L100 · Rev A');
  assert.equal(motorTitleWithRevision('DieX 400_80', 'L100', null), 'DieX 400_80 · L100');
  assert.equal(motorTitleWithRevision('D', 'c', 'B', (r) => `版本 ${r}`), 'D · c · 版本 B');
});

test('buttons: Assign Rev A without a revision, New revision on the working one, nothing on a replaced one or for a member', () => {
  assert.equal(revisionAction(null, true), 'assign');
  assert.equal(revisionAction(row('d', 'c', 'A'), true), 'new');
  assert.equal(revisionAction(row('d', 'c', 'A', { latest: false, frozen: true }), true), null);
  assert.equal(revisionAction(row('d', 'c', 'A', { latest: true, frozen: true }), true), null);
  assert.equal(revisionAction(null, false), null);
  assert.equal(revisionAction(row('d', 'c', 'A'), false), null);
});

test('the production strip carries the letter only when there is one', () => {
  const at = (production) => stripContextFor({ tab: 'production', project: null, production });
  assert.deepEqual(at({ die: 'D', config: 'c', rev: 'B' }), { kind: 'production', die: 'D', config: 'c', rev: 'B' });
  assert.deepEqual(at({ die: 'D', config: 'c', title: 'Run', rev: 'A' }), { kind: 'production', die: 'D', config: 'c', title: 'Run', rev: 'A' });
  assert.deepEqual(at({ die: 'D', config: 'c' }), { kind: 'production', die: 'D', config: 'c' });
});

test('wiring: the letter is plain text next to the name in the catalogue row, the project variant header and Production', () => {
  const text = read('../../components/common/MotorRevisionText.tsx');
  assert.match(text, /t\('revisions\.label', \{ rev \}\)/);
  assert.doesNotMatch(text, /<Chip|bgcolor|color:/);                                // uniform row style: no chip, no colour
  assert.match(read('../../components/catalog/FamilyCatalog.tsx'), /\{c\.name\}\s*<MotorRevisionText die=\{die\.name\} config=\{c\.name\} \/>/);
  assert.match(read('../../modules/development/components/DevelopmentPage.tsx'), /\{r\.variant\}<MotorRevisionText die=\{r\.motor\.die\} config=\{r\.motor\.config\} canonicalDie=\{r\.motor\.canonical_die\} \/>/);
  const page = read('../../modules/production/components/ProductionPage.tsx');
  assert.match(page, /\{die\} \/ \{config\}<RevisionLabel rev=\{prod\?\.product_revision\?\.rev\} \/>/);          // the page title
  assert.match(page, /setProduction\(\{ die, config, \.\.\.\(stripTitle \? \{ title: stripTitle \} : \{\}\), \.\.\.\(stripRev \? \{ rev: stripRev \} : \{\}\) \}\)/);
  assert.match(read('../../components/common/TabContextStrip.tsx'), /<b style=\{\{ color: 'var\(--text-1\)' \}\}>\{config\}<\/b><RevisionLabel rev=\{rev\} \/>/);   // the strip
  assert.match(read('../../modules/production/components/ProductionListPage.tsx'), /<RevisionLabel rev=\{p\.product_revision\?\.rev\} \/>/);
});

test('wiring: staff buttons in the variant controls, a required reason and the new-lamination box in the dialog', () => {
  const controls = read('../../modules/development/components/VariantControls.tsx');
  assert.match(controls, /revisionAction\(revision, staff\)/);
  assert.match(controls, /revisionButton === 'assign'[\s\S]*motorRevisionsApi\.assign\(\{ die, config: motor\.config, project_id: projectId, agreed_with_client: true \}\)/);
  assert.match(controls, /revisionButton === 'new' && revision[\s\S]*<NewRevisionDialog revision=\{revision\}/);
  const dialog = read('../../modules/development/components/NewRevisionDialog.tsx');
  assert.match(dialog, /disabled=\{busy \|\| !reason\.trim\(\)\}/);                 // no reason, no button
  assert.match(dialog, /required[^>]*multiline/);
  assert.match(dialog, /new_die: newDie, from_rev: revision\.rev/);
  assert.match(dialog, /t\('revisions\.newDie'\)/);
  const api = read('../../lib/motorRevisionsApi.ts');
  assert.match(api, /\/api\/motor-revisions\/index/);
  assert.match(api, /\/api\/motor-revisions\/assign/);
  assert.match(api, /\/api\/motor-revisions\/\$\{encodeURIComponent\(lineage\)\}\/new/);
});

test('texts: every key exists in EN and ZH, the label shows the letter', () => {
  const get = (o, path) => path.split('.').reduce((x, k) => x?.[k], o);
  const dev = { en: JSON.parse(read('../../locales/en/development.json')), zh: JSON.parse(read('../../locales/zh-CN/development.json')) };
  const com = { en: JSON.parse(read('../../locales/en/common.json')), zh: JSON.parse(read('../../locales/zh-CN/common.json')) };
  for (const key of ['assign', 'assignTip', 'new', 'newTip', 'dialogTitle', 'dialogLine', 'reason', 'reasonTip', 'newDie', 'newDieTip', 'create', 'cancel', 'failed']) {
    for (const lng of ['en', 'zh']) assert.ok(String(get(dev[lng], `revisions.${key}`) ?? '').length > 0, `${lng} revisions.${key}`);
  }
  assert.equal(get(com.en, 'revisions.label'), 'Rev {rev}');
  assert.equal(get(com.zh, 'revisions.label'), '版本 {rev}');
  assert.match(get(dev.en, 'revisions.dialogTitle'), /\{to\}/);
});
