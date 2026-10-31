/** Legal pages and the sign-up acceptance: pure helpers of `legalLogic.ts` (routes, Markdown blocks, the box gating, the existing-user
 *  dialog) against the REAL texts in config/legal, plus the locale strings the box is built from. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  TERMS_VERSION, LEGAL_PAGES, PAGE_DOCS, matchLegalPage, isLegalPath, legalPath, resolveLegalUrl, safeHref, parseInline, inlineText,
  parseLegalMarkdown, displayText, headingId, inShort, acceptancePayload, signupAllowed, showTermsHint, isTermsRequired, shouldAskTerms,
  emptyAcceptance, acceptanceComplete, forumVariant, asksBusinessName, acceptLabelText, termsDeadline,
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

test('"In short" of the Terms of Use is found in both languages (the existing-user dialog quotes it)', () => {
  const en = inShort(text('terms_of_use', 'terms_of_use', 'en'));
  assert.equal(en.label, 'In short:');
  assert.ok(en.body.startsWith('AeroStator is for businesses. Simulations are free.'));
  const zh = inShort(text('terms_of_use', 'terms_of_use', 'zh-CN'));
  assert.equal(zh.label, '简而言之：');
  assert.ok(zh.body.startsWith('AeroStator 面向企业。仿真免费。'));
  assert.equal(inShort('# T\n\nplain text only'), null);
});

test('the block: country + box carried together, nothing that could pass for consent when incomplete, the buttons wait for it', () => {
  const blank = emptyAcceptance();
  assert.deepEqual(blank, { capacity: '', country: '', business: '', ticked: false, earlier: false });   // initially UNTICKED, no capacity, no country
  assert.deepEqual(acceptancePayload(blank, 'v0.4'), { accept_terms: false, terms_version: '', country: '', business_name: '', capacity: '' });
  const full = { capacity: 'business', country: ' de ', business: '  Motor GmbH ', ticked: true, earlier: false };
  assert.deepEqual(acceptancePayload(full, 'v0.4'), { accept_terms: true, terms_version: 'v0.4', country: 'DE', business_name: 'Motor GmbH', capacity: 'business' });
  assert.equal(acceptancePayload({ ...full, ticked: 'yes' }, 'v0.4').accept_terms, false);       // only a literal true is consent
  // creating an account: off until a capacity and the country are chosen AND the box is ticked (the business name is required for a business only)
  const only = (o) => ({ ...blank, capacity: 'sole_trader', ...o });
  assert.equal(acceptanceComplete(only({ ticked: true })), false);
  assert.equal(acceptanceComplete(only({ country: 'DE' })), false);
  assert.equal(acceptanceComplete(only({ country: 'DE', ticked: true })), true);
  assert.equal(acceptanceComplete(only({ country: 'DE', ticked: true, capacity: '' })), false);
  assert.equal(signupAllowed({ creating: true, form: blank }), false);
  assert.equal(signupAllowed({ creating: true, form: only({ ticked: true }) }), false);
  assert.equal(signupAllowed({ creating: true, form: only({ country: 'SI', ticked: true }) }), true);
  assert.equal(signupAllowed({ creating: true, form: only({ country: 'SI', ticked: true }), busy: true }), false);
  // signing in to an existing account never needs it
  assert.equal(signupAllowed({ creating: false, form: blank }), true);
  assert.equal(showTermsHint({ creating: true, form: blank }), true);
  assert.equal(showTermsHint({ creating: true, form: only({ country: 'SI', ticked: true }) }), false);
  assert.equal(showTermsHint({ creating: false, form: blank }), false);
  // the forum of agreement section 13: B for Mainland China and Hong Kong, A for the rest
  assert.deepEqual(['CN', 'hk', ' HK ', 'SI', 'DE', 'MO', 'TW', ''].map(forumVariant), ['B', 'B', 'B', 'A', 'A', 'A', 'A', 'A']);
  assert.equal(isTermsRequired({ code: 'terms_required' }), true);
  assert.equal(isTermsRequired({ code: 'country_required' }), true);
  assert.equal(isTermsRequired({ code: 'capacity_required' }), true);
  assert.equal(isTermsRequired({ code: 'business_name_required' }), true);
  assert.equal(isTermsRequired(new Error('x')), false);
  assert.equal(isTermsRequired(null), false);
});

test('existing accounts: asked once per sign-in, never when not required, "Later" holds only for that session', () => {
  const need = { version: 'v0.4', required: true };
  assert.equal(shouldAskTerms(need, 'sid-1', null), true);
  assert.equal(shouldAskTerms(need, 'sid-1', 'sid-1'), false);      // "Later" in this session
  assert.equal(shouldAskTerms(need, 'sid-2', 'sid-1'), true);       // a new sign-in is a new session
  assert.equal(shouldAskTerms({ version: 'v0.4', required: false }, 'sid-1', null), false);
  assert.equal(shouldAskTerms(null, 'sid-1', null), false);        // a failed status call shows nothing
  assert.equal(shouldAskTerms(undefined, null, null), false);
  assert.equal(shouldAskTerms(need, null, null), true);
});

test('the box sentence: R5 wording per capacity, EN and zh-CN, built from the locale pieces', () => {
  const sentence = (a, cap) => [a.lead[cap], a.terms, a.sep1, a.agreement, a.mid[cap], a.privacy, a.tail].join('');
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
  const zh = common('zh-CN').legal.accept;
  for (const cap of ['business', 'sole_trader', 'other']) {
    assert.ok(sentence(zh, cap).includes('我在免费模式下的数据一经用于训练即不可撤销，即使我此后订购该电机、订立付费协议或切换至私有模式亦然。'), cap);
    assert.ok(sentence(en, cap).includes('Training on my FREE data cannot be undone, even if I later order the motor, sign a paid agreement or switch to PRIVATE.'), cap);
  }
  assert.ok(sentence(zh, 'business').startsWith('我接受《使用条款》及《平台协议》（免费模式）') && sentence(zh, 'business').endsWith('我已阅读《隐私政策》。'));
  assert.equal(en.capacity.business, 'For a business I am authorised to bind');
  assert.equal(en.capacity.sole_trader, 'For myself as a registered sole trader or self-employed professional');
  assert.equal(en.capacity.other, 'Neither (for example I work for an employer or customer without authority to bind it)');
  assert.equal(en.earlier, 'Also use the engineering data my account entered before today');
  assert.equal(zh.earlier, '同时使用我的账户在今天之前输入的工程数据');
  assert.equal(en.country, 'Country where your business is established');
  assert.equal(common('en').legal.update.deadline, 'From {date} new simulations need these terms.');
  assert.equal(common('zh-CN').legal.update.deadline, '自 {date} 起，启动新的仿真须先接受这些条款。');
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
    const stray = (v.replace(/\{\w+\}/g, '').match(/[A-Za-z][A-Za-z0-9_]*/g) ?? []).filter((w) => !allowed.has(w));
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

