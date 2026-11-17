/** The Motors catalog by die (owner 2026-11-17: "in the style of the Overview: the die, and under it the motors from it").
 *  Family header (the designation family without the length, e.g. "CILN28 200") -> one block per die (lamination) -> its
 *  motors, each by the standard designation, the per-motor parameters BESIDE the name.  Pure over `GET /api/family/tree`
 *  (+ `GET /api/overview` for admins), so it runs under `node --test`.
 *
 *  Current or old (owner decision 2026-11-17, confirmed): among motors with the SAME designation, the one a project (current
 *  link) or a production names is current; the rest are old (muted, sorted last).  No link on any of them: the newest
 *  LOCKED one is current (marked "no link: newest locked"); none locked: none is marked.  The one rule is `markOld`. */
import { shownDuties } from './catalogRows.ts';

export interface ByDieBeside { steel?: string | null; magnet?: string | null; magnet_lamination_mm?: number | null; slot_insulation?: string | null }
export interface ByDieDuty { name: string; power_kw?: number | null; torque_nm?: number | null; duty_class?: string | null;
  mode?: string; rpm?: number | null; current_arms?: number | null;
  result?: { mass_kg?: number | null; efficiency_pct?: number | null; ripple_pct?: number | null; v_ll_peak_v?: number | null } | null;
  primary_drive?: string; runs?: Array<{ drive?: string; primary?: boolean; steps?: number | null; gap_layers?: number | null }> }
export interface ByDieBattery { cells?: number | null; v_cell_nom?: number | null; v_min?: number | null; v_max?: number | null; v_nom?: number | null }
export interface ByDieCfg {
  name: string; designation?: string | null; designation_complete?: boolean; designation_unknown?: string[];
  family?: string; beside?: ByDieBeside; locked?: boolean; duties?: ByDieDuty[]; battery?: ByDieBattery | null;
  /** for a customer / the factory, when the designation is not complete: family and length as far as known (server) */
  display_name?: string | null;
}
export interface ByDieDie { name: string; created?: string; slots?: number | null; poles?: number | null; stator_diameter?: number | null;
  locked?: boolean; layer?: string; configs: ByDieCfg[];
  /** the family header from the server (motor_designation.die_family): null when the die cannot be named yet */
  family?: string | null }
/** The Overview's answer, only the parts this view reads (admins only: the route is admin-gated). */
export interface ByDieOverview {
  items: Record<string, { projects: Array<{ id: string; link_status: string }>; productions: string[]; computed?: boolean;
    /** the ONE derived stage of the motor (server: routes/overview.motor_stage, owner 2026-11-17) */
    stage?: string | null }>;
  projects: Record<string, { id: string; title: string; status: string; stage?: string; display?: { state?: string } }>;
  productions: Record<string, { die: string; config: string; status: string; current_revision: string; display?: { state?: string } }>;
}

/** Active / Parked / Archived of a project or production (display_state); anything else reads as active. */
export type LinkState = 'active' | 'parked' | 'archived';
const linkState = (x: { display?: { state?: string } } | undefined): LinkState =>
  (x?.display?.state === 'parked' || x?.display?.state === 'archived' ? x.display.state : 'active') as LinkState;

export interface MotorRow {
  die: string; config: string;
  /** what is shown: the designation, or the stored configuration name when there is none (`named` false) */
  label: string; named: boolean; complete: boolean; unknown: string[];
  beside: ByDieBeside;
  projects: Array<{ id: string; title: string; trial: boolean; stage: string; state: LinkState }>;
  productions: Array<{ status: string; rev: string; state: LinkState }>;
  computed: boolean;
  /** a project (current link) or a production links it */
  linked: boolean;
  /** the server's derived stage (undefined: an older server, `motorStage` falls back) */
  stage?: string | null;
  /** another motor with the same designation is the current one */
  old: boolean;
  /** this one is current only by the fallback (no link on any of the same name): the newest locked one */
  newestLocked: boolean;
  locked: boolean; created: string;
  /** the point the row quotes (`ratedPoint`) */
  rated: ByDieDuty | null;
  /** its values beside the name (owner 2026-11-17): power · torque · speed · efficiency · ripple · voltage · phase current */
  values: RowValues | null;
}

/** The Simulation preset a quoted ripple must have been solved at (steps per electrical period, air-gap rows per side). */
export const RIPPLE_PRESET = { steps: 96, gapLayers: 2 } as const;

