/**
 * Unread messages of projects and productions (owner 2026-10-28).
 *
 * The server keeps the read state (GET /api/messages/unread -> counts; POST .../read when a thread is shown).  This module holds
 * the pure helpers (tested with node --test) and ONE shared store the main menu, the lists and the pages read from, so a badge
 * changes everywhere the moment a thread is read.  `refreshUnread()` fetches the counts; App polls it while the page is visible.
 */

export interface UnreadCounts {
  projects: Record<string, number>;
  /** keyed `"<die>/<config>"` */
  productions: Record<string, number>;
  totalProjects: number;
  totalProductions: number;
}

export const NO_UNREAD: UnreadCounts = { projects: {}, productions: {}, totalProjects: 0, totalProductions: 0 };

const count = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) && v > 0 ? Math.floor(v) : 0);

function countMap(v: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  if (v && typeof v === 'object' && !Array.isArray(v)) {
    for (const [k, n] of Object.entries(v as Record<string, unknown>)) { const c = count(n); if (c) out[k] = c; }
  }
  return out;
}

/** The counts answer, or "nothing unread" for anything that is not one (the badge must never break the page). */
export function parseUnreadCounts(j: unknown): UnreadCounts {
  if (!j || typeof j !== 'object') return NO_UNREAD;
  const o = j as Record<string, unknown>;
  const projects = countMap(o.projects), productions = countMap(o.productions);
  const sum = (m: Record<string, number>) => Object.values(m).reduce((a, b) => a + b, 0);
  return {
    projects, productions,
    totalProjects: count(o.total_projects) || sum(projects),
    totalProductions: count(o.total_productions) || sum(productions),
  };
}

export const productionUnreadKey = (die: string, config: string) => `${die}/${config}`;

/** A badge text: nothing for 0, "99+" beyond two digits. */
export function unreadLabel(n: number): string {
  const c = count(n);
  return c === 0 ? '' : c > 99 ? '99+' : String(c);
}

interface Flagged { id?: string; unread?: boolean }

/** The ids of the messages the server flagged unread (project thread). */
export function unreadIdsOf(messages: Flagged[]): string[] {
  return messages.filter((m) => m.unread && m.id).map((m) => String(m.id));
}

/** Highlight state while a thread stays open: every message that was unread at any time since the view opened stays marked, even
 *  after the server has been told it is read (the mark applies on the next visit). */
export function accumulateHighlight(shown: ReadonlySet<string>, flagged: readonly string[]): Set<string> {
  const next = new Set(shown);
  for (const id of flagged) next.add(id);
  return next;
}

/** Index of the first highlighted message (where the "New" divider goes), or -1. */
export function firstUnreadIndex(messages: Flagged[], shown: ReadonlySet<string>): number {
  return messages.findIndex((m) => !!m.id && shown.has(String(m.id)));
}

// ── shared store ────────────────────────────────────────────────────────────────────────────────────────────────

const API = (import.meta.env?.VITE_API_URL ?? 'http://localhost:8001') as string;
let state: UnreadCounts | null = null;          // null = not read yet (lists fall back to their own per-item field)
const listeners = new Set<() => void>();
let inflight: Promise<void> | null = null;

export const getUnread = (): UnreadCounts | null => state;
export function subscribeUnread(fn: () => void): () => void { listeners.add(fn); return () => { listeners.delete(fn); }; }
function publish(next: UnreadCounts | null) {
  const same = state === next || (state && next && JSON.stringify(state) === JSON.stringify(next));
  if (same) return;
  state = next;
  listeners.forEach((fn) => fn());
}

/** Read the counts now (one request at a time).  A failure leaves the last counts. */
export function refreshUnread(): Promise<void> {
  if (inflight) return inflight;
  inflight = fetch(`${API}/api/messages/unread`, { cache: 'no-store' })
    .then((r) => (r.ok ? r.json() : null))
    .then((j) => { if (j) publish(parseUnreadCounts(j)); })
    .catch(() => { /* signed out or offline - keep what we have */ })
    .finally(() => { inflight = null; });
  return inflight;
}

/** Sign-out / identity change: drop the counts of the previous account. */
export const resetUnread = () => publish(null);
