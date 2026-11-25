/** The owner queue's card logic (docs/OWNER_QUEUE.md): order, Skip, filters, the keyboard map, ages.  Pure, no React, so it runs under
 *  `node --test`.  The server decides the queue order; `queueOrder` is the same rule so a client-side list stays in step. */

export type OqKind = 'decision' | 'question' | 'message' | 'catalog_write' | 'outside_message';
export type OqState = 'open' | 'answered' | 'read' | 'withdrawn';
export interface OqOption { label: string; recommended: boolean }
export interface OqAnswer { option: string | null; text: string | null; by: string; at: string; approved_hash?: string }
export interface OqItem {
  id: string; kind: OqKind; project_id: string | null; asked_by: string; token_name: string; question: string; options: OqOption[];
  detail: string | null; recipient?: string | null; blocks: string | null; created_at: string; state: OqState; answer: OqAnswer | null;
}
export interface OqCount { open_blocking: number; open_messages: number; oldest_open_at: string | null }

/** Kinds whose card shows the detail (and the recipient) in full above the buttons: an approval of exact content. */
export const APPROVAL_KINDS: readonly OqKind[] = ['catalog_write', 'outside_message'];
export const isApproval = (k: OqKind): boolean => APPROVAL_KINDS.includes(k);
export const bandOf = (it: Pick<OqItem, 'kind'>): number => (it.kind === 'message' ? 2 : 1);

/** Band 1 (blocking kinds) before band 2 (messages); oldest first inside a band; a tie keeps the given order. */
export function queueOrder<T extends Pick<OqItem, 'kind' | 'created_at'>>(items: readonly T[]): T[] {
  return items.map((it, i) => ({ it, i })).sort((a, b) => bandOf(a.it) - bandOf(b.it)
    || (a.it.created_at < b.it.created_at ? -1 : a.it.created_at > b.it.created_at ? 1 : 0) || a.i - b.i).map((x) => x.it);
}

/** The cards of this round: queue order, then the skipped ones at the end in the order they were skipped.  Client-side only. */
export function orderCards<T extends Pick<OqItem, 'id' | 'kind' | 'created_at'>>(items: readonly T[], skipped: readonly string[]): T[] {
  const base = queueOrder(items);
  const rank = new Map(skipped.map((id, i) => [id, i]));
  const keep = base.filter((it) => !rank.has(it.id));
  const moved = base.filter((it) => rank.has(it.id)).sort((a, b) => (rank.get(a.id) as number) - (rank.get(b.id) as number));
  return [...keep, ...moved];
}

/** Skip a card: it goes to the end of this round (skipping it again moves it behind the others skipped before). */
export const skipCard = (skipped: readonly string[], id: string): string[] => [...skipped.filter((x) => x !== id), id];

export interface Filters { project: string; session: string }
export const NO_FILTER: Filters = { project: '', session: '' };
export const NO_PROJECT = '\u0000none';          // the filter value for "items without a project"

export function applyFilters<T extends Pick<OqItem, 'project_id' | 'asked_by'>>(items: readonly T[], f: Filters): T[] {
  return items.filter((it) => (!f.project || (f.project === NO_PROJECT ? !it.project_id : it.project_id === f.project))
    && (!f.session || it.asked_by === f.session));
}
export const projectChoices = (items: readonly Pick<OqItem, 'project_id'>[]): string[] =>
  [...new Set(items.map((i) => i.project_id).filter((x): x is string => !!x))].sort();
export const sessionChoices = (items: readonly Pick<OqItem, 'asked_by'>[]): string[] => [...new Set(items.map((i) => i.asked_by))].sort();
export const hasUnassigned = (items: readonly Pick<OqItem, 'project_id'>[]): boolean => items.some((i) => !i.project_id);

export const clampIndex = (i: number, n: number): number => (n <= 0 ? 0 : Math.min(Math.max(0, i), n - 1));

export type KeyAction = { type: 'prev' } | { type: 'next' } | { type: 'option'; index: number } | null;
/** Left / right arrows move, 1-5 pick an option; nothing happens while a text field has focus or a modifier is held. */
export function keyAction(key: string, ctx: { typing: boolean; optionCount: number; modifier?: boolean }): KeyAction {
  if (ctx.typing || ctx.modifier) return null;
  if (key === 'ArrowLeft') return { type: 'prev' };
  if (key === 'ArrowRight') return { type: 'next' };
  if (/^[1-5]$/.test(key)) { const index = Number(key) - 1; return index < ctx.optionCount ? { type: 'option', index } : null; }
  return null;
}

export type AgeUnit = 'ageMinutes' | 'ageHours' | 'ageDays';
export function ageParts(createdAt: string, now: number): { unit: AgeUnit; n: number } {
  const ms = Date.parse(createdAt);
  const s = Number.isFinite(ms) ? Math.max(0, Math.floor((now - ms) / 1000)) : 0;
  if (s < 3600) return { unit: 'ageMinutes', n: Math.floor(s / 60) };
  if (s < 86400) return { unit: 'ageHours', n: Math.floor(s / 3600) };
  return { unit: 'ageDays', n: Math.floor(s / 86400) };
}

export const badgeTotal = (c: OqCount | null): number => (c ? c.open_blocking + c.open_messages : 0);

/** History: answered, read and withdrawn items, newest first (by the time of the answer, else of the creation). */
export function historyOrder(items: readonly OqItem[]): OqItem[] {
  const when = (i: OqItem) => i.answer?.at || i.created_at;
  return items.filter((i) => i.state !== 'open').sort((a, b) => (when(a) < when(b) ? 1 : when(a) > when(b) ? -1 : 0));
}

/** The body of an answer: an option and / or a text; empty text is not sent. */
export function answerBody(option: string | null, text: string): { option?: string; text?: string } {
  const out: { option?: string; text?: string } = {};
  if (option) out.option = option;
  if (text.trim()) out.text = text.trim();
  return out;
}
