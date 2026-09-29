// The app's i18n instance (docs/I18N.md); the machinery lives in ./core.
import { createI18n } from './core';
import { detectLocale } from './locale';

export { NAMESPACES, flattenKeys, createI18n } from './core';
export type { Namespace } from './core';

/** Detected locale: localStorage → browser languages → English. */
const i18n = createI18n({ lng: detectLocale(), dev: !!import.meta.env?.DEV });
export default i18n;
