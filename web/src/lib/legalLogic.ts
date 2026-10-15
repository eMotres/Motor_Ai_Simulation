// Privacy Policy (/privacy) and Terms & Conditions (/terms): pure helpers (no DOM, no network, no imports)
// so node --test can run them directly.

export type LegalPageKind = 'privacy' | 'terms';

export const DEFAULT_TERMS_URL = '/terms';
export const DEFAULT_PRIVACY_URL = '/privacy';

export const PRIVACY_SECTION_IDS = [
  'controller',
  'data',
  'analytics',
  'purpose',
  'storage',
  'sharing',
  'rights',
  'security',
  'changes',
  'contact',
] as const;

export const TERMS_SECTION_IDS = [
  'company',
  'scope',
  'accounts',
  'license',
  'nda',
  'orders',
  'leadTimes',
  'payment',
  'shipping',
  'warranty',
  'returns',
  'liability',
  'governingLaw',
  'changes',
  'contact',
] as const;

export type PrivacySectionId = (typeof PRIVACY_SECTION_IDS)[number];
export type TermsSectionId = (typeof TERMS_SECTION_IDS)[number];

/** Match /privacy or /terms (and /zh-cn/privacy, /zh-cn/terms), with or without a trailing slash. */
export function matchLegalPage(pathname: unknown): LegalPageKind | null {
  if (typeof pathname !== 'string') return null;
  if (/^\/(zh-cn\/)?privacy\/?$/i.test(pathname)) return 'privacy';
  if (/^\/(zh-cn\/)?terms\/?$/i.test(pathname)) return 'terms';
  return null;
}

export function isLegalPath(pathname: unknown): boolean {
  return matchLegalPage(pathname) !== null;
}

/** Resolve a legal URL from the build-time env override, falling back to the in-app path. */
export function resolveLegalUrl(envValue: unknown, fallback: string): string {
  const s = typeof envValue === 'string' ? envValue.trim() : '';
  return s || fallback;
}

export function resolveTermsUrl(envValue?: unknown): string {
  return resolveLegalUrl(envValue, DEFAULT_TERMS_URL);
}

export function resolvePrivacyUrl(envValue?: unknown): string {
  return resolveLegalUrl(envValue, DEFAULT_PRIVACY_URL);
}
