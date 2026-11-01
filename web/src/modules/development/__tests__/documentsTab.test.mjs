import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { formatBytes, MAX_DOCUMENT_BYTES } from '../documentsModel.ts';

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
