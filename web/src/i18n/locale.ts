// Interface-language selection (docs/I18N.md).  Pure helpers, no React, so the
// node tests exercise them directly.
//
// Order of precedence, first hit wins:
//   1. the signed-in user's server preference (/api/me/preferences) — applied
//      by i18n/persist.ts once /api/me has answered;
//   2. this browser's localStorage (`ui.locale`);
//   3. a first-visit guess from the browser alone (detectInitialLanguage.ts):
//      a Chinese language tag or a China time zone -> zh-CN;
//   4. English, the source language.

import { detectInitialLanguage } from './detectInitialLanguage';

export const SUPPORTED_LOCALES = ['en', 'zh-CN'] as const;
export type Locale = (typeof SUPPORTED_LOCALES)[number];
export const DEFAULT_LOCALE: Locale = 'en';
export const STORAGE_KEY = 'ui.locale';

/** Browser-tab title per language (set with <html lang> on every switch). */
export const DOCUMENT_TITLES: Record<Locale, string> = {
  en: 'AeroStator Core — Motor Technology Portal',
  'zh-CN': 'AeroStator Core — 电机技术平台',
};

/** Endonyms for the switcher — each language names itself. */
export const LOCALE_LABELS: Record<Locale, string> = { en: 'English', 'zh-CN': '简体中文' };

/** Map any BCP-47 tag onto a shipped locale, or null.  All Chinese variants
 *  go to zh-CN for now (zh-TW / zh-HK readers can read Simplified; a
 *  Traditional mirror would be a new locale folder, nothing else). */
export function normalizeLocale(tag: unknown): Locale | null {
  if (typeof tag !== 'string' || !tag) return null;
  const t = tag.trim().toLowerCase().replace('_', '-');
  if (t === 'en' || t.startsWith('en-')) return 'en';
  if (t === 'zh' || t.startsWith('zh-')) return 'zh-CN';
  return null;
}

type StorageLike = Pick<Storage, 'getItem' | 'setItem'> | null | undefined;

function safeStorage(): StorageLike {
  try { return typeof localStorage === 'undefined' ? null : localStorage; } catch { return null; }
}

export function readStoredLocale(storage: StorageLike = safeStorage()): Locale | null {
  try { return normalizeLocale(storage?.getItem(STORAGE_KEY)); } catch { return null; }
}

export function writeStoredLocale(loc: Locale, storage: StorageLike = safeStorage()): void {
  try { storage?.setItem(STORAGE_KEY, loc); } catch { /* private window / blocked storage */ }
}

export function detectLocale(opts: {
  storage?: StorageLike; languages?: readonly string[] | null; timeZone?: string | null;
} = {}): Locale {
  const stored = readStoredLocale(opts.storage === undefined ? safeStorage() : opts.storage);
  if (stored) return stored;
  return detectInitialLanguage({ languages: opts.languages, timeZone: opts.timeZone });
}
