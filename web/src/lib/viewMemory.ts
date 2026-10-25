/** Where the user was (owner 2026-10-26): every page remembers its view after a browser reload.
 *
 *  ONE mechanism for all of them: per-viewer browser storage (localStorage) under the `view.` namespace.
 *  - Every read and write is wrapped in try/catch: blocked / full / missing storage never breaks a page, it just forgets.
 *  - A page validates what it reads (`validate` returns the value or null); a stored view that no longer exists
 *    (deleted project, lost access, renamed tab) is dropped silently and the page opens its default view.
 *  - Only view choices are stored: ids of tabs, sections, projects and steps.  Never a secret, token, password or key.
 *  Pure functions (no React) so they run under `node --test`; the hook lives in useRememberedView.ts. */

export const VIEW_PREFIX = 'view.';

/** Keys that must never be used for view memory. */
const FORBIDDEN = /token|secret|password|passwd|credential|api[-_.]?key|bearer|session[-_.]?id/i;

export interface ViewStorage { getItem(key: string): string | null; setItem(key: string, value: string): void; removeItem(key: string): void }

function defaultStorage(): ViewStorage | null {
  try { return typeof localStorage === 'undefined' ? null : localStorage; } catch { return null; }
}

/** `view.<scope>`: scope is a dotted page path, e.g. `development.<user email>` or `admin.section`. */
export function viewKey(scope: string): string {
  if (!scope || FORBIDDEN.test(scope)) throw new Error(`view memory: refused scope "${scope}"`);
  return scope.startsWith(VIEW_PREFIX) ? scope : `${VIEW_PREFIX}${scope}`;
}

/** The remembered value of a scope, or null when nothing valid is stored (empty, blocked or stale storage).
 *  A value written by an older build as a bare string is accepted as that string. */
export function readView<T>(scope: string, validate: (raw: unknown) => T | null, storage: ViewStorage | null = defaultStorage()): T | null {
  try {
    const text = storage?.getItem(viewKey(scope));
    if (text === null || text === undefined) return null;
    let raw: unknown;
    try { raw = JSON.parse(text); } catch { raw = text; }
    return validate(raw);
  } catch { return null; }
}

export function writeView(scope: string, value: unknown, storage: ViewStorage | null = defaultStorage()): void {
  try {
    if (value === null || value === undefined) storage?.removeItem(viewKey(scope));
    else storage?.setItem(viewKey(scope), JSON.stringify(value));
  } catch { /* not remembered this time */ }
}

export function clearView(scope: string, storage: ViewStorage | null = defaultStorage()): void { writeView(scope, null, storage); }

/** Validator: one of a fixed list of ids. */
export const oneOf = <T extends string>(ids: readonly T[]) => (raw: unknown): T | null => (typeof raw === 'string' && (ids as readonly string[]).includes(raw) ? raw as T : null);

/** Validator: a plain object whose every field passes its own check; unknown fields are dropped. */
export function shape<T extends object>(fields: { [K in keyof T]: (raw: unknown) => T[K] | undefined }) {
  return (raw: unknown): T | null => {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(fields) as Array<keyof T & string>) {
      const value = fields[key]((raw as Record<string, unknown>)[key]);
      if (value === undefined) return null;
      out[key] = value;
    }
    return out as T;
  };
}
export const text = (raw: unknown): string | undefined => (typeof raw === 'string' && raw.length > 0 && raw.length <= 200 ? raw : undefined);
export const nullableText = (raw: unknown): string | null | undefined => (raw === null ? null : text(raw));
export const bool = (raw: unknown): boolean | undefined => (typeof raw === 'boolean' ? raw : undefined);
export const smallInt = (raw: unknown): number | undefined => (typeof raw === 'number' && Number.isInteger(raw) && raw >= 0 && raw < 1000 ? raw : undefined);
