// First-visit language (docs/I18N.md).  Pure, no DOM and no network: the
// browser's own signals only (no IP geolocation, no external service).
//
// A visitor with no saved choice gets zh-CN when
//   (a) any of navigator.languages / navigator.language is a Chinese tag, or
//   (b) the browser time zone is a China time zone;
// everyone else gets English.  A saved choice always wins (see locale.ts).

export type InitialLanguage = 'en' | 'zh-CN';

/** IANA zones that mean "the visitor is in China" (mainland + HK + Macau). */
export const CHINA_TIME_ZONES: readonly string[] = [
  'Asia/Shanghai', 'Asia/Chongqing', 'Asia/Harbin', 'Asia/Urumqi',
  'Asia/Kashgar', 'Asia/Hong_Kong', 'Asia/Macau',
];

/** zh, zh-CN, zh-Hans, zh-SG, zh-TW, zh-HK, zh_CN, ... (any case). */
export function isChineseTag(tag: unknown): boolean {
  if (typeof tag !== 'string') return false;
  const t = tag.trim().toLowerCase().replace(/_/g, '-');
  return t === 'zh' || t.startsWith('zh-');
}

export function isChinaTimeZone(tz: unknown): boolean {
  return typeof tz === 'string' && CHINA_TIME_ZONES.includes(tz.trim());
}

/** The browser's time zone, or null when Intl is missing or throws. */
export function browserTimeZone(): string | null {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || null; } catch { return null; }
}

/** The browser's language list (navigator.languages, else navigator.language). */
export function browserLanguages(): string[] {
  try {
    if (typeof navigator === 'undefined') return [];
    const l = navigator.languages;
    if (l && l.length) return [...l];
    return navigator.language ? [navigator.language] : [];
  } catch { return []; }
}

/** `languages` / `timeZone` default to the live browser values (tests pass them). */
export function detectInitialLanguage(opts: {
  languages?: readonly string[] | null; timeZone?: string | null;
} = {}): InitialLanguage {
  const langs = opts.languages === undefined ? browserLanguages() : (opts.languages ?? []);
  if (langs.some(isChineseTag)) return 'zh-CN';
  const tz = opts.timeZone === undefined ? browserTimeZone() : opts.timeZone;
  return isChinaTimeZone(tz) ? 'zh-CN' : 'en';
}
