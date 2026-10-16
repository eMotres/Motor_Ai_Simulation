// node --test - the pure helpers of the online NDA dialog (lib/nda.ts): live rendering with the CN/HK blocks, the form
// checks, the Markdown blocks.  Imports the .ts itself (Node >= 22.18 / 24; else skipped).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

let L = null;
try { L = await import('../nda.ts'); } catch { /* old Node */ }
const t = L ? test : test.skip;

const form = { company: ' Acme   Drones Ltd ', reg_no: 'HRB 1', country: 'de', address: 'Berlin', signatory: 'Erika  Mustermann', title: 'CTO' };
const TEXT = 'A {{#if country_is_cn_hk}}CN{{/if}}{{#unless country_is_cn_hk}}SI{{/unless}} B {{company}} {{country}} {{email}} {{date}} {{version}} {{nope}}';

t('the blocks show only the selected variant, placeholders are filled, unknown ones stay', () => {
  const si = L.renderNda(TEXT, L.values(form, 'e@x.de', '2026-10-13', 'v1'));
  assert.equal(si.trim(), 'A SI B Acme Drones Ltd DE e@x.de 2026-10-13 v1 {{nope}}');
  for (const c of ['CN', 'hk', 'Hk']) {
    assert.match(L.renderNda(TEXT, L.values({ ...form, country: c }, 'e@x.de', 'd', 'v')), /^A CN B /);
  }
  assert.equal(L.renderNda('x {{#if nope}}a\n\n\n\nb{{/if}}\n\n\n\ny {{unknown}}', L.values(form, 'e', 'd', 'v')), 'x \n\ny {{unknown}}\n');
});

t('the dialog agreement sections are always English followed by Simplified Chinese', () => {
  const sections = L.renderNdaLanguages({ en: 'English {{company}}', 'zh-CN': '中文 {{company}}' },
    L.values({ ...form, country: 'DE' }, 'e@x.de', '2026-10-13', 'v1'));
  assert.deepEqual(sections.map((section) => section.lang), ['en', 'zh-CN']);
  assert.deepEqual(sections.map((section) => section.text), ['English Acme Drones Ltd\n', '中文 Acme Drones Ltd\n']);
});

t('a blank form shows underscores so the preview reads, the flag follows the country', () => {
  const v = L.values(L.emptyForm(), 'e@x.de', 'd', 'v', '______');
  assert.equal(v.company, '______');
  assert.equal(v.country, '______');
  assert.equal(v.country_is_cn_hk, false);
  assert.equal(L.isCnHk(' cn '), true);
  assert.equal(L.isCnHk('SI'), false);
});

t('the drafted agreement renders both variants cleanly, same rule as the server (fixture shared with the python test)', () => {
  const text = readFileSync(new URL('../../../../tests/fixtures/nda/model_nda_en.md', import.meta.url), 'utf8').replace(/\r\n/g, '\n');
  const cn = L.renderNda(text, L.values({ ...form, country: 'CN' }, 'e@x.de', '2026-10-13', 'v3'));
  const si = L.renderNda(text, L.values({ ...form, country: 'SI' }, 'e@x.de', '2026-10-13', 'v3'));
  for (const r of [cn, si]) { assert.ok(!r.includes('{{')); assert.ok(!/\n{3,}/.test(r)); assert.ok(r.includes('Acme Drones Ltd')); }
  assert.ok(cn.includes('arbitration') && !cn.includes('Convention'));
  assert.ok(si.includes('Convention') && !si.includes('arbitration'));
  // hash of the exact text shown: stable and different per variant
  const h = (s) => createHash('sha256').update(s, 'utf8').digest('hex');
  assert.notEqual(h(cn), h(si));
  assert.equal(h(si), h(L.renderNda(text, L.values({ ...form, country: 'SI' }, 'e@x.de', '2026-10-13', 'v3'))));
});

t('form errors: every field required, listed country, typed name equals the signatory name, box ticked', () => {
  const C = ['DE', 'CN', 'HK'];
  assert.deepEqual(Object.keys(L.formErrors(L.emptyForm(), C, '', false)).sort(), ['accept', 'address', 'company', 'country', 'reg_no', 'signatory', 'title', 'typed_name']);
  assert.deepEqual(L.formErrors(form, C, 'erika mustermann', true), {});
  assert.deepEqual(L.formErrors(form, C, 'Somebody', true), { typed_name: 'name' });
  assert.deepEqual(L.formErrors({ ...form, country: 'ZZ' }, C, 'Erika Mustermann', true), { country: 'country' });
  assert.deepEqual(L.formErrors(form, C, 'Erika Mustermann', false), { accept: 'accept' });
});

t('markdown blocks: headings, paragraphs, bold and italic runs, never HTML', () => {
  const b = L.mdBlocks('# Title\n\n**1. Definitions**\n\nPlain **bold** and *italic* <b>x</b>.');
  assert.equal(b[0].kind, 'h1');
  assert.deepEqual(b[1].runs, [{ text: '1. Definitions', bold: true }]);
  assert.deepEqual(b[2].runs, [{ text: 'Plain ' }, { text: 'bold', bold: true }, { text: ' and ' }, { text: 'italic', italic: true }, { text: ' <b>x</b>.' }]);
});

t('utcToday is the UTC date', () => {
  assert.equal(L.utcToday(new Date('2026-10-13T23:59:59Z')), '2026-10-13');
});

t('the client render hashes to the SAME value as the python renderer (cross-language lock, both languages, both variants)', () => {
  const f = { company: 'Acme Drones Ltd', reg_no: 'HRB 1', country: 'CN', address: 'Berlin', signatory: 'Erika Mustermann', title: 'CTO' };
  const h = (s) => createHash('sha256').update(s, 'utf8').digest('hex');
  const expect = {
    'en CN': 'fd174cb610c0f1fb2dc51e78eacfbb507ca626b581026c969ab8b7bcfd4b43c5',
    'en SI': '63cd700989536b990490cbdf5cbd915955fd3fd854f0f236ada4b4c965b66975',
    'zh-CN CN': 'e6340750fc9010b87d184cb27ae9bb6f6affa0eb33e6d77f70c36fd1ac71a44c',
    'zh-CN SI': '4c9dd2e214542aa4c6df1fe4d75618a76c78c95e16077e4961733a61572cd182',
  };
  for (const [key, want] of Object.entries(expect)) {
    const [lang, country] = key.split(' ');
    const text = readFileSync(new URL(`../../../../tests/fixtures/nda/model_nda_${lang}.md`, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
    assert.equal(h(L.renderNda(text, L.values({ ...f, country }, 'e@x.de', '2026-10-13', 'v3'))), want, key);
  }
});