export interface RowValues {
  power_kw: number | null; torque_nm: number | null; rpm: number | null; efficiency_pct: number | null;
  ripple_pct: number | null;
  /** why the ripple is below the preset (muted): 'steps' (< 96 per period) / 'gap' (1 row per side); null = at the preset or unknown */
  ripple_low: { steps: number | null; gap_layers: number | null } | null;
  /** the configuration's battery (nominal, the range in the tooltip), else the point's line-line peak voltage */
  voltage: { kind: 'battery'; v: number | null; min: number | null; max: number | null; cells: number | null }
    | { kind: 'vll_peak'; v: number | null } | null;
  current_arms: number | null; mass_kg: number | null;
}

const fin = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** The values a row shows for its quoted point; missing ones stay null (a dash, never a guess). */
export function rowValues(c: ByDieCfg, d: ByDieDuty | null): RowValues | null {
  if (!d) return null;
  const run = (d.runs ?? []).find((r) => r.primary) ?? (d.runs ?? []).find((r) => r.drive === d.primary_drive) ?? null;
  const steps = fin(run?.steps), gap = fin(run?.gap_layers);
  const low = (steps !== null && steps < RIPPLE_PRESET.steps) || (gap !== null && gap < RIPPLE_PRESET.gapLayers);
  const b = c.battery;
  const nominal = fin(b?.v_nom) ?? (fin(b?.cells) !== null && fin(b?.v_cell_nom) !== null ? (b!.cells as number) * (b!.v_cell_nom as number) : null);
  return {
    power_kw: fin(d.power_kw), torque_nm: fin(d.torque_nm), rpm: fin(d.rpm), efficiency_pct: fin(d.result?.efficiency_pct),
    ripple_pct: fin(d.result?.ripple_pct), ripple_low: low ? { steps, gap_layers: gap } : null,
    voltage: b ? { kind: 'battery', v: nominal, min: fin(b.v_min), max: fin(b.v_max), cells: fin(b.cells) }
      : { kind: 'vll_peak', v: fin(d.result?.v_ll_peak_v) },
    current_arms: fin(d.current_arms), mass_kg: fin(d.result?.mass_kg),
  };
}
export interface DieBlock { die: ByDieDie; motors: MotorRow[] }
export interface FamilyGroup { family: string; named: boolean; blocks: DieBlock[] }

const stripRev = (s: string) => s.replace(/ Rev [A-Z]+$/, '');

/** The family of a die: the server's (`family`), else the first configuration's complete family; '' = cannot be named yet
 *  (those dies share ONE muted group at the end). */
export function familyOf(d: ByDieDie): { family: string; named: boolean } {
  const f = (typeof d.family === 'string' && d.family) ? d.family
    : d.configs.map((c) => c.family).find((x) => typeof x === 'string' && x && !x.includes('?'));
  return f ? { family: f, named: true } : { family: '', named: false };
}

/** The point the row quotes: the one marked rated (named `rated`, else the first whose name starts with `rated`), else the
 *  largest continuous point by power (a short-time point is never quoted as the rating). */
export function ratedPoint(c: ByDieCfg): ByDieDuty | null {
  const ds = shownDuties((c.duties ?? []) as never) as unknown as ByDieDuty[];
  const marked = ds.find((d) => d.name === 'rated') ?? ds.find((d) => String(d.name).toLowerCase().startsWith('rated'));
  if (marked) return marked;
  const cont = ds.filter((d) => d.duty_class !== 'short_time' && typeof d.power_kw === 'number' && Number.isFinite(d.power_kw));
  return cont.length ? cont.reduce((a, b) => (Math.abs(b.power_kw as number) > Math.abs(a.power_kw as number) ? b : a)) : null;
}

export function isLinked(m: Pick<MotorRow, 'projects' | 'productions'>): boolean {
  return m.productions.length > 0 || m.projects.some((p) => !p.trial);
}

