import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { formatBytes, isCoverImage, MAX_DOCUMENT_BYTES } from '../documentsModel.ts';

const locale = (lang) => JSON.parse(readFileSync(new URL(`../../../locales/${lang}/development.json`, import.meta.url), 'utf8'));

test('formatBytes picks a readable unit', () => {
  assert.equal(formatBytes(0), '0 B');
  assert.equal(formatBytes(1536), '1.5 KB');
  assert.equal(formatBytes(5 * 1024 * 1024), '5.0 MB');
  assert.equal(formatBytes(MAX_DOCUMENT_BYTES), '200 MB');
  assert.equal(formatBytes(Number.NaN), '0 B');
});

test('the Documents tab strings exist in EN and ZH with the same keys', () => {
  const keys = (o, p = '') => Object.entries(o).flatMap(([k, v]) => (v && typeof v === 'object' ? keys(v, `${p}${k}.`) : [`${p}${k}`])).sort();
  const en = locale('en'), zh = locale('zh-CN');
  assert.deepEqual(keys(zh.documents), keys(en.documents));
  assert.ok(en.tabs.documents && zh.tabs.documents);
  for (const f of ['kicad', 'fabrication', 'bom', '3d', 'docs']) assert.ok(en.documents.folders[f] && zh.documents.folders[f], f);
});

test('no English words are left in the ZH Documents strings (units and placeholders are fine)', () => {
  const zh = locale('zh-CN');
  const values = [zh.tabs.documents, ...Object.values(zh.documents).flatMap((v) => (typeof v === 'object' ? Object.values(v) : [v]))];
  for (const v of values) {
    const words = String(v).replace(/\{[^}]*\}/g, '').replace(/\b\d+\s*(MB|KB|GB)\b/g, '').match(/[A-Za-z]{2,}/g) ?? [];
    assert.deepEqual(words, [], `Latin text in ZH string: ${v}`);
  }
});

test('the project page offers the Documents view to every member and the link grammar knows it', () => {
  const page = readFileSync(new URL('../components/DevelopmentPage.tsx', import.meta.url), 'utf8');
  assert.match(page, /id: 'documents' as const/);
  assert.match(page, /view === 'documents' && <ProjectDocuments/);
  assert.match(readFileSync(new URL('../../../lib/appLink.ts', import.meta.url), 'utf8'), /PROJECT_VIEWS = \[[^\]]*'documents'/);
});

test('only PNG, JPEG and WebP files are offered as the project cover', () => {
  for (const ok of ['top.png', 'TOP.PNG', 'a.jpg', 'a.jpeg', 'a.webp']) assert.equal(isCoverImage(ok), true, ok);
  for (const no of ['a.pdf', 'a.step', 'a.png.zip', 'png', 'a.svg', 'a.gif']) assert.equal(isCoverImage(no), false, no);
});

test('the cover shows on the project card and at the top of the project page, with strings in both languages', () => {
  const card = readFileSync(new URL('../components/ProjectOverview.tsx', import.meta.url), 'utf8');
  const page = readFileSync(new URL('../components/DevelopmentPage.tsx', import.meta.url), 'utf8');
  assert.match(card, /p\.cover_version && <ProjectCover/);
  // on the card the cover is the small corner picture, in place of the motor section (owner 2026-11-02: one style)
  assert.match(card, /<ProjectCover[^>]*height=\{CARD_THUMB_SIZE\}[\s\S]*?position: 'absolute', right: 10, bottom: 8/);
  assert.match(card, /!p\.cover_version && motor && <CardMotorThumb/);
  assert.match(page, /selected\.cover_version && <ProjectCover/);
  for (const lang of ['en', 'zh-CN']) for (const k of ['coverSet', 'coverClear', 'coverUpload', 'coverTip', 'coverNotImage']) assert.ok(locale(lang).documents[k], `${lang}.${k}`);
});
