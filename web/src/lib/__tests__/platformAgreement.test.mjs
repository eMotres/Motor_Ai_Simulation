// node --test - the pure helpers of the Platform Agreement dialog and the 428 retry (lib/platformAgreement.ts).
// The module imports ./nda without an extension, so both files are transpiled and wired by hand (same pattern as apiAuthIdleTimeout.test.mjs).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';

const require = createRequire(import.meta.url);
const ts = require('typescript');
const compile = (file, requires = {}) => {
  const source = readFileSync(new URL(file, import.meta.url), 'utf8');
  const out = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(out, {
    module, exports: module.exports, Promise, Response, URL,
    require: (id) => {
      if (id in requires) return requires[id];
      throw new Error(`Unexpected import ${id}`);
    },
  });
  return module.exports;
};
const plain = (x) => JSON.parse(JSON.stringify(x));           // objects built inside the vm context have another prototype
const nda = compile('../nda.ts');
const A = compile('../platformAgreement.ts', { './nda': nda });

const known = { company: 'Acme Drones Ltd', reg_no: 'HRB 1', country: 'DE', address: 'Berlin' };
const goodForm = { ...A.emptyAgreementForm(), mode: 'FREE', signer: ' Ann  Example ', title: 'CTO', business: true, authority: true };

test('only the server 428 with its own code means "accept first"', () => {
  assert.equal(A.isAgreementRequired(428, { detail: { code: 'agreement_required', version: 'v1' } }), true);
  assert.equal(A.isAgreementRequired(428, { detail: { code: 'other' } }), false);
  assert.equal(A.isAgreementRequired(428, { detail: 'agreement_required' }), false);
  assert.equal(A.isAgreementRequired(403, { detail: { code: 'agreement_required' } }), false);
  assert.equal(A.isAgreementRequired(428, null), false);
});

test('the account menu offers the agreement only when it applies, admins always', () => {
  assert.equal(A.showsAgreement(null), false);
  assert.equal(A.showsAgreement({ enforced: false, required: false, accepted: false, exempt: null }), false);       // nothing said to users
  assert.equal(A.showsAgreement({ enforced: true, required: true, accepted: false, exempt: null }), true);
  assert.equal(A.showsAgreement({ enforced: true, required: false, accepted: false, exempt: 'staff' }), false);
  assert.equal(A.showsAgreement({ enforced: false, required: false, accepted: false, exempt: null, admin: { installed: false } }), true);
});

test('the quiet lines: acceptance parts and the admin company text', () => {
  assert.deepEqual(plain(A.agreementParts({ mode: 'FREE', version: 'v1.0', iso: '2026-10-27T09:30:00Z' })), { mode: 'FREE', version: 'v1.0', date: '2026-10-27' });
  assert.equal(A.companyAgreementText({ mode: 'PRIVATE', version: 'v1.0' }), 'PRIVATE · v1.0');
  assert.equal(A.companyAgreementText(null), '—');
  assert.equal(A.companyAgreementText(undefined), '—');
});

test('form errors: mode, signer, title, both boxes; company fields only where the server does not know them', () => {
  assert.deepEqual(Object.keys(A.agreementErrors(A.emptyAgreementForm(known), known, ['DE'])).sort(), ['authority', 'business', 'mode', 'signer', 'title']);
  assert.deepEqual(plain(A.agreementErrors({ ...goodForm, ...known }, known, ['DE'])), {});
  const unknown = A.agreementErrors({ ...goodForm }, {}, ['DE']);
  assert.deepEqual(Object.keys(unknown).sort(), ['address', 'company', 'country', 'reg_no']);
  assert.equal(A.agreementErrors({ ...goodForm, company: 'X', reg_no: '1', address: 'a', country: 'zz' }, {}, ['DE']).country, 'country');
  assert.deepEqual(plain(A.agreementErrors({ ...goodForm, company: 'X', reg_no: '1', address: 'a', country: ' de ' }, {}, ['DE'])), {});
  assert.equal(A.agreementErrors({ ...goodForm, mode: 'GOLD' }, known, ['DE']).mode, 'mode');
});