/** Owner rule 2026-11-17: same designation -> the linked one is current, the rest old.  In place. */
export function markOld(rows: MotorRow[]): void {
  const byName = new Map<string, MotorRow[]>();
  for (const r of rows) if (r.named && r.complete) {
    const k = stripRev(r.label);
    byName.set(k, [...(byName.get(k) ?? []), r]);
  }
  for (const same of byName.values()) {
    if (same.length < 2) continue;
    if (same.some((r) => r.linked)) { for (const r of same) r.old = !r.linked; continue; }
    const locked = same.filter((r) => r.locked);
    if (!locked.length) continue;                      // nothing says which one: none is marked
    const cur = locked.reduce((a, b) => (b.created > a.created || (b.created === a.created && b.config > a.config) ? b : a));
    for (const r of same) { r.old = r !== cur; r.newestLocked = r === cur; }
  }
}

/** `staff` false (a customer, the factory): a motor without a complete designation is shown by its family and length
 *  (`display_name`) or not named at all ('' = "name pending"), never by its stored configuration name. */
export function buildByDie(dies: ByDieDie[], overview: ByDieOverview | null, opts: { staff?: boolean } = {}): FamilyGroup[] {
  const staff = opts.staff !== false;
  const all: MotorRow[] = [];
  const blocks: DieBlock[] = (dies ?? []).filter((d) => d && typeof d.name === 'string').map((d) => {
    const motors = (d.configs ?? []).map((c): MotorRow => {
      const key = `${d.name}/${c.name}`;
      const it = overview?.items?.[key];
      const projects = (it?.projects ?? []).map((p) => {
        const pr = overview?.projects?.[p.id];
        return pr ? { id: p.id, title: pr.title, trial: p.link_status === 'trial', stage: pr.stage || pr.status, state: linkState(pr) } : null;
      }).filter((p): p is MotorRow['projects'][number] => !!p);
      const productions = (it?.productions ?? []).map((k) => overview?.productions?.[k])
        .filter((p): p is NonNullable<typeof p> => !!p).map((p) => ({ status: p.status, rev: p.current_revision, state: linkState(p) }));
      const named = typeof c.designation === 'string' && c.designation.length > 0;
      const row: MotorRow = {
        die: d.name, config: c.name, label: named ? c.designation as string : staff ? c.name : (c.display_name || ''), named,
        complete: named && c.designation_complete !== false, unknown: c.designation_unknown ?? [],
        beside: c.beside ?? {}, projects, productions, computed: !!it?.computed, linked: false, old: false,
        ...(it && 'stage' in it ? { stage: it.stage ?? null } : {}),
        newestLocked: false, locked: !!(c.locked || d.locked), created: String(d.created ?? ''), rated: null, values: null,
      };
      row.rated = ratedPoint(c);
      row.values = rowValues(c, row.rated);
      row.linked = isLinked(row);
      all.push(row);
      return row;
    });
    return { die: d, motors };
  });
  // without the Overview (a customer, the factory, or before it answered) the links are unknown: nothing is marked, because
  // the fallback alone could call the linked motor "old"
  if (overview) markOld(all);
  const rank = (m: MotorRow) => (m.old ? 2 : m.linked || m.newestLocked ? 0 : 1);
  for (const b of blocks) b.motors.sort((a, z) => rank(a) - rank(z) || a.label.localeCompare(z.label));
  const groups = new Map<string, FamilyGroup>();
  for (const b of blocks) {
    const { family, named } = familyOf(b.die);
    const g = groups.get(family) ?? { family, named, blocks: [] };
    g.blocks.push(b);
    groups.set(family, g);
  }
  // inside a family: the die with a current motor first, then the newest
  const blockRank = (b: DieBlock) => (b.motors.some((m) => m.linked || m.newestLocked) ? 0
    : b.motors.every((m) => m.old) && b.motors.length ? 2 : 1);
  for (const g of groups.values()) {
    g.blocks.sort((a, z) => blockRank(a) - blockRank(z) || String(z.die.created ?? '').localeCompare(String(a.die.created ?? '')));
  }
  // named families by stator OD, then by name (the tab holds every diameter at once); the dies the generator cannot name yet last
  const od = (g: FamilyGroup) => Math.min(...g.blocks.map((b) => (typeof b.die.stator_diameter === 'number' ? b.die.stator_diameter : Infinity)));
  return [...groups.values()].sort((a, z) => Number(a.named === false) - Number(z.named === false) || od(a) - od(z)
    || a.family.localeCompare(z.family));
}

