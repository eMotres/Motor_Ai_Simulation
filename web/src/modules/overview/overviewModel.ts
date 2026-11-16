/** The owner's Overview (owner 2026-11-16): the catalog tree (die -> motors) with, per motor, the projects that use it and the
 *  productions that make it, two columns that are never merged; boards grouped by platform the same way.  Pure functions over
 *  `/api/overview` and `/api/family/tree`, so they run under `node --test`. */
import type { DisplayFilter, DisplayState, DisplayStateRec } from '../../lib/displayState.ts';

export interface OverviewProject { id: string; title: string; customer_org: string; status: string; updated_at: string;
  spec_submitted?: { version: number; at: string | null } | null; motors: string[]; display: DisplayStateRec; project_id?: string | null }
export interface OverviewProduction { die: string; config: string; title: string; status: string; current_revision: string;
  order_quantity: number; product_type: string; updated_at: string; display: DisplayStateRec; production_id?: string | null }
export interface OverviewItem { die: string; config: string; item_id: string | null; kind: 'motor' | 'board'; state: string;
  layers: string[]; projects: Array<{ id: string; link_status: string }>; productions: string[]; works_with: string[];
  analysis_controllers: string[] }
export interface OverviewAnswer { items: Record<string, OverviewItem>; projects: Record<string, OverviewProject>;
  productions: Record<string, OverviewProduction>; no_motor_yet: string[]; review: Array<Record<string, unknown>>;
  registry: { objects: number; backfilled_at: string } | null }
export interface TreeDie { name: string; slots?: number | null; poles?: number | null; stator_diameter?: number | null;
  configs?: Array<{ name: string }> }

export interface Row { key: string; item: OverviewItem; projects: Array<OverviewProject & { link_status: string }>;
  productions: OverviewProduction[]; state: DisplayState }
export interface Family { die: string; slots: number | null; poles: number | null; od: number | null; inCatalog: boolean; rows: Row[] }
export interface OverviewView { motors: Family[]; controllers: Family[]; catalogOnly: TreeDie[]; noMotorYet: OverviewProject[];
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
  for (const [key, item] of Object.entries(answer.items).sort(([a], [b]) => a.localeCompare(b))) {
    const projects = item.projects.map((p) => answer.projects[p.id] && { ...answer.projects[p.id], link_status: p.link_status })
      .filter((p): p is OverviewProject & { link_status: string } => !!p);
    const productions = item.productions.map((k) => answer.productions[k]).filter((p): p is OverviewProduction => !!p);
    const state = rowState(projects, productions);
    counts[state] += 1; counts.all += 1;
    if (filter !== 'all' && state !== filter) continue;
    const groups = item.kind === 'board' ? controllers : motors;
    let fam = groups.get(item.die);
    if (!fam) {
      const d = dies.get(item.die);
      fam = { die: item.die, slots: num(d?.slots), poles: num(d?.poles), od: num(d?.stator_diameter), inCatalog: !!d, rows: [] };
      groups.set(item.die, fam);
    }
    fam.rows.push({ key, item, projects, productions, state });
  }
  const linkedDies = new Set(Object.values(answer.items).map((i) => i.die));
  const byName = (a: Family, b: Family) => a.die.localeCompare(b.die);
  return {
    motors: [...motors.values()].sort(byName),
    controllers: [...controllers.values()].sort(byName),
    catalogOnly: tree.filter((d) => d && typeof d.name === 'string' && !linkedDies.has(d.name)).sort((a, b) => a.name.localeCompare(b.name)),
    noMotorYet: answer.no_motor_yet.map((id) => answer.projects[id]).filter((p): p is OverviewProject => !!p
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