test('the sign-in dialog, the OAuth consent sign-up and the shared surfaces use the box and the in-app links', () => {
  const read = (...p) => readFileSync(join(HERE, '..', '..', ...p), 'utf8');
  const login = read('components', 'auth', 'LoginDialog.tsx');
  assert.match(login, /<TermsBox /);
  assert.match(login, /acceptancePayload\(acceptance, TERMS_VERSION, \{ labels: legalLabels\(t, acceptance\) \}\)/);     // the e-mail form (also the OAuth consent page sign-up: returnTo)
  assert.match(login, /acceptancePayload\(acceptanceRef\.current, TERMS_VERSION, \{ labels: legalLabels\(tRef\.current, acceptanceRef\.current\) \}\)/);   // Google
  const box = read('components', 'common', 'TermsBox.tsx');
  assert.ok(box.indexOf('legal.accept.capacity.label') < box.indexOf('legal.accept.country') && box.indexOf('legal.accept.country') < box.indexOf('legal.accept.business') && box.indexOf('legal.accept.business') < box.indexOf('<Checkbox'));   // capacity, country, name, then the box
  assert.match(box, /<FormControl component="fieldset" required/);                // the capacity is required
  assert.match(box, /<TextField select[^>]*required/);                             // the country is required
  assert.match(box, /required=\{cap === 'business'\}/);                              // the business name is required for a business only
  assert.match(box, /showEarlier && \(/);                                           // the earlier-data box exists only where asked for
  assert.match(login, /disabled=\{busy \|\| boxHint\}/);
  assert.doesNotMatch(login, /TERMS_URL|PRIVACY_URL|emotres\.com/);
  const consent = read('components', 'auth', 'OAuthConsent.tsx');
  assert.match(consent, /signIn\(\{ mode: 'register', returnTo \}\)/);        // the sign-up of the consent page IS the LoginDialog
  assert.doesNotMatch(read('lib', 'localAuth.ts'), /emotres\.com\/(terms|privacy)/);
  assert.match(read('Root.tsx'), /matchLegalPage\(path\)/);
  assert.match(read('Root.tsx'), /<TermsUpdateHost \/>/);
  assert.match(read('components', 'landing', 'Landing.tsx'), /termsUrl\(legalLang\)[\s\S]*agreementUrl\(legalLang\)[\s\S]*privacyUrl\(legalLang\)/);
  assert.match(read('components', 'common', 'AnalyticsConsent.tsx'), /privacyUrl\(/);
  assert.doesNotMatch(read('components', 'common', 'AnalyticsConsent.tsx'), /https:\/\/emotres\.com/);
});

test('capacity: a business must be named, a sole trader may, "neither" has no name; the name of "neither" is never sent', () => {
  const f = (o) => ({ capacity: 'business', country: 'DE', business: '', ticked: true, earlier: false, ...o });
  assert.equal(acceptanceComplete(f({})), false);                                                  // business without a name
  assert.equal(acceptanceComplete(f({ business: '  ' })), false);
  assert.equal(acceptanceComplete(f({ business: 'Motor GmbH' })), true);
  assert.equal(acceptanceComplete(f({ capacity: 'sole_trader' })), true);
  assert.equal(acceptanceComplete(f({ capacity: 'other' })), true);
  assert.equal(acceptanceComplete(f({ capacity: 'owner' })), false);
  assert.deepEqual(['business', 'sole_trader', 'other', ''].map(asksBusinessName), [true, true, false, false]);
  assert.equal(acceptancePayload(f({ capacity: 'other', business: 'Employer AG' }), 'v0.4').business_name, '');
  assert.equal(acceptancePayload(f({ capacity: 'sole_trader', business: ' Solo ' }), 'v0.4').business_name, 'Solo');
});

test('the earlier-data election goes only with the existing-account dialog, with the labels as shown', () => {
  const f = { capacity: 'business', country: 'de', business: 'Acme', ticked: true, earlier: true };
  const sign = acceptancePayload(f, 'v0.4', { labels: { accept: 'A', election: 'E' } });
  assert.deepEqual(sign, { accept_terms: true, terms_version: 'v0.4', country: 'DE', business_name: 'Acme', capacity: 'business', accept_label: 'A' });
  assert.ok(!('historical_opt_in' in sign));                                                       // a new account has no earlier data
  const ex = acceptancePayload(f, 'v0.4', { existing: true, labels: { accept: 'A', election: 'E' } });
  assert.equal(ex.historical_opt_in, true); assert.equal(ex.election_label, 'E');
  assert.equal(acceptancePayload({ ...f, earlier: false }, 'v0.4', { existing: true }).historical_opt_in, false);   // initially unticked = false
  assert.equal(emptyAcceptance().earlier, false);
});

test('the deadline reported by the server is shown only when it is an ISO date', () => {
  assert.equal(termsDeadline({ required_from: '2026-12-01' }), '2026-12-01');
  for (const bad of [null, undefined, {}, { required_from: null }, { required_from: 'soon' }, { required_from: '2026-12-01T00:00:00Z' }]) assert.equal(termsDeadline(bad), null);
  assert.match(readFileSync(join(HERE, '..', '..', 'components', 'auth', 'TermsUpdateDialog.tsx'), 'utf8'), /legal\.update\.deadline/);
});

test('the label kept with the record is the sentence beside the box, in the language shown', () => {
  const en = common('en').legal.accept;
  const label = acceptLabelText(en, 'sole_trader');
  assert.ok(label.startsWith('I accept the Terms of Use and the Platform Agreement (FREE mode)') && label.endsWith('Privacy Policy.'));
});