/** The magnet segmentation in one short value: "solid" or the slice length; null when unknown. */
export function segmentationText(mm: number | null | undefined, words: { solid: string; slice: (mm: number) => string }): string | null {
  if (mm === null || mm === undefined) return words.solid;
  return Number.isFinite(mm) && mm > 0 ? words.slice(mm) : words.solid;
}

// ── the table columns (owner 2026-11-17: "first the characteristics, then everything else", one column each) ──────────
export type ColKey = 'power' | 'torque' | 'speed' | 'eff' | 'ripple' | 'volt' | 'current' | 'cogging' | 'mass';
/** In the owner's order; `w` px fixed (tabular numerals) so the numbers line up across every family on the page. */
export const COLS: ReadonlyArray<{ key: ColKey; unit: string; w: number }> = [
  { key: 'power', unit: 'kW', w: 52 }, { key: 'torque', unit: 'N·m', w: 52 }, { key: 'speed', unit: 'rpm', w: 56 },
  { key: 'eff', unit: '%', w: 46 }, { key: 'ripple', unit: '%', w: 46 }, { key: 'volt', unit: 'V', w: 48 },
  { key: 'current', unit: 'A', w: 50 }, { key: 'cogging', unit: 'N·m', w: 58 }, { key: 'mass', unit: 'kg', w: 48 }];
export const COL_DIGITS: Record<ColKey, number> = { power: 2, torque: 2, speed: 0, eff: 1, ripple: 1, volt: 1, current: 1, cogging: 3, mass: 2 };
export const NAME_COL_W = 230;

/** The number in one column (null = a dash); the cogging comes from the configuration's saved cogging (the caller's). */
export function colValue(key: ColKey, m: Pick<MotorRow, 'values'>, cogging?: number | null): number | null {
  const v = m.values;
  if (key === 'cogging') return cogging ?? null;
  if (!v) return null;
  switch (key) {
    case 'power': return v.power_kw;
    case 'torque': return v.torque_nm;
    case 'speed': return v.rpm;
    case 'eff': return v.efficiency_pct;
    case 'ripple': return v.ripple_pct;
    case 'volt': return v.voltage?.v ?? null;
    case 'current': return v.current_arms;
    case 'mass': return v.mass_kg;
  }
  return null;
}

export type Sort = { key: ColKey; dir: 'asc' | 'desc' } | null;
/** A header click: first descending, then ascending, then back to the catalog order. */
export function nextSort(cur: Sort, key: ColKey): Sort {
  if (!cur || cur.key !== key) return { key, dir: 'desc' };
  return cur.dir === 'desc' ? { key, dir: 'asc' } : null;
}
/** Sorting stays INSIDE each die block (families and dies keep their places); a missing value sorts last either way. */
export function sortMotors<M extends Pick<MotorRow, 'values'>>(motors: M[], key: ColKey, dir: 'asc' | 'desc', coggingOf?: (m: M) => number | null): M[] {
  const val = (m: M) => colValue(key, m, coggingOf?.(m) ?? null);
  return [...motors].sort((a, b) => {
    const x = val(a), y = val(b);
    if (x === null && y === null) return 0;
    if (x === null) return 1;
    if (y === null) return -1;
    return dir === 'desc' ? y - x : x - y;
  });
}


// ── the admin's stage, project state and running work in the catalog (owner 2026-11-17) ─────────────────────────────

/** The derived stages, in order (owner 2026-11-17: "production starts when the money from the customer arrives"). */
export const STAGE_IDS = ['specification', 'simulation', 'confirmation', 'in_production', 'finished'] as const;
/** The i18n key of a stage: the derived ones in `common:overview.stageName`, a project status in `development:status`. */
export const stageKey = (s: string): string => ((STAGE_IDS as readonly string[]).includes(s) || s === 'computed'
  ? `common:overview.stageName.${s}` : `development:status.${s}`);

/** The derived stage of a motor: the SERVER's (routes/overview.motor_stage, from stored facts: the production's lifecycle,
 *  the offered flag, results, the submitted specification).  An older server without it: the old reading, which never
 *  says "in production" for a mere production record any more. */
export function motorStage(m: Pick<MotorRow, 'productions' | 'computed' | 'projects'> & { stage?: string | null }): string | null {
  if (m.stage !== undefined) return m.stage;
  if (m.productions.length) return 'confirmation';
  if (m.computed) return 'simulation';
  const p = m.projects.find((x) => !x.trial) ?? m.projects[0];
  return p ? p.stage : null;
}

