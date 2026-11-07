/** Legal pages, the sign-up box and the Platform Agreement at the start of a simulation: pure helpers of `legalLogic.ts` (routes, Markdown
 *  blocks, the sign-up box gating, the "updated terms" line, the agreement form) against the REAL texts in config/legal, plus the locale
 *  strings the boxes are built from. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  TERMS_VERSION, LEGAL_PAGES, PAGE_DOCS, matchLegalPage, isLegalPath, legalPath, resolveLegalUrl, safeHref, parseInline, inlineText,
  parseLegalMarkdown, displayText, headingId, inShort, signupPayload, signupLabelText, signupAllowed, showTermsHint, isTermsRequired,
  termsLineVisible, emptyAgreementForm, agreementComplete, agreementPayload, asksEarlierData, forumVariant, asksBusinessName, acceptLabelText,
} from '../legalLogic.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, '..', '..', '..', '..');
const LOCALES = join(HERE, '..', '..', 'locales');
const text = (sub, stem, lang) => readFileSync(join(ROOT, 'config', 'legal', sub, 'v0.4', `${stem}_${lang}.md`), 'utf8').replace('{{version}}', 'v0.4');
const common = (lng) => JSON.parse(readFileSync(join(LOCALES, lng, 'common.json'), 'utf8'));

test('the web version is the server version (a mismatch would make every sign-up a 422)', () => {
  const py = readFileSync(join(ROOT, 'src', 'motor_ai_sim', 'legal_texts.py'), 'utf8');
  assert.equal(/^TERMS_VERSION = "([^"]+)"/m.exec(py)[1], TERMS_VERSION);
});

test('routes: the three pages, with and without /zh-cn/ and a trailing slash; nothing else', () => {
  assert.deepEqual([...LEGAL_PAGES], ['terms', 'privacy', 'platform-agreement']);
  for (const [p, k] of [['/terms', 'terms'], ['/privacy/', 'privacy'], ['/platform-agreement', 'platform-agreement'],
    ['/zh-cn/terms', 'terms'], ['/zh-cn/privacy/', 'privacy'], ['/ZH-CN/platform-agreement', 'platform-agreement']]) {
    assert.equal(matchLegalPage(p), k, p);
    assert.equal(isLegalPath(p), true);
  }
  for (const p of ['/', '/terms-conditions', '/terms/x', '/zh-cn/', '/zh-cn', '/preferences', '/xterms', undefined, 5, null]) {
    assert.equal(matchLegalPage(p), null, String(p));
  }
  assert.deepEqual(PAGE_DOCS['platform-agreement'], ['platform-agreement', 'schedules']);
});

test('links: the in-app page, the /zh-cn/ prefix in Chinese, an env override wins', () => {
  assert.equal(legalPath('terms', 'en'), '/terms');
  assert.equal(legalPath('platform-agreement', 'zh-CN'), '/zh-cn/platform-agreement');
  assert.equal(matchLegalPage(legalPath('privacy', 'zh-CN')), 'privacy');
  assert.equal(resolveLegalUrl(undefined, 'privacy', 'en'), '/privacy');
  assert.equal(resolveLegalUrl('  ', 'privacy', 'zh-CN'), '/zh-cn/privacy');
  assert.equal(resolveLegalUrl(' https://example.com/p ', 'privacy', 'en'), 'https://example.com/p');
});

test('inline Markdown: bold, italic, links; unsafe links stay plain text', () => {
  const c = parseInline('see **bold** and *it* and [site](https://example.com/a) and [bad](javascript:alert(1)) end');
  assert.deepEqual(c.map((n) => n.t), ['text', 'strong', 'text', 'em', 'text', 'link', 'text']);
  assert.equal(c[5].href, 'https://example.com/a');
  assert.equal(inlineText(c), 'see bold and it and site and [bad](javascript:alert(1)) end');
  assert.equal(safeHref('data:text/html,x'), null);
  assert.equal(safeHref('/privacy'), '/privacy');
  assert.equal(safeHref('//evil.example/x'), null);
  assert.equal(safeHref('mailto:support@aerostator.com'), 'mailto:support@aerostator.com');
});

test('headings get stable anchors in both languages', () => {
  assert.equal(headingId('13. Governing law and disputes'), 's13');
  assert.equal(headingId('13. 适用法律与争议'), 's13');
  assert.equal(headingId('Schedule 2 — Service Providers and subprocessors'), 'schedule-2');
});

test('the real texts parse: every text has a title, sections, the privacy list, the schedules table', () => {
  for (const lang of ['en', 'zh-CN']) {
    const docs = {
      terms: text('terms_of_use', 'terms_of_use', lang), privacy: text('privacy', 'privacy', lang),
      agreement: text('platform_agreement_public', 'platform_agreement', lang), schedules: text('platform_agreement_public', 'schedules', lang),
    };
    for (const [name, md] of Object.entries(docs)) {
      const blocks = parseLegalMarkdown(displayText(md));
      assert.equal(blocks[0].t, 'h', `${lang} ${name} starts with a heading`);
      assert.equal(blocks[0].level, 1);
      assert.ok(blocks.filter((b) => b.t === 'h' && b.level === 2).length >= 3, `${lang} ${name} sections`);
      assert.ok(blocks.filter((b) => b.t === 'p').length >= 5);
    }
    const priv = parseLegalMarkdown(docs.privacy);
    assert.ok(priv.some((b) => b.t === 'ul' && b.items.length >= 3), `${lang} privacy has its bullet list`);
    const tables = parseLegalMarkdown(docs.schedules).filter((b) => b.t === 'table');
    assert.equal(tables.length, 1, `${lang} schedules: the one table`);
    assert.equal(tables[0].head.length, 6);
    assert.ok(tables[0].rows.length >= 8 && tables[0].rows.every((r) => r.length === 6));
  }
});

test('display transform: both section 13 variants shown, placeholders become blanks, nothing else changes', () => {
  const agr = text('platform_agreement_public', 'platform_agreement', 'en');
  assert.ok(agr.includes('{{#unless country_is_cn_hk}}') && agr.includes('{{company}}'));
  const shown = displayText(agr);
  assert.ok(!/\{\{/.test(shown));
  assert.ok(shown.includes('Variant A') && shown.includes('Variant B'));
  assert.ok(shown.includes('13.1 This Agreement is governed by Slovenian substantive law'));
  assert.ok(shown.includes('________ — ________'));
  assert.equal(displayText('a\n\n\n\nb'), 'a\n\nb');
});

test('"In short" of the Terms of Use is found in both languages', () => {
  const en = inShort(text('terms_of_use', 'terms_of_use', 'en'));
  assert.equal(en.label, 'In short:');
  assert.ok(en.body.startsWith('AeroStator is for businesses. Simulations are free.'));
  const zh = inShort(text('terms_of_use', 'terms_of_use', 'zh-CN'));
  assert.equal(zh.label, '简而言之：');
  assert.ok(zh.body.startsWith('AeroStator 面向企业。仿真免费。'));
  assert.equal(inShort('# T\n\nplain text only'), null);
});

test('the sign-up box: ONE unticked box, nothing that could pass for consent when unticked, the buttons wait for it', () => {
  assert.deepEqual(signupPayload(false, 'v0.4'), { accept_terms: false, terms_version: '' });                  // initially UNTICKED
  assert.deepEqual(signupPayload(true, 'v0.4'), { accept_terms: true, terms_version: 'v0.4' });
  assert.deepEqual(signupPayload(true, 'v0.4', 'I use AeroStator ...'), { accept_terms: true, terms_version: 'v0.4', accept_label: 'I use AeroStator ...' });
  assert.deepEqual(signupPayload(false, 'v0.4', 'label'), { accept_terms: false, terms_version: '' });          // no label without consent
  assert.equal(signupPayload('yes', 'v0.4').accept_terms, false);                                             // only a literal true is consent
  // no capacity, country or business name at sign-up
  assert.deepEqual(Object.keys(signupPayload(true, 'v0.4', 'x')).sort(), ['accept_label', 'accept_terms', 'terms_version']);
  assert.equal(signupAllowed({ creating: true, ticked: false }), false);
  assert.equal(signupAllowed({ creating: true, ticked: true }), true);
  assert.equal(signupAllowed({ creating: true, ticked: true, busy: true }), false);
  assert.equal(signupAllowed({ creating: false, ticked: false }), true);                                       // signing in never needs it
  assert.equal(showTermsHint({ creating: true, ticked: false }), true);
  assert.equal(showTermsHint({ creating: true, ticked: true }), false);
  assert.equal(showTermsHint({ creating: false, ticked: false }), false);
  assert.equal(isTermsRequired({ code: 'terms_required' }), true);
  for (const old of ['country_required', 'capacity_required', 'business_name_required']) assert.equal(isTermsRequired({ code: old }), false, old);
  assert.equal(isTermsRequired(new Error('x')), false);
  assert.equal(isTermsRequired(null), false);
});

test('the sign-up sentence and the "updated terms" line, EN and zh-CN, built from the locale pieces', () => {
  const en = common('en').legal;
  const zh = common('zh-CN').legal;
  const s = (L) => signupLabelText({ lead: L.signup.lead, terms: L.accept.terms, sep: L.signup.sep, privacy: L.accept.privacy, tail: L.signup.tail });
  assert.equal(s(en), 'I use AeroStator for business and accept the Terms of Use and the Privacy Policy.');
  assert.equal(s(zh), '我为商业目的使用 AeroStator，并接受《使用条款》和《隐私政策》。');
  const line = (L) => signupLabelText({ lead: L.banner.lead, terms: L.accept.terms, sep: L.banner.sep, privacy: L.accept.privacy, tail: L.banner.tail });
  assert.equal(line(en), 'We updated the Terms of Use and the Privacy Policy.');
  assert.equal(line(zh), '我们更新了《使用条款》和《隐私政策》。');
  assert.deepEqual([en.banner.accept, en.banner.dismiss, zh.banner.accept, zh.banner.dismiss], ['Accept', 'Dismiss', '接受', '关闭']);
});

test('existing accounts: ONE dismissible line when the current Terms are missing, hidden for the session once dismissed, never blocking', () => {
  const need = { version: 'v0.4', required: true };
  assert.equal(termsLineVisible(need, 'a@x.com', null), true);
  assert.equal(termsLineVisible(need, 'a@x.com', 'a@x.com'), false);          // "Dismiss" in this browser session
  assert.equal(termsLineVisible(need, 'A@X.com', 'a@x.com'), false);          // the same account, any case
  assert.equal(termsLineVisible(need, 'b@x.com', 'a@x.com'), true);           // another account in the same tab is still told
  assert.equal(termsLineVisible({ version: 'v0.4', required: false }, 'a@x.com', null), false);
  assert.equal(termsLineVisible(null, 'a@x.com', null), false);               // a failed status call shows nothing
  assert.equal(termsLineVisible(undefined, '', null), false);
  assert.equal(termsLineVisible(need, '', null), true);
});

test('the agreement form: nothing preselected, the mode / capacity / country / box all required, the name for a business', () => {
  const blank = emptyAgreementForm();
  assert.deepEqual(blank, { mode: '', capacity: '', country: '', business: '', ticked: false, earlier: false });   // no default mode, both boxes UNTICKED
  const f = (o) => ({ mode: 'FREE', capacity: 'sole_trader', country: 'DE', business: '', ticked: true, earlier: false, ...o });
  assert.equal(agreementComplete(blank), false);
  assert.equal(agreementComplete(f({})), true);
  assert.equal(agreementComplete(f({ mode: '' })), false);                   // the mode must be chosen explicitly
  assert.equal(agreementComplete(f({ mode: 'TRIAL' })), false);
  assert.equal(agreementComplete(f({ mode: 'PRIVATE' })), true);
  assert.equal(agreementComplete(f({ capacity: '' })), false);
  assert.equal(agreementComplete(f({ capacity: 'owner' })), false);
  assert.equal(agreementComplete(f({ country: '  ' })), false);
  assert.equal(agreementComplete(f({ ticked: false })), false);
  assert.equal(agreementComplete(f({ ticked: 'yes' })), false);
  // a business must be named, a sole trader may, "neither" has no name
  assert.equal(agreementComplete(f({ capacity: 'business' })), false);
  assert.equal(agreementComplete(f({ capacity: 'business', business: '  ' })), false);
  assert.equal(agreementComplete(f({ capacity: 'business', business: 'Motor GmbH' })), true);
  assert.equal(agreementComplete(f({ capacity: 'other' })), true);
  assert.deepEqual(['business', 'sole_trader', 'other', ''].map(asksBusinessName), [true, true, false, false]);
  // the forum of agreement section 13: B for Mainland China and Hong Kong, A for the rest
  assert.deepEqual(['CN', 'hk', ' HK ', 'SI', 'DE', 'MO', 'TW', ''].map(forumVariant), ['B', 'B', 'B', 'A', 'A', 'A', 'A', 'A']);
});

test('the earlier-data box is a FREE election: never asked, sent or labelled for PRIVATE', () => {
  const f = { mode: 'PRIVATE', capacity: 'business', country: 'DE', business: 'Motor GmbH', ticked: true, earlier: true };
  const out = agreementPayload(f, 'v0.4', 'en', { accept: 'A', election: 'E' });
  assert.equal(out.historical_opt_in, false);
  assert.equal(out.election_label, undefined);
  assert.equal(out.accept_label, 'A');
  const free = agreementPayload({ ...f, mode: 'FREE' }, 'v0.4', 'en', { accept: 'A', election: 'E' });
  assert.equal(free.historical_opt_in, true);
  assert.equal(free.election_label, 'E');
  assert.equal(asksEarlierData('PRIVATE'), false);
  assert.equal(asksEarlierData('FREE'), true);
  assert.equal(asksEarlierData(''), true);
  const dlg = readFileSync(new URL('../../components/agreement/SimulationAgreementDialog.tsx', import.meta.url), 'utf8');
  assert.match(dlg, /\{asksEarlierData\(mode\) && \(/);
  assert.match(dlg, /earlier: asksEarlierData\(next\) \? form\.earlier : false/);
});

test('the agreement request: country upper-cased, the name of "neither" never sent, the earlier-data election and labels as shown', () => {
  const f = { mode: 'FREE', capacity: 'business', country: ' de ', business: '  Motor GmbH ', ticked: true, earlier: false };
  assert.deepEqual(agreementPayload(f, 'v0.4', 'en'), {
    accept_agreement: true, agreement_version: 'v0.4', mode: 'FREE', capacity: 'business', country: 'DE', business_name: 'Motor GmbH',
    historical_opt_in: false, lang: 'en',
  });
  assert.equal(agreementPayload({ ...f, capacity: 'other' }, 'v0.4', 'en').business_name, '');
  assert.equal(agreementPayload({ ...f, capacity: 'sole_trader', business: ' Solo ' }, 'v0.4', 'en').business_name, 'Solo');
  const pr = agreementPayload({ ...f, mode: 'PRIVATE', earlier: true }, 'v0.4', 'zh-CN', { accept: 'A', election: 'E' });
  assert.equal(pr.mode, 'PRIVATE'); assert.equal(pr.historical_opt_in, false); assert.equal(pr.lang, 'zh-CN');
  assert.equal(pr.accept_label, 'A'); assert.equal(pr.election_label, undefined);
  const none = agreementPayload({ ...f, ticked: 'yes' }, 'v0.4', 'en');                                       // only a literal true is consent
  assert.equal(none.accept_agreement, false); assert.equal(none.agreement_version, '');
  assert.equal(emptyAgreementForm().earlier, false);
});

test('the agreement box sentence: R5 wording per capacity for FREE, the PRIVATE promise for PRIVATE, EN and zh-CN', () => {
  const sentence = (a, cap, mode = 'FREE') => acceptLabelText(a, cap, mode);
  const en = common('en').legal.accept;
  assert.equal(sentence(en, 'business'),
    'I accept the Terms of Use and the Platform Agreement (FREE mode), including the Schedules and the dispute clause shown, for the business '
    + 'named above, which I am authorised to bind. In exchange for free simulation, MOTRES may use the engineering data I supply from now on '
    + 'to train its own internal AI models; no payment or report is owed, and lawfully trained models are kept, subject to confidentiality and '
    + 'law. Training on my FREE data cannot be undone, even if I later order the motor, sign a paid agreement or switch to PRIVATE. '
    + 'Personal data is not used. I have read the Privacy Policy.');
  assert.ok(sentence(en, 'sole_trader').includes('for myself as a registered sole trader or self-employed professional.'));
  assert.equal(sentence(en, 'other'),
    'I work for a business without authority to bind it; I accept the Terms of Use and the Platform Agreement for my own use, and my data are '
    + 'used for training only where I confirm that I may license them. Training on my FREE data cannot be undone, even if I later order the '
    + 'motor, sign a paid agreement or switch to PRIVATE. I have read the Privacy Policy.');
  assert.equal(sentence(en, 'business', 'PRIVATE'),
    'I accept the Terms of Use and the Platform Agreement (PRIVATE mode requested), including the Schedules and the dispute clause shown. '
    + "I ask for PRIVATE mode: my engineering data are not used to train MOTRES's AI models, and our team will contact me about the fee. "
    + 'I have read the Privacy Policy.');
  const zh = common('zh-CN').legal.accept;
  for (const cap of ['business', 'sole_trader', 'other']) {
    assert.ok(sentence(zh, cap).includes('我在免费模式下的数据一经用于训练即不可撤销，即使我此后订购该电机、订立付费协议或切换至私有模式亦然。'), cap);
    assert.ok(sentence(en, cap).includes('Training on my FREE data cannot be undone, even if I later order the motor, sign a paid agreement or switch to PRIVATE.'), cap);
    assert.ok(!sentence(en, cap, 'PRIVATE').includes('cannot be undone') && sentence(en, cap, 'PRIVATE').includes('not used to train'), cap);   // PRIVATE: no FREE exchange
  }
  assert.ok(sentence(zh, 'business').startsWith('我接受《使用条款》及《平台协议》（免费模式）') && sentence(zh, 'business').endsWith('我已阅读《隐私政策》。'));
  assert.ok(sentence(zh, 'business', 'PRIVATE').startsWith('我接受《使用条款》及《平台协议》（私有模式申请）') && sentence(zh, 'business', 'PRIVATE').endsWith('我已阅读《隐私政策》。'));
  assert.equal(en.capacity.business, 'For a business I am authorised to bind');
  assert.equal(en.capacity.sole_trader, 'For myself as a registered sole trader or self-employed professional');
  assert.equal(en.capacity.other, 'Neither (for example I work for an employer or customer without authority to bind it)');
  assert.equal(en.earlier, 'Also use the engineering data my account entered before today');
  assert.equal(zh.earlier, '同时使用我的账户在今天之前输入的工程数据');
  assert.equal(en.country, 'Country where your business is established');
  // the label kept with the record is the sentence beside the box, in the language shown
  const label = acceptLabelText(en, 'sole_trader');
  assert.ok(label.startsWith('I accept the Terms of Use and the Platform Agreement (FREE mode)') && label.endsWith('Privacy Policy.'));
});

test('the simulation-start dialog is short: five lines, then the fields; the mode has no default and PRIVATE says the team will contact', () => {
  const en = common('en').legal.agreement;
  const zh = common('zh-CN').legal.agreement;
  for (const k of ['point1', 'point2', 'point3', 'point4', 'point5']) {
    assert.ok(en[k].length < 130, `${k} is a short line`);
    assert.ok(zh[k].length < 70, `${k} (zh) is a short line`);
  }
  assert.equal(en.modeFree.startsWith('FREE'), true);
  assert.equal(en.modePrivate, 'Ask for PRIVATE (paid): no AI training; our team will contact me.');
  assert.equal(en.privateNote, 'Our team will contact you about PRIVATE. Until then your data are not used for AI training.');
  const src = readFileSync(join(HERE, '..', '..', 'components', 'agreement', 'SimulationAgreementDialog.tsx'), 'utf8');
  assert.match(src, /useState<AgreementForm>\(emptyAgreementForm\(\)\)/);                       // nothing preselected
  assert.ok(src.indexOf('legal.agreement.point1') < src.indexOf('legal.agreement.modeLabel') && src.indexOf('legal.agreement.modeLabel') < src.indexOf('legal.accept.capacity.label')
    && src.indexOf('legal.accept.capacity.label') < src.indexOf('legal.accept.country') && src.indexOf('legal.accept.country') < src.indexOf('legal.accept.business')
    && src.indexOf('legal.accept.business') < src.indexOf('<Checkbox'));                           // points, mode, capacity, country, name, then the boxes
  assert.match(src, /<FormControl component="fieldset" required[^>]*data-testid="sim-agreement-mode"/);
  assert.match(src, /<FormControl component="fieldset" required[^>]*data-testid="terms-capacity"/);
  assert.match(src, /<TextField select[^>]*required/);                                               // the country is required
  assert.match(src, /required=\{cap === 'business'\}/);                                              // the business name is required for a business only
  assert.match(src, /mode === 'PRIVATE' && \(/);                                                      // the PRIVATE note appears once PRIVATE is chosen
  assert.match(src, /legal\.agreement\.privateDone/);
  assert.match(src, /agreementUrl\(lang\)[\s\S]*termsUrl\(lang\)[\s\S]*privacyUrl\(lang\)/);          // links to the three full texts
});

test('every assistant says it is one: the same line in the help chat, the specification assistant and the design assistant', () => {
  assert.equal(common('en').legal.aiDisclosure, 'AI assistant \u2014 answers can be wrong.');
  assert.equal(common('zh-CN').legal.aiDisclosure, '人工智能助手——回答可能有误。');
  const read = (...p) => readFileSync(join(HERE, '..', '..', ...p), 'utf8');
  for (const f of [['components', 'support', 'SupportWidget.tsx'], ['modules', 'development', 'components', 'SpecAssistantPanel.tsx'], ['modules', 'designer', 'DesignerTab.tsx']]) {
    const src = read(...f);
    assert.match(src, /tc\('legal\.aiDisclosure'\)/, f.join('/'));
    assert.match(src, /data-testid="ai-disclosure"/);
  }
  // the Gemini line stays a generic switch on the provider kind the server reports; no provider is chosen here
  assert.match(read('components', 'support', 'SupportWidget.tsx'), /supportProvider === 'gemini'/);
});

test('the new Chinese strings contain no Latin words except the brand names', () => {
  const allowed = new Set(['AeroStator', 'MOTRES', 'Google', 'Gemini']);
  const flat = (o, p = '') => Object.entries(o).flatMap(([k, v]) => (v && typeof v === 'object' ? flat(v, `${p}${k}.`) : [[`${p}${k}`, v]]));
  const strings = [
    ...flat(common('zh-CN').legal, 'common.legal.'),
    ...['terms', 'agreement', 'privacy'].map((k) => [`landing.footer.${k}`, JSON.parse(readFileSync(join(LOCALES, 'zh-CN', 'landing.json'), 'utf8')).footer[k]]),
    ['support.geminiNotice', JSON.parse(readFileSync(join(LOCALES, 'zh-CN', 'support.json'), 'utf8')).geminiNotice],
    ['errors.terms_required', JSON.parse(readFileSync(join(LOCALES, 'zh-CN', 'errors.json'), 'utf8')).terms_required],
  ];
  assert.ok(strings.length >= 20);
  for (const [k, v] of strings) {
    // FREE / PRIVATE stay Latin only where the existing agreement dialog uses them: the mode choice of the simulation-start dialog
    const ok = k.startsWith('common.legal.agreement.') ? new Set([...allowed, 'FREE', 'PRIVATE']) : allowed;
    const stray = (v.replace(/\{\w+\}/g, '').match(/[A-Za-z][A-Za-z0-9_]*/g) ?? []).filter((w) => !ok.has(w));
    assert.deepEqual(stray, [], `${k}: Latin words in the zh string`);
  }
});

