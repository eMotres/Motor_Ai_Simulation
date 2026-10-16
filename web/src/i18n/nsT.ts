// A namespace-bound translator for component files (docs/I18N.md).
//
// Extracted strings in large panels call `tx('key')` from module scope, so the
// extraction stays a mechanical, merge-friendly one-token change per string
// (several open PRs touch the same files).  The panel's main component calls
// `useTranslation('<ns>')` once: that subscribes the tree to language changes
// and lazy-loads the namespace; the file's small helper components re-render
// with it.
import type { TOptions } from 'i18next';
import i18n from './index';

export function nsT(ns: string) {
  return (key: string, opts?: TOptions): string => i18n.t(key, { ns, ...opts }) as string;
}