const STATE_RANK: Record<LinkState, number> = { active: 0, parked: 1, archived: 2 };
/** A motor is as current as its most current project or production; null = linked to nothing (a catalog motor). */
export function motorState(m: Pick<MotorRow, 'projects' | 'productions'>): LinkState | null {
  const all = [...m.projects.map((p) => p.state), ...m.productions.map((p) => p.state)];
  if (!all.length) return null;
  return all.reduce<LinkState>((best, s) => (STATE_RANK[s] < STATE_RANK[best] ? s : best), 'archived');
}

export type StateFilter = LinkState | 'all';
/** Owner 2026-11-17 ("how do I remove old projects from the catalog"): with a state chosen, a row shows only the project
 *  and production chips in that state, and a motor linked ONLY to projects / productions in other states leaves the view.
 *  A motor linked to nothing stays under Active (and All).  A die block left without motors goes too; a family as well. */
export function filterByState(groups: FamilyGroup[], filter: StateFilter): FamilyGroup[] {
  if (filter === 'all') return groups;
  const out: FamilyGroup[] = [];
  for (const g of groups) {
    const blocks: DieBlock[] = [];
    for (const b of g.blocks) {
      const motors = b.motors.filter((m) => {
        const st = motorState(m);
        return st === null ? filter === 'active' : m.projects.some((p) => p.state === filter) || m.productions.some((p) => p.state === filter);
      }).map((m) => ({ ...m, projects: m.projects.filter((p) => p.state === filter),
        productions: m.productions.filter((p) => p.state === filter) }));
      if (motors.length || (!b.motors.length && filter === 'active')) blocks.push({ ...b, motors });
    }
    if (blocks.length) out.push({ ...g, blocks });
  }
  return out;
}

/** How many motors each state choice would show (the header's counts). */
export function stateCounts(groups: FamilyGroup[]): Record<StateFilter, number> {
  const n = (f: StateFilter) => filterByState(groups, f).reduce((a, g) => a + g.blocks.reduce((x, b) => x + b.motors.length, 0), 0);
  return { active: n('active'), parked: n('parked'), archived: n('archived'), all: n('all') };
}

/** One running job as `/api/catalog/jobs` answers it (motor_ai_sim.job_status). */
export interface RunningJob { source: string; tag: string; server: string; state: string; kind: string; session: string;
  die: string | null; configuration: string | null; started_at?: string | null;
  progress?: { done?: number | null; total?: number | null; unit?: string | null } | null;
  cpus_reserved?: number | null; cpu_used?: number | null; mem_reserved_gb?: number | null; mem_now_gb?: number | null }
export interface ServerView { server: string; state: 'live' | 'stale' | 'missing' | 'no_status'; age_s: number | null;
  budget?: { cpus?: number; mem_gb?: number } | null;
  totals?: { cpus_reserved?: number; cpus_used?: number; mem_reserved_gb?: number; mem_used_gb?: number; queued?: number } | null; jobs: number }
export interface JobsAnswer { servers: ServerView[]; motors: Record<string, RunningJob[]>; dies: Record<string, RunningJob[]>;
  unknown: RunningJob[]; unnamed: RunningJob[] }

/** The chip text of a job: kind, server, state, progress when it reports it (e.g. "optimisation · US · 12/40"). */
export function jobChip(j: RunningJob): string {
  const prog = j.progress && typeof j.progress.done === 'number' && typeof j.progress.total === 'number' ? ` · ${j.progress.done}/${j.progress.total}` : '';
  const state = j.state === 'queued' ? ' · queued' : '';
  return `${j.kind} · ${(j.server || '?').toUpperCase()}${state}${prog}`;
}

// ── filters (owner 2026-11-17: "filters of what is relevant for me now ... filter by stage"; saved sets "my current") ──

/** One filter set: the state of the linked projects, the stages, the projects, "has a running job", "only the highlighted
 *  project's motors".  Empty lists = no restriction; everything combines with AND (and with the application filter). */
export interface CatalogFilter { state: StateFilter; stages: string[]; projects: string[]; running: boolean; onlyHighlighted: boolean }
export const NO_FILTER: CatalogFilter = { state: 'active', stages: [], projects: [], running: false, onlyHighlighted: false };
/** Filter values that stand for more than one stage; none since 2026-11-17 (simulation and confirmation are separate). */
export const STAGE_GROUPS: Record<string, string[]> = {};