test('EN and zh-CN carry the same legal keys; the old passive terms line is gone', () => {
  const keys = (o, p = '') => Object.entries(o).flatMap(([k, v]) => (v && typeof v === 'object' ? keys(v, `${p}${k}.`) : [`${p}${k}`])).sort();
  assert.deepEqual(keys(common('zh-CN').legal), keys(common('en').legal));
  for (const lng of ['en', 'zh-CN']) {
    const terms = common(lng).auth.terms;
    assert.deepEqual(Object.keys(terms).sort(), ['license', 'source']);
  }
  const en = JSON.parse(readFileSync(join(LOCALES, 'en', 'support.json'), 'utf8')).geminiNotice;
  assert.equal(en, 'Runs on Google Gemini. Google may use help-chat messages to improve its services. Do not enter confidential information.');
  assert.equal(JSON.parse(readFileSync(join(LOCALES, 'zh-CN', 'support.json'), 'utf8')).geminiNotice,
    '由 Google Gemini 提供。Google 可能使用帮助对话内容改进其服务。请勿输入保密信息。');
});

test('the sign-in dialog, the OAuth consent sign-up and the shared surfaces use the ONE box and the in-app links', () => {
  const read = (...p) => readFileSync(join(HERE, '..', '..', ...p), 'utf8');
  const login = read('components', 'auth', 'LoginDialog.tsx');
  assert.match(login, /<TermsBox /);
  assert.match(login, /signupPayload\(ticked, TERMS_VERSION, signupLabel\(t\)\)/);                          // the e-mail form (also the OAuth consent page sign-up: returnTo)
  assert.match(login, /signupPayload\(tickedRef\.current, TERMS_VERSION, signupLabel\(tRef\.current\)\)/);   // Google
  assert.doesNotMatch(login.replace(/^\s*\/\/.*$/gm, ''), /capacity|business_name|country_required/);
  const box = read('components', 'common', 'TermsBox.tsx');
  assert.match(box, /<Checkbox[^>]*required/);                                                         // the box is required
  assert.match(box, /legal\.signup\.lead[\s\S]*legal\.accept\.terms[\s\S]*legal\.signup\.sep[\s\S]*legal\.accept\.privacy[\s\S]*legal\.signup\.tail/);
  assert.doesNotMatch(box.replace(/^\/\/.*$/gm, ''), /capacity|country|Radio|TextField/);              // nothing but the box
  assert.match(login, /disabled=\{busy \|\| boxHint\}/);
  assert.doesNotMatch(login, /TERMS_URL|PRIVACY_URL|emotres\.com/);
  const consent = read('components', 'auth', 'OAuthConsent.tsx');
  assert.match(consent, /signIn\(\{ mode: 'register', returnTo \}\)/);        // the sign-up of the consent page IS the LoginDialog
  assert.doesNotMatch(read('lib', 'localAuth.ts'), /emotres\.com\/(terms|privacy)/);
  assert.match(read('Root.tsx'), /matchLegalPage\(path\)/);
  assert.match(read('Root.tsx'), /<TermsUpdateLine \/>/);
  assert.doesNotMatch(read('Root.tsx'), /TermsUpdateHost/);
  const line = read('components', 'auth', 'TermsUpdateLine.tsx');                                    // a snackbar line, not a dialog
  assert.match(line, /<Snackbar open/);
  assert.doesNotMatch(line, /<Dialog/);
  assert.match(line, /legal\.banner\.accept[\s\S]*legal\.banner\.dismiss/);
  assert.match(read('components', 'landing', 'Landing.tsx'), /termsUrl\(legalLang\)[\s\S]*agreementUrl\(legalLang\)[\s\S]*privacyUrl\(legalLang\)/);
  assert.match(read('components', 'common', 'AnalyticsConsent.tsx'), /privacyUrl\(/);
  assert.doesNotMatch(read('components', 'common', 'AnalyticsConsent.tsx'), /https:\/\/emotres\.com/);
});
