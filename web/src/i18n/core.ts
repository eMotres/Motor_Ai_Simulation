// i18n bootstrap (docs/I18N.md) — i18next + react-i18next + ICU MessageFormat.
//
// * EN is the source language; every other locale mirrors its key set
//   (web/src/locales/<lng>/<ns>.json, checked by i18nParity.test.mjs).
// * `common` and `errors` load with the shell; every tab's namespace is lazy:
//   a component that calls useTranslation('thermal') pulls locales/<lng>/
//   thermal.json the first time it renders (Suspense is off — the component
//   re-renders when it arrives, showing English meanwhile, never a blank).
// * A key missing in zh-CN falls back to English; in dev it also warns once.
import i18next, { type i18n as I18n, type Resource } from 'i18next';
import ICU from 'i18next-icu';
import resourcesToBackend from 'i18next-resources-to-backend';
import { initReactI18next } from 'react-i18next';
import { DEFAULT_LOCALE, SUPPORTED_LOCALES } from './locale';

export const NAMESPACES = ['common', 'errors', 'help', 'motors', 'geometry', 'simulation',
  'results', 'controller', 'thermal', 'admin'] as const;
export type Namespace = (typeof NAMESPACES)[number];

/** Flatten a nested resource object into dotted keys (parity test + dev warn). */
export function flattenKeys(obj: unknown, prefix = ''): string[] {
  if (obj == null || typeof obj !== 'object') return prefix ? [prefix] : [];
  return Object.entries(obj as Record<string, unknown>).flatMap(([k, v]) =>
    flattenKeys(v, prefix ? `${prefix}.${k}` : k));
}

const warned = new Set<string>();
function warnMissingAgainstEnglish(inst: I18n, lng: string, ns: string): void {
  if (lng === DEFAULT_LOCALE) return;
  const en = new Set(flattenKeys(inst.getResourceBundle(DEFAULT_LOCALE, ns)));
  const have = new Set(flattenKeys(inst.getResourceBundle(lng, ns)));
  const missing = [...en].filter((k) => !have.has(k));
  const tag = `${lng}/${ns}`;
  if (missing.length && !warned.has(tag)) {
    warned.add(tag);
    console.warn(`[i18n] ${tag}: ${missing.length} key(s) fall back to English:`, missing.slice(0, 20));
  }
}

/** Build an i18next instance.  `resources` (tests) bypasses the lazy loader. */
export function createI18n(opts: { lng?: string; resources?: Resource; dev?: boolean } = {}): I18n {
  const inst = i18next.createInstance();
  inst.use(ICU).use(initReactI18next);
  if (!opts.resources) {
    inst.use(resourcesToBackend((lng: string, ns: string) =>
      import(`../locales/${lng}/${ns}.json`)));
  }
  const dev = opts.dev ?? false;
  void inst.init({
    lng: opts.lng ?? DEFAULT_LOCALE,
    fallbackLng: DEFAULT_LOCALE,
    supportedLngs: [...SUPPORTED_LOCALES],
    load: 'currentOnly',          // never ask for a bare "zh" bundle
    ns: ['common', 'errors'],
    defaultNS: 'common',
    fallbackNS: 'common',
    resources: opts.resources,
    partialBundledLanguages: true,
    interpolation: { escapeValue: false },   // React escapes
    returnNull: false,
    react: { useSuspense: false },
    saveMissing: dev,
    missingKeyHandler: dev
      ? (lngs, ns, key) => console.warn(`[i18n] missing key ${ns}:${key} (${lngs.join(',')})`)
      : undefined,
  });
  if (dev) inst.on('loaded', (loaded) => {
    for (const [lng, nss] of Object.entries(loaded))
      for (const ns of Object.keys(nss as object)) {
        // English must be loaded too for the comparison; ask, then compare.
        void inst.loadLanguages(DEFAULT_LOCALE).then(() => warnMissingAgainstEnglish(inst, lng, ns));
      }
  });
  // <html lang> follows the interface: screen readers + CJK font selection.
  inst.on('languageChanged', (lng) => {
    try { document.documentElement.lang = lng; } catch { /* no DOM (tests) */ }
  });
  return inst;
}
