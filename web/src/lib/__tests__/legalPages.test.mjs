/** Pure helpers and link/wording checks for the Privacy Policy (/privacy) and Terms (/terms) pages. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  DEFAULT_PRIVACY_URL, DEFAULT_TERMS_URL, PRIVACY_SECTION_IDS, TERMS_SECTION_IDS,
  isLegalPath, matchLegalPage, resolvePrivacyUrl, resolveTermsUrl,
} from '../legalLogic.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = join(HERE, '..', '..');
const LOCALES = join(SRC, 'locales');
const flat = (o, p = '') => Object.entries(o).flatMap(([k, v]) =>
  v && typeof v === 'object' ? flat(v, p ? `${p}.${k}` : k) : [[p ? `${p}.${k}` : k, v]]);

test('matchLegalPage and isLegalPath match /privacy and /terms in EN and zh-CN', () => {
  for (const p of ['/privacy', '/privacy/', '/zh-cn/privacy', '/zh-CN/privacy/']) {
    assert.equal(matchLegalPage(p), 'privacy', p);
    assert.equal(isLegalPath(p), true, p);
  }
  for (const p of ['/terms', '/terms/', '/zh-cn/terms', '/zh-CN/terms/']) {
    assert.equal(matchLegalPage(p), 'terms', p);
    assert.equal(isLegalPath(p), true, p);
  }
  for (const p of ['/', '/zh-cn/', '/preferences', '/privacy-policy', '/terms-conditions', '/privacy/extra', null, undefined, 42]) {
    assert.equal(matchLegalPage(p), null, String(p));
    assert.equal(isLegalPath(p), false, String(p));
  }
});

test('resolveTermsUrl and resolvePrivacyUrl default to in-app routes and keep env overrides', () => {
  assert.equal(DEFAULT_TERMS_URL, '/terms');
  assert.equal(DEFAULT_PRIVACY_URL, '/privacy');
  assert.equal(resolveTermsUrl(undefined), '/terms');
  assert.equal(resolveTermsUrl(''), '/terms');
  assert.equal(resolveTermsUrl('   '), '/terms');
  assert.equal(resolveTermsUrl('https://custom.example/terms'), 'https://custom.example/terms');
  assert.equal(resolvePrivacyUrl(undefined), '/privacy');
  assert.equal(resolvePrivacyUrl(''), '/privacy');
  assert.equal(resolvePrivacyUrl('  https://custom.example/privacy  '), 'https://custom.example/privacy');
});

test('localAuth, AnalyticsConsent, Root, and Landing wire /privacy and /terms', () => {
  const localAuth = readFileSync(join(SRC, 'lib', 'localAuth.ts'), 'utf8');
  assert.match(localAuth, /TERMS_URL\s*=\s*resolveTermsUrl\(import\.meta\.env\.VITE_TERMS_URL\)/);
  assert.match(localAuth, /PRIVACY_URL\s*=\s*resolvePrivacyUrl\(import\.meta\.env\.VITE_PRIVACY_URL\)/);
  assert.doesNotMatch(localAuth, /emotres\.com\/(terms-conditions|privacy-policy)/);

  const consent = readFileSync(join(SRC, 'components', 'common', 'AnalyticsConsent.tsx'), 'utf8');
  assert.match(consent, /import\s+\{[^}]*PRIVACY_URL[^}]*\}\s+from\s+'\.\.\/\.\.\/lib\/localAuth'/);
  assert.doesNotMatch(consent, /https:\/\/emotres\.com/);

  const root = readFileSync(join(SRC, 'Root.tsx'), 'utf8');
  assert.match(root, /matchLegalPage\(path\)/);
  assert.match(root, /<LegalPage\s+kind=\{legalKind\}\s*\/>/);

  const landing = readFileSync(join(SRC, 'components', 'landing', 'Landing.tsx'), 'utf8');
  assert.match(landing, /href=\{TERMS_URL\}/);
  assert.match(landing, /href=\{PRIVACY_URL\}/);
});

test('EN and zh-CN legal sections have full key parity and cover required app facts', () => {
  const en = JSON.parse(readFileSync(join(SRC, 'locales', 'en', 'common.json'), 'utf8')).legal;
  const zh = JSON.parse(readFileSync(join(SRC, 'locales', 'zh-CN', 'common.json'), 'utf8')).legal;
  assert.ok(en && zh);

  assert.deepEqual(Object.keys(en.privacy.sections), [...PRIVACY_SECTION_IDS]);
  assert.deepEqual(Object.keys(zh.privacy.sections), [...PRIVACY_SECTION_IDS]);
  assert.deepEqual(Object.keys(en.terms.sections), [...TERMS_SECTION_IDS]);
  assert.deepEqual(Object.keys(zh.terms.sections), [...TERMS_SECTION_IDS]);

  for (const id of PRIVACY_SECTION_IDS) {
    assert.ok(en.privacy.sections[id].title && en.privacy.sections[id].body, `en privacy.${id}`);
    assert.ok(zh.privacy.sections[id].title && zh.privacy.sections[id].body, `zh privacy.${id}`);
  }
  for (const id of TERMS_SECTION_IDS) {
    assert.ok(en.terms.sections[id].title && en.terms.sections[id].body, `en terms.${id}`);
    assert.ok(zh.terms.sections[id].title && zh.terms.sections[id].body, `zh terms.${id}`);
  }

  const enPrivacyAll = Object.values(en.privacy.sections).map((s) => s.body).join(' ');
  const enTermsAll = Object.values(en.terms.sections).map((s) => s.body).join(' ');
  const zhPrivacyAll = Object.values(zh.privacy.sections).map((s) => s.body).join(' ');
  const zhTermsAll = Object.values(zh.terms.sections).map((s) => s.body).join(' ');

  for (const needle of [
    'MOTRES d.o.o.', '7255691000', 'SI22399933', 'Umami', 'Google Analytics',
    'China', 'Hetzner', 'Germany', '/preferences', 'support@aerostator.com',
  ]) {
    assert.ok(enPrivacyAll.includes(needle), `en privacy missing "${needle}"`);
  }
  for (const needle of ['Apache License 2.0', 'MOTRES d.o.o.', 'NDA', 'support@aerostator.com', '/preferences']) {
    assert.ok(enTermsAll.includes(needle), `en terms missing "${needle}"`);
  }
  for (const needle of ['MOTRES d.o.o.', 'Umami', 'Google Analytics', 'Hetzner', '/preferences', 'support@aerostator.com']) {
    assert.ok(zhPrivacyAll.includes(needle), `zh privacy missing "${needle}"`);
  }
  for (const needle of ['Apache License 2.0', 'MOTRES d.o.o.', 'NDA', 'support@aerostator.com', '/preferences']) {
    assert.ok(zhTermsAll.includes(needle), `zh terms missing "${needle}"`);
  }
});

test('all EN and zh-CN locale files have exact key parity', () => {
  const enFiles = readdirSync(join(LOCALES, 'en')).filter((f) => f.endsWith('.json')).sort();
  const zhFiles = readdirSync(join(LOCALES, 'zh-CN')).filter((f) => f.endsWith('.json')).sort();
  assert.deepEqual(zhFiles, enFiles);
  for (const ns of enFiles) {
    const enKeys = flat(JSON.parse(readFileSync(join(LOCALES, 'en', ns), 'utf8'))).map(([k]) => k).sort();
    const zhKeys = flat(JSON.parse(readFileSync(join(LOCALES, 'zh-CN', ns), 'utf8'))).map(([k]) => k).sort();
    assert.deepEqual(zhKeys, enKeys, `key mismatch in ${ns}`);
  }
});