export function isFiltered(f: CatalogFilter): boolean {
  return f.stages.length > 0 || f.projects.length > 0 || f.running || f.onlyHighlighted;
}

/** Keep the motors that match every chosen part; drop the dies and families left empty.  `running(die, cfg)` = a job names it,
 *  `highlighted(die, cfg)` = the project the tab highlights links it (both supplied by the caller). */
export function applyFilters(groups: FamilyGroup[], f: CatalogFilter,
  running?: (die: string, cfg: string) => boolean, highlighted?: (die: string, cfg: string) => boolean): FamilyGroup[] {
  if (!isFiltered(f)) return groups;
  const stages = new Set(f.stages.flatMap((s) => STAGE_GROUPS[s] ?? [s]));
  const keep = (m: MotorRow) => (!stages.size || stages.has(motorStage(m) ?? ''))
    && (!f.projects.length || m.projects.some((p) => f.projects.includes(p.id)))
    && (!f.running || !!running?.(m.die, m.config))
    && (!f.onlyHighlighted || !!highlighted?.(m.die, m.config));
  const out: FamilyGroup[] = [];
  for (const g of groups) {
    const blocks = g.blocks.map((b) => ({ ...b, motors: b.motors.filter(keep) })).filter((b) => b.motors.length);
    if (blocks.length) out.push({ ...g, blocks });
  }
  return out;
}

export const motorCount = (groups: FamilyGroup[]) => groups.reduce((a, g) => a + g.blocks.reduce((x, b) => x + b.motors.length, 0), 0);

/** The stage choices present in the catalog (the derived stages, with "simulation" standing for computed + simulation). */
export function stageOptions(groups: FamilyGroup[]): string[] {
  const seen = new Set<string>();
  for (const g of groups) for (const b of g.blocks) for (const m of b.motors) {
    const s = motorStage(m);
    if (s) seen.add(s === 'computed' ? 'simulation' : s);
  }
  const order = [...STAGE_IDS, 'spec', 'spec_submitted', 'design', 'quote', 'prototype', 'production', 'closed'];
  return [...seen].sort((a, b) => (order.indexOf(a) + 100) % 100 - (order.indexOf(b) + 100) % 100 || a.localeCompare(b));
}

/** The projects linked from the catalog's motors, by title (admins: every project the Overview names). */
export function projectOptions(groups: FamilyGroup[]): Array<{ id: string; title: string }> {
  const seen = new Map<string, string>();
  for (const g of groups) for (const b of g.blocks) for (const m of b.motors) for (const p of m.projects) seen.set(p.id, p.title);
  return [...seen].map(([id, title]) => ({ id, title })).sort((a, b) => a.title.localeCompare(b.title));
}

/** A saved filter set ("my current"): named, several per account, the last used one remembered. */
export interface SavedFilterSet { name: string; filter: CatalogFilter }
export function upsertSet(sets: SavedFilterSet[], name: string, filter: CatalogFilter): SavedFilterSet[] {
  const n = name.trim();
  if (!n) return sets;
  const rest = sets.filter((s) => s.name !== n);
  return [...rest, { name: n, filter: { ...filter, stages: [...filter.stages], projects: [...filter.projects] } }]
    .sort((a, b) => a.name.localeCompare(b.name));
}
/** A stored value back into a filter (old / damaged storage gives the default, never a throw). */
export function parseFilter(raw: unknown): CatalogFilter | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const list = (v: unknown) => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string').slice(0, 50) : []);
  const state = ['active', 'parked', 'archived', 'all'].includes(String(r.state)) ? r.state as StateFilter : 'active';
  return { state, stages: list(r.stages), projects: list(r.projects), running: r.running === true, onlyHighlighted: r.onlyHighlighted === true };
}
export function parseSets(raw: unknown): SavedFilterSet[] | null {
  if (!Array.isArray(raw)) return null;
  return raw.slice(0, 30).map((s) => (s && typeof s === 'object' && typeof (s as SavedFilterSet).name === 'string'
    ? { name: (s as SavedFilterSet).name.slice(0, 60), filter: parseFilter((s as SavedFilterSet).filter) ?? NO_FILTER } : null))
    .filter((s): s is SavedFilterSet => !!s);
}
