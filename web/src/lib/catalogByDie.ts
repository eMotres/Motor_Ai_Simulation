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
  items: Record<string, { projects: Array<{ id: string; link_status: string }>; productions: string[]; computed?: boolean }>;
  projects: Record<string, { id: string; title: string; status: string; stage?: string }>;
  productions: Record<string, { die: string; config: string; status: string; current_revision: string }>;
}

export interface MotorRow {
  die: string; config: string;
  /** what is shown: the designation, or the stored configuration name when there is none (`named` false) */
  label: string; named: boolean; complete: boolean; unknown: string[];
  beside: ByDieBeside;
  projects: Array<{ id: string; title: string; trial: boolean; stage: string }>;
  productions: Array<{ status: string; rev: string }>;
  computed: boolean;
  /** a project (current link) or a production links it */
  linked: boolean;
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
        return pr ? { id: p.id, title: pr.title, trial: p.link_status === 'trial', stage: pr.stage || pr.status } : null;
      }).filter((p): p is MotorRow['projects'][number] => !!p);
      const productions = (it?.productions ?? []).map((k) => overview?.productions?.[k])
        .filter((p): p is NonNullable<typeof p> => !!p).map((p) => ({ status: p.status, rev: p.current_revision }));
      const named = typeof c.designation === 'string' && c.designation.length > 0;
      const row: MotorRow = {
        die: d.name, config: c.name, label: named ? c.designation as string : staff ? c.name : (c.display_name || ''), named,
        complete: named && c.designation_complete !== false, unknown: c.designation_unknown ?? [],
        beside: c.beside ?? {}, projects, productions, computed: !!it?.computed, linked: false, old: false,
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
