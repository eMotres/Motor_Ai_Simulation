// node --test: the owner-queue card RENDERED with the real EN and zh-CN locale files (esbuild bundles the real component, as
// catalogFiltersRender.test.mjs does): a decision with options, the recommended mark and the counter text; an outside_message with
// the FULL detail and the recipient BEFORE the buttons; a message with Read / Reply; and no raw locale keys.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, '..', '..', '..', '..');
const tmp = mkdtempSync(join(tmpdir(), 'oq-test-'));
const out = join(tmp, 'bundle.mjs');
const rel = (p) => resolve(src, p).split('\\').join('/');
const mem = new Map();
globalThis.localStorage = { getItem: (k) => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => mem.set(k, String(v)), removeItem: (k) => mem.delete(k) };

writeFileSync(join(tmp, 'entry.jsx'), `
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { I18nextProvider } from 'react-i18next';
import { createI18n } from '${rel('i18n/core.ts')}';
import DecisionCard from '${rel('modules/overview/decisions/DecisionCard.tsx')}';
import enCommon from '${rel('locales/en/common.json')}';
import zhCommon from '${rel('locales/zh-CN/common.json')}';
export const makeI18n = (lng) => createI18n({ lng, resources: { en: { common: enCommon }, 'zh-CN': { common: zhCommon } } });
const noop = () => {};
export const card = (i18n, item, extra = {}) => renderToStaticMarkup(React.createElement(I18nextProvider, { i18n },
  React.createElement(DecisionCard, { item, now: Date.parse('2026-11-25T12:00:00Z'), text: '', onText: noop, onOption: noop, onSend: noop, onRead: noop, ...extra })));
`);
await build({
  entryPoints: [join(tmp, 'entry.jsx')], bundle: true, platform: 'node', format: 'esm', outfile: out, jsx: 'automatic', logLevel: 'error',
  nodePaths: [resolve(src, '..', 'node_modules')], define: { 'import.meta.env': '{"DEV":false,"VITE_API_URL":"http://api.test"}' },
  banner: { js: "import { createRequire } from 'module'; const require = createRequire(import.meta.url);" },
});
const m = await import(pathToFileURL(out).href);
process.on('exit', () => { try { rmSync(tmp, { recursive: true, force: true }); } catch { /* */ } });
const ready = (i) => new Promise((r) => (i.isInitialized ? r() : i.on('initialized', () => r())));
const decode = (s) => s.replace(/&#x27;|&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');

const base = { id: 'oq-000000000001', project_id: 'p1', asked_by: 'US · pump', token_name: 'tok', blocks: 'the next run', created_at: '2026-11-25T07:00:00Z',
  state: 'open', answer: null, detail: null, recipient: null };
const LONG = Array.from({ length: 60 }, (_, i) => `Line ${i + 1} of the final text.`).join('\n\n') + '\n\n| a | b |\n|---|---|\n| 1 | 2 |';
const decision = { ...base, kind: 'decision', question: 'Which winding?', options: [{ label: 'Alpha', recommended: true }, { label: 'Beta', recommended: false }] };
const outside = { ...base, id: 'oq-000000000002', kind: 'outside_message', question: 'Send the letter?', detail: LONG, recipient: 'a@example.test, project thread p1',
  options: [{ label: 'Approve', recommended: false }, { label: 'Reject', recommended: false }] };
const catalog = { ...outside, id: 'oq-000000000003', kind: 'catalog_write', recipient: null, detail: '| field | old | new |\n|---|---|---|\n| mass | 1 | 2 |' };
const message = { ...base, id: 'oq-000000000004', kind: 'message', question: 'Report ready', options: [], detail: 'The report is in the folder.' };

for (const lng of ['en', 'zh-CN']) {
  test(`${lng}: decision card shows the project, the asker, the age, the options and the recommended mark`, async () => {
    const i18n = m.makeI18n(lng); await ready(i18n);
    const h = decode(m.card(i18n, decision, { projectTitle: 'Pump project', projectHref: '/project/p1' }));
    assert.ok(h.includes('Pump project') && h.includes('US · pump') && h.includes('Which winding?') && h.includes('the next run'), h);
    assert.ok(h.includes('1. Alpha') && h.includes('2. Beta'), h);
    assert.equal((h.match(/data-recommended="true"/g) || []).length, 1);
    assert.ok(h.includes(lng === 'en' ? '(Recommended)' : '(推荐)'), h);
    assert.ok(h.includes(lng === 'en' ? '5 h' : '5 小时'));
    assert.ok(!h.includes('data-testid="oq-read"'));
  });

  test(`${lng}: an outside_message shows the whole detail and the recipient BEFORE the buttons, nothing collapsed`, async () => {
    const i18n = m.makeI18n(lng); await ready(i18n);
    const h = decode(m.card(i18n, outside));
    const buttons = h.indexOf('data-testid="oq-buttons"');
    assert.ok(buttons > 0);
    for (const needle of ['Line 1 of the final text.', 'Line 60 of the final text.', '<table', 'a@example.test, project thread p1']) {
      const at = h.indexOf(needle);
      assert.ok(at > 0 && at < buttons, `${needle} must come before the buttons`);
    }
    assert.ok(h.indexOf('data-testid="oq-recipient"') < buttons && h.includes('data-full="true"'));
    const region = h.slice(h.indexOf('data-testid="oq-detail"'), buttons);
    assert.ok(!/<details|show more|style="[^"]*(max-height|overflow:\s*hidden|-webkit-line-clamp)/i.test(region), 'no clipping or collapsing');
    assert.ok(!/MuiCollapse|MuiAccordion/.test(region));
    assert.ok(h.includes('1. Approve') && h.includes('2. Reject'));
    assert.ok(!h.includes('data-testid="oq-send"'), 'an approval card has no text-only Send: the text goes with Approve / Reject');
    const c = decode(m.card(i18n, catalog));
    assert.ok(c.includes('<table') && c.indexOf('<table') < c.indexOf('data-testid="oq-buttons"') && !c.includes('data-testid="oq-recipient"'));
  });

  test(`${lng}: a message has Read and Reply, and no raw locale keys appear on any card`, async () => {
    const i18n = m.makeI18n(lng); await ready(i18n);
    const h = decode(m.card(i18n, message));
    assert.ok(h.includes('data-testid="oq-read"') && h.includes('data-testid="oq-send"') && !h.includes('data-testid="oq-option"'), h);
    assert.ok(h.includes(lng === 'en' ? '>Read<' : '>已读<') && h.includes(lng === 'en' ? '>Reply<' : '>回复<'), h);
    for (const item of [decision, outside, catalog, message]) assert.ok(!/ownerQueue\./.test(decode(m.card(i18n, item))), item.kind);
  });
}

test('the counter text is formatted from the real locale files', async () => {
  for (const [lng, want] of [['en', '3 of 14'], ['zh-CN', '第 3 项，共 14 项']]) {
    const i18n = m.makeI18n(lng); await ready(i18n);
    assert.equal(i18n.t('ownerQueue.counter', { ns: 'common', n: 3, total: 14 }), want);
    assert.equal(i18n.t('ownerQueue.countTip', { ns: 'common', blocking: 4, messages: 9 }), lng === 'en' ? '4 decisions, 9 messages' : '4 项决策，9 条消息');
  }
});
