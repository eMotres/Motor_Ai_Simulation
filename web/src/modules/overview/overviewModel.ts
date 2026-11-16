/** The owner's Overview (owner 2026-11-16): the catalog tree (die -> motors) with, per motor, the projects that use it and the
 *  productions that make it, two columns that are never merged; boards grouped by platform the same way.  Pure functions over
 *  `/api/overview` and `/api/family/tree`, so they run under `node --test`. */
import type { DisplayFilter, DisplayState, DisplayStateRec } from '../../lib/displayState.ts';
import type { ArchivedDie } from '../../lib/dieArchiveApi.ts';

export interface OverviewProject { id: string; title: string; customer_org: string; status: string; updated_at: string;
  spec_submitted?: { version: number; at: string | null } | null; motors: string[]; display: DisplayStateRec; project_id?: string | null;
  /** derived by the server, never stored: in_production / computed / the project's own status */
  stage?: string }
export interface OverviewProduction { die: string; config: string; title: string; status: string; current_revision: string;
  order_quantity: number; product_type: string; updated_at: string; display: DisplayStateRec; production_id?: string | null }
export interface OverviewItem { die: string; config: string; item_id: string | null; kind: 'motor' | 'board'; state: string;
  layers: string[]; projects: Array<{ id: string; link_status: string }>; productions: string[]; works_with: string[];
  analysis_controllers: string[]; computed?: boolean }
export interface OverviewAnswer { items: Record<string, OverviewItem>; projects: Record<string, OverviewProject>;
  productions: Record<string, OverviewProduction>; no_item_yet: string[]; review: Array<Record<string, unknown>>;
  registry: { objects: number; backfilled_at: string } | null;
  /** dies that left the catalog listing: this workspace's archive and the shared dies retired for all users */
  archived_dies?: ArchivedDie[] }
export interface TreeDie { name: string; slots?: number | null; poles?: number | null; stator_diameter?: number | null;
  configs?: Array<{ name: string }> }

export interface Row { key: string; item: OverviewItem; projects: Array<OverviewProject & { link_status: string }>;
  productions: OverviewProduction[]; state: DisplayState;
  /** no current project link and no production: a superseded or trial variant, shown muted after the current one */
  muted: boolean }
export interface Family { die: string; slots: number | null; poles: number | null; od: number | null; inCatalog: boolean; rows: Row[] }
export interface OverviewView { motors: Family[]; controllers: Family[]; catalogOnly: TreeDie[]; noItemYet: OverviewProject[];
  counts: Record<DisplayFilter, number> }

const RANK: Record<DisplayState, number> = { active: 0, parked: 1, archived: 2 };

/** A row is as current as its most current project or production: active if any is active, then parked, else archived. */
export function rowState(projects: Array<{ display: DisplayStateRec }>, productions: Array<{ display: DisplayStateRec }>): DisplayState {
  const all = [...projects, ...productions].map((x) => x.display?.state ?? 'active');
  if (!all.length) return 'active';
  return all.reduce<DisplayState>((best, s) => (RANK[s] < RANK[best] ? s : best), 'archived');
}

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

export function buildView(answer: OverviewAnswer, tree: TreeDie[], filter: DisplayFilter): OverviewView {
  const dies = new Map(tree.filter((d) => d && typeof d.name === 'string').map((d) => [d.name, d]));
  const counts: Record<DisplayFilter, number> = { active: 0, parked: 0, archived: 0, all: 0 };
  const motors = new Map<string, Family>(), controllers = new Map<string, Family>();
  // a row of an archived or retired die is archived, whatever its links say: the die left the catalog (owner 2026-11-17)
  const gone = new Set((answer.archived_dies ?? []).map((d) => d.die));
  for (const [key, item] of Object.entries(answer.items).sort(([a], [b]) => a.localeCompare(b))) {
    const projects = item.projects.map((p) => answer.projects[p.id] && { ...answer.projects[p.id], link_status: p.link_status })
      .filter((p): p is OverviewProject & { link_status: string } => !!p);
    const productions = item.productions.map((k) => answer.productions[k]).filter((p): p is OverviewProduction => !!p);
    const state = gone.has(item.die) ? 'archived' : rowState(projects, productions);
    counts[state] += 1; counts.all += 1;
    if (filter !== 'all' && state !== filter) continue;
    const groups = item.kind === 'board' ? controllers : motors;
    let fam = groups.get(item.die);
    if (!fam) {
      const d = dies.get(item.die);
      fam = { die: item.die, slots: num(d?.slots), poles: num(d?.poles), od: num(d?.stator_diameter), inCatalog: !!d, rows: [] };
      groups.set(item.die, fam);
    }
    const muted = !productions.length && !projects.some((p) => p.link_status !== 'trial');
    fam.rows.push({ key, item, projects, productions, state, muted });
  }
  const linkedDies = new Set(Object.values(answer.items).map((i) => i.die));
  const byName = (a: Family, b: Family) => a.die.localeCompare(b.die);
  for (const fam of [...motors.values(), ...controllers.values()]) {
    fam.rows.sort((a, b) => Number(a.muted) - Number(b.muted) || a.item.config.localeCompare(b.item.config));
  }
  return {
    motors: [...motors.values()].sort(byName),
    controllers: [...controllers.values()].sort(byName),
    catalogOnly: tree.filter((d) => d && typeof d.name === 'string' && !linkedDies.has(d.name)).sort((a, b) => a.name.localeCompare(b.name)),
    noItemYet: answer.no_item_yet.map((id) => answer.projects[id]).filter((p): p is OverviewProject => !!p
      && (filter === 'all' || (p.display?.state ?? 'active') === filter)),
    counts,
  };
}

/** The flat Projects tab: one row per project, newest activity first, filtered by its own display state. */
export function flatProjects(answer: OverviewAnswer, filter: DisplayFilter): OverviewProject[] {
  return Object.values(answer.projects)
    .filter((p) => filter === 'all' || (p.display?.state ?? 'active') === filter)
    .sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || '') || a.title.localeCompare(b.title));
}

/** The family header shows Ø and slots/poles only when the die name does not already say them ("CIANO10 12_40 Ø12 12s10p"). */
export function headerChips(f: { die: string; od: number | null; slots: number | null; poles: number | null }): { od: boolean; sp: boolean } {
  const name = f.die.toLowerCase();
  return {
    od: f.od !== null && !name.includes(`ø${f.od}`),
    sp: f.slots !== null && f.poles !== null && !name.includes(`${f.slots}s${f.poles}p`),
  };
}
