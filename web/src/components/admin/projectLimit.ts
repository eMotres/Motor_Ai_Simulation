// Admin · Users — the open-projects limit of an account (server: development/project_limits.py, owner 2026-10-30).
// A stored limit is a whole number or 'unlimited'; null = no override (the server's default applies).

export type ProjectLimit = number | 'unlimited' | null;
export const MAX_PROJECT_LIMIT = 1000;

/** The typed text as a limit: '' = back to the default, a whole number 0..1000, or 'unlimited' (also the symbol ∞). */
export function parseProjectLimit(text: string): { ok: true; value: ProjectLimit } | { ok: false } {
  const t = text.trim().toLowerCase();
  if (t === '') return { ok: true, value: null };
  if (t === 'unlimited' || t === '∞') return { ok: true, value: 'unlimited' };
  if (!/^\d+$/.test(t)) return { ok: false };
  const n = Number(t);
  return n <= MAX_PROJECT_LIMIT ? { ok: true, value: n } : { ok: false };
}

/** A limit as the field shows it ('' = default, so the placeholder reads "default (2)"). */
export const projectLimitText = (v: ProjectLimit | undefined): string => (v == null ? '' : String(v));

/** One short phrase: "unlimited", "3 projects", or "default (2)". */
export function projectLimitLabel(v: ProjectLimit | undefined, defaultLimit: number): string {
  if (v === 'unlimited') return 'unlimited';
  if (typeof v === 'number') return v === 1 ? '1 project' : `${v} projects`;
  return `default (${defaultLimit})`;
}

/** Does the typed text differ from what is stored (so a blur or Enter should save)? */
export function projectLimitChanged(text: string, stored: ProjectLimit | undefined): boolean {
  const parsed = parseProjectLimit(text);
  return parsed.ok && parsed.value !== (stored ?? null);
}
