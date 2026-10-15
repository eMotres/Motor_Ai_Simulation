// The construction scope "Active | Full" - ONE shared UI state for the Geometry and the Materials tab (owner 2026-10-15).
//
//   active  the electromagnetic construction: the cross-section geometry, its parameter table, the EM material assignment.
//   full    the whole motor: the CAD model, every construction parameter, THE part tree.
//
// Switching it in one tab switches the other (one store, one stored choice).  The choice is remembered per signed-in user in
// this browser (localStorage; the old Materials-only key 'materials.mode' is read once as the starting value).  Pure and
// framework-free so node --test can pin it; the React hook lives in components/common/ConstructionScopeSwitch.tsx.

export type ConstructionScope = 'active' | 'full';

export const SCOPES: ReadonlyArray<{ id: ConstructionScope; labelKey: string; tipKey: string }> = [
  { id: 'active', labelKey: 'scopeActive', tipKey: 'scopeActiveTip' },
  { id: 'full', labelKey: 'scopeFull', tipKey: 'scopeFullTip' },
];

export const SCOPE_STORAGE_KEY = 'construction.scope';
export const LEGACY_SCOPE_KEY = 'materials.mode';

/** Anything but 'full' is Active: a tab opens as it always did. */
export function parseScope(raw: unknown): ConstructionScope {
  return raw === 'full' ? 'full' : 'active';
}

/** The per-user storage key (anonymous / unknown user share 'anon'). */
export function scopeKey(email: string | null | undefined): string {
  const e = typeof email === 'string' && email.trim() ? email.trim().toLowerCase() : 'anon';
  return `${SCOPE_STORAGE_KEY}.${encodeURIComponent(e)}`;
}

export interface KeyValueStorage {
  getItem(k: string): string | null;
  setItem(k: string, v: string): void;
}

function safeGet(s: KeyValueStorage | null | undefined, k: string): string | null {
  try { return s ? s.getItem(k) : null; } catch { return null; }
}

/** The remembered scope of a user: its own key, else the legacy Materials key, else Active. */
export function readScope(storage: KeyValueStorage | null | undefined, email: string | null | undefined): ConstructionScope {
  const own = safeGet(storage, scopeKey(email));
  if (own !== null) return parseScope(own);
  return parseScope(safeGet(storage, LEGACY_SCOPE_KEY));
}

export interface ScopeStore {
  get(): ConstructionScope;
  set(next: ConstructionScope): void;
  /** Re-read the stored choice for this user (after sign-in / sign-out); returns the new scope. */
  bindUser(email: string | null | undefined): ConstructionScope;
  subscribe(fn: () => void): () => void;
}

export function createScopeStore(storage: KeyValueStorage | null | undefined): ScopeStore {
  let email: string | null | undefined = null;
  let scope: ConstructionScope = readScope(storage, email);
  const subs = new Set<() => void>();
  const emit = () => { subs.forEach((f) => f()); };
  return {
    get: () => scope,
    set(next) {
      const v = parseScope(next);
      if (v === scope) return;
      scope = v;
      try { storage?.setItem(scopeKey(email), v); } catch { /* a remembered choice is a convenience */ }
      emit();
    },
    bindUser(e) {
      if ((e ?? null) === (email ?? null)) return scope;
      email = e ?? null;
      const next = readScope(storage, email);
      if (next !== scope) { scope = next; emit(); }
      return scope;
    },
    subscribe(fn) { subs.add(fn); return () => { subs.delete(fn); }; },
  };
}

function browserStorage(): KeyValueStorage | null {
  try { return typeof localStorage === 'undefined' ? null : localStorage; } catch { return null; }
}

/** THE store both tabs read and write. */
export const scopeStore: ScopeStore = createScopeStore(browserStorage());
