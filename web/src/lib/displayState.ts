/** Active / Parked / Archived (owner 2026-11-16): how staff lists show a project or a production.
 *  A presentation state for our side only (backend: motor_ai_sim/display_state.py); customers and the factory never load it.
 *  A parked object never wakes by itself (owner correction): new activity only shows as a count on the filter chip and a
 *  badge on the row inside the Parked / Archived list.  Pure functions, so they run under `node --test`. */

export const DISPLAY_STATES = ['active', 'parked', 'archived'] as const;
export type DisplayState = typeof DISPLAY_STATES[number];
export type DisplayFilter = DisplayState | 'all';
export const DISPLAY_FILTERS: readonly DisplayFilter[] = ['active', 'parked', 'archived', 'all'];

export interface DisplayStateRec { state: DisplayState; explicit: boolean; default: DisplayState; reason: string; by: string; at: string }
export interface DisplayStates { projects: Record<string, DisplayStateRec>; productions: Record<string, DisplayStateRec> }

export const productionStateKey = (die: string, config: string) => `${die}/${config}`;

/** The shown state of one object; an object the answer does not know yet (just created) is active. */
export function stateOf(map: Record<string, DisplayStateRec> | undefined, key: string): DisplayState {
  const s = map?.[key]?.state;
  return s && (DISPLAY_STATES as readonly string[]).includes(s) ? s : 'active';
}

/** Something happened in the object after it was parked / archived: a newer activity stamp than the state's own. */
export function activityAfter(rec: DisplayStateRec | undefined, stamps: Array<string | null | undefined>, unread = 0): boolean {
  if (!rec || rec.state === 'active') return false;
  if (unread > 0) return true;
  const at = Date.parse(rec.at || '');
  if (!Number.isFinite(at)) return false;
  return stamps.some((s) => { const t = Date.parse(s || ''); return Number.isFinite(t) && t > at; });
}

export interface FilterCounts { counts: Record<DisplayFilter, number>; woke: Record<DisplayFilter, number> }

export function filterCounts<T>(rows: T[], state: (row: T) => DisplayState, woke: (row: T) => boolean): FilterCounts {
  const counts: Record<DisplayFilter, number> = { active: 0, parked: 0, archived: 0, all: rows.length };
  const wokeN: Record<DisplayFilter, number> = { active: 0, parked: 0, archived: 0, all: 0 };
  for (const r of rows) {
    const s = state(r);
    counts[s] += 1;
    if (s !== 'active' && woke(r)) { wokeN[s] += 1; wokeN.all += 1; }
  }
  return { counts, woke: wokeN };
}

/** Rows shown under a filter; a search query shows every state (search finds everything). */
export function filterRows<T>(rows: T[], filter: DisplayFilter, state: (row: T) => DisplayState, searching = false): T[] {
  if (filter === 'all' || searching) return rows;
  return rows.filter((r) => state(r) === filter);
}