test('the accept body sends the typed fields and only the company data the server lacks', () => {
  const body = A.acceptBody({ ...goodForm, ...known }, known, 'v1', 'zh-CN');
  assert.deepEqual(plain(body), { mode: 'FREE', signer: 'Ann Example', title: 'CTO', business: true, authority: true, version: 'v1', lang: 'zh-CN' });
  const partial = A.acceptBody({ ...goodForm, company: ' Solo  GmbH ', reg_no: '9', country: 'at', address: 'Wien' }, { company: '' }, 'v1', 'en');
  assert.equal(partial.company, 'Solo GmbH');
  assert.equal(partial.country, 'at');
});

test('the text is rendered like the server: client data, mode, signer, date; unknown placeholders stay', () => {
  const text = '# T\n\n{{company}} ({{reg_no}}, {{country}}, {{address}}) mode {{mode}} by {{signatory}}, {{title}}, {{email}}, {{date}}, v{{version}}. {{later}}\n';
  const out = A.renderAgreement(text, { ...known, country: 'de' }, { mode: 'PRIVATE', signer: 'Ann  Example', title: 'CTO' }, 'a@x.de', '2026-10-27', '1');
  assert.equal(out, '# T\n\nAcme Drones Ltd (HRB 1, DE, Berlin) mode PRIVATE by Ann Example, CTO, a@x.de, 2026-10-27, v1. {{later}}\n');
  const blank = A.renderAgreement(text, { company: '', reg_no: '', country: '', address: '' }, { mode: '', signer: '', title: '' }, 'a@x.de', 'd', '1');
  assert.ok(blank.includes('______ (______, ______, ______) mode ______ by ______'));
});

const json = (status, body) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const gated = () => json(428, { detail: { code: 'agreement_required', version: 'v1' } });

test('a 428 opens the dialog and, after acceptance, repeats the call once', async () => {
  let asked = 0; let retried = 0;
  const res = await A.handleAgreementRequired(gated(), async () => { retried += 1; return json(200, { ok: true }); }, async (d) => { asked += 1; assert.equal(d.version, 'v1'); return true; });
  assert.equal(res.status, 200); assert.equal(asked, 1); assert.equal(retried, 1);
});

test('a cancelled dialog returns the 428 untouched and does not repeat the call', async () => {
  let retried = 0;
  const res = await A.handleAgreementRequired(gated(), async () => { retried += 1; return json(200, {}); }, async () => false);
  assert.equal(res.status, 428); assert.equal(retried, 0);
  assert.equal((await res.json()).detail.code, 'agreement_required');
});

test('other answers (200, 403, a 428 with another code) are never intercepted', async () => {
  const never = async () => { throw new Error('asked'); };
  for (const r of [json(200, {}), json(403, { detail: { code: 'agreement_required' } }), json(428, { detail: { code: 'precondition' } }), new Response('nope', { status: 428 })]) {
    assert.equal((await A.handleAgreementRequired(r, never, never)).status, r.status);
  }
});

test('without a mounted host nobody is asked; concurrent calls share one dialog', async () => {
  A.setAgreementHost(null);
  assert.equal(await A.requestAgreement({ version: 'v1' }), false);
  let opened = 0; let release;
  A.setAgreementHost(() => { opened += 1; return new Promise((resolve) => { release = resolve; }); });
  const a = A.requestAgreement({}); const b = A.requestAgreement({});
  release(true);
  assert.deepEqual(await Promise.all([a, b]), [true, true]);
  assert.equal(opened, 1);
  const c = A.requestAgreement({});                       // after it closed, a new gated call opens a new dialog
  release(false);
  assert.equal(await c, false);
  assert.equal(opened, 2);
  A.setAgreementHost(null);
});
