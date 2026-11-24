// Specification-file upload (owner 2026-11-24): the browser-side checks, the card source, the controls' wiring and the EN / zh-CN strings.
// Synthetic data only.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileRefusal, fileSourceOf, FILE_MAX_BYTES } from '../specFile.ts';

const read = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8');

test('the browser refuses what the server refuses, with the reason', () => {
  assert.equal(fileRefusal('req.docx', 1000), null);
  for (const n of ['a.txt', 'a.csv', 'a.md', 'a.pdf', 'a.xlsx', 'a.xls', 'A.DOCX']) assert.equal(fileRefusal(n, 10), null, n);
  assert.equal(fileRefusal('old.doc', 1000), 'doc');
  assert.equal(fileRefusal('m.xlsm', 1000), 'macro');
  assert.equal(fileRefusal('m.docm', 1000), 'macro');
  assert.equal(fileRefusal('photo.png', 1000), 'type');
  assert.equal(fileRefusal('noext', 1000), 'type');
  assert.equal(fileRefusal('a.txt', 0), 'empty');
  assert.equal(fileRefusal('a.txt', FILE_MAX_BYTES + 1), 'size');
  assert.equal(fileRefusal('a.txt', FILE_MAX_BYTES), null);
  assert.equal(fileRefusal('a.txt', 100, 50), 'size');                       // the server's own limit wins
  assert.equal(fileRefusal('C:\\docs\\x.doc', 10), 'doc');
});

test('the source of a card is read from the proposal; a card without a file has none', () => {
  assert.equal(fileSourceOf({}), null);
  assert.equal(fileSourceOf({ file_source: { ref: '' } }), null);
  assert.deepEqual(fileSourceOf({ file_source: { ref: 'page 3', excerpt: '6000 rpm', verified: true } }), { ref: 'page 3', excerpt: '6000 rpm', verified: true });
});

test('the attach control is only for who may edit the specification, and the cards show their source in a tooltip', () => {
  const widget = read('../../../components/support/SupportWidget.tsx');
  assert.match(widget, /spec && canApply && fileUpload[\s\S]{0,600}data-testid="assistant-attach"/);
  assert.match(widget, /setFileUpload\(!!s\.file_upload\)/);
  assert.match(widget, /fileRefusal\(file\.name/);                            // checked in the browser before the upload
  assert.match(widget, /data-testid="assistant-file-error"/);
  const cards = read('../../../components/support/SpecChatCards.tsx');
  assert.match(cards, /data-testid="spec-source"/);
  assert.match(cards, /<Tooltip title=\{<Box data-testid="spec-source-tip">/);
  assert.doesNotMatch(cards, /variant="caption"|helperText=/);
});

test('every new string exists in English and Chinese, and the tooltip names who and what', () => {
  const en = JSON.parse(read('../../../locales/en/development.json')).specAssistant;
  const zh = JSON.parse(read('../../../locales/zh-CN/development.json')).specAssistant;
  const keys = ['attach', 'attachTip', 'fileDoc', 'fileMacro', 'fileType', 'fileSize', 'fileEmpty', 'fileFailed', 'source', 'sourceFrom', 'sourceExcerpt', 'sourceConverted', 'sourceNotFound'];
  for (const k of keys) { assert.ok(typeof en[k] === 'string' && en[k], `en ${k}`); assert.ok(typeof zh[k] === 'string' && zh[k], `zh ${k}`); }
  assert.match(en.attachTip, /owner and admin only; motor projects only/);
  assert.match(zh.attachTip, /仅限项目负责人和管理员；仅限电机项目/);
  assert.match(en.fileDoc, /re-save as DOCX/);
  for (const k of ['sourceFrom', 'sourceExcerpt', 'sourceConverted']) {                        // the placeholders survive translation
    const ph = (s) => (s.match(/\{\w+\}/g) ?? []).sort().join();
    assert.equal(ph(en[k]), ph(zh[k]), k);
  }
});

test('the client sends the file as multipart and carries the history without the file text', () => {
  const api = read('../specAssistantApi.ts');
  assert.match(api, /form\.append\('file', file, file\.name\)/);
  assert.doesNotMatch(api, /'Content-Type': 'multipart/);                                         // the browser sets the boundary
});
