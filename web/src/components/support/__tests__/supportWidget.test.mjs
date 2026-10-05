// node --test — the help widget's contract, checked on its SOURCE and its strings
// (the repo's node tests cannot render React; the behaviour itself is in
// lib/supportFlow.ts, tested in lib/__tests__/supportFlow.test.mjs).
//
// Owner decisions 2026-10-05 pinned here:
//   * no separate Report form or tab: only Ask and My tickets;
//   * the draft card has Send, Dismiss and editable type / title / description;
//   * answers are rendered through the markdown renderer, never as raw HTML
//     (no dangerouslySetInnerHTML in the support components);
//   * UI strings: EN and a FULL ZH mirror, no mixed languages, none hard-coded in
//     the components, and no small print (no font size under 12 px);
//   * the widget sends the session context with chat and ticket and installs the
//     failed-call recorder.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = join(HERE, '..', '..', '..');
const read = (...p) => readFileSync(join(SRC, ...p), 'utf8');
const widget = read('components', 'support', 'SupportWidget.tsx');
const card = read('components', 'support', 'TicketDraftCard.tsx');
const md = read('components', 'support', 'SupportMarkdown.tsx');
const en = JSON.parse(read('locales', 'en', 'support.json'));
const zh = JSON.parse(read('locales', 'zh-CN', 'support.json'));

const flat = (o, p = '') => Object.entries(o).flatMap(([k, v]) =>
  v && typeof v === 'object' ? flat(v, p ? `${p}.${k}` : k) : [[p ? `${p}.${k}` : k, v]]);
const CJK = /[㐀-鿿]/;
const LATIN_WORD = /[A-Za-z]{3,}/g;

test('there is no Report form or tab: only Ask and My tickets', () => {
  assert.ok(!/value="report"/.test(widget) && !/'report'/.test(widget));
  assert.ok(!/Report/.test(widget + card), 'no "Report" anywhere in the widget');
  assert.ok(!/submitTicket\(\{\s*type: rtype/.test(widget), 'the manual form is gone');
  const tabs = [...widget.matchAll(/<Tab label=\{t\('([^']+)'\)\} value="([^"]+)"/g)].map((m) => m[2]);
  assert.deepEqual(tabs, ['ask', 'tickets']);
  assert.match(widget, /\{user && \(\s*<Tabs/, 'a visitor sees no tab bar');
});

test('the draft card: editable type, title and description; Send and Dismiss', () => {
  for (const key of ['draft.send', 'draft.dismiss', 'draft.titleLabel', 'draft.descriptionLabel', 'draft.typeLabel', 'draft.attached']) {
    assert.ok(card.includes(`t('${key}'`), key);
  }
  assert.match(card, /onEdit\(\{ type: v \}\)/);
  assert.match(card, /onEdit\(\{ title: e\.target\.value \}\)/);
  assert.match(card, /onEdit\(\{ description: e\.target\.value \}\)/);
  assert.match(card, /onClick=\{onSend\}/);
  assert.match(card, /onClick=\{onDismiss\}/);
  assert.match(card, /disabled=\{!signedIn \|\| !canSend\(flow\)\}/, 'Send needs a title and a signed-in user');
  for (const k of ['bug', 'feature', 'question', 'account']) assert.ok(en.type[k] && zh.type[k], k);
});

test('Send goes through the confirm flow with the context; nothing is filed from the chat call', () => {
  assert.match(widget, /runConfirm\(flowRef\.current, submitTicket, context\(\), setFlow\)/);
  assert.match(widget, /askAssistant\(next\.msgs, user \? context\(\) : undefined\)/);
  assert.match(widget, /installFailedCallRecorder\(/);
  assert.ok(!/submitTicket\(/.test(widget.replace('runConfirm(flowRef.current, submitTicket', '')), 'submitTicket is only reachable through runConfirm');
});

test('assistant answers are rendered as sanitised markdown, user text as plain text', () => {
  assert.match(widget, /<SupportMarkdown text=\{m\.content\} \/>/);
  const code = (src) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');   // comments may talk about it
  for (const [name, src] of [['widget', widget], ['card', card], ['markdown', md]]) {
    assert.ok(!/dangerouslySetInnerHTML|innerHTML|insertAdjacentHTML|document\.write/.test(code(src)), `${name}: raw HTML sink`);
  }
  assert.match(md, /rel="noopener noreferrer"/);
  assert.match(md, /from '\.\.\/\.\.\/lib\/supportMarkdown'/);
});

test('every t() key the components use exists in EN and in ZH', () => {
  const keys = new Set([...(widget + card).matchAll(/\bt\('([A-Za-z0-9_.]+)'/g)].map((m) => m[1]));
  const enMap = new Map(flat(en));
  const zhMap = new Map(flat(zh));
  for (const k of keys) {
    assert.ok(enMap.has(k), `EN missing ${k}`);
    assert.ok(zhMap.has(k), `ZH missing ${k}`);
  }
  // template keys (type.<k>, status.<k>)
  for (const k of ['bug', 'feature', 'question', 'account']) assert.ok(enMap.has(`type.${k}`) && zhMap.has(`type.${k}`));
  for (const k of ['open', 'in_progress', 'resolved', 'closed']) assert.ok(enMap.has(`status.${k}`) && zhMap.has(`status.${k}`));
});

test('ZH is a full mirror: same keys, same placeholders, Chinese text, no mixed languages', () => {
  const enMap = new Map(flat(en));
  const zhMap = new Map(flat(zh));
  assert.deepEqual([...zhMap.keys()].sort(), [...enMap.keys()].sort(), 'identical key sets');
  const ph = (s) => [...String(s).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
  const allowedLatin = new Set(['vadim', 'motresres', 'com', 'AeroStator']);
  for (const [k, v] of zhMap) {
    assert.deepEqual(ph(v), ph(enMap.get(k)), `${k}: placeholders`);
    assert.ok(CJK.test(v), `${k}: the ZH string has no Chinese`);
    const latin = (v.replace(/\{\w+\}/g, '').match(LATIN_WORD) ?? []).filter((w) => !allowedLatin.has(w));
    assert.deepEqual(latin, [], `${k}: English words inside the ZH string`);
  }
  for (const [k, v] of enMap) assert.ok(!CJK.test(v), `${k}: Chinese inside the EN string`);
});

test('no user-visible English is hard-coded in the components, and no small print', () => {
  for (const [name, src] of [['widget', widget], ['card', card]]) {
    const jsxText = [...src.matchAll(/>\s*([A-Za-z][A-Za-z ,.'’!?-]{3,})\s*</g)].map((m) => m[1]);
    assert.deepEqual(jsxText, [], `${name}: JSX text that is not translated`);
    const attrs = [...src.matchAll(/(?:label|placeholder|title|aria-label)="([^"{]+)"/g)].map((m) => m[1]);
    assert.deepEqual(attrs, [], `${name}: attribute text that is not translated`);
    for (const m of src.matchAll(/fontSize:\s*([0-9.]+)/g)) {
      assert.ok(Number(m[1]) >= 12, `${name}: font size ${m[1]} is small print`);
    }
  }
});

test('the namespace is registered, so the lazy loader finds support.json', () => {
  const core = read('i18n', 'core.ts');
  assert.match(core, /'support'\] as const/);
});
