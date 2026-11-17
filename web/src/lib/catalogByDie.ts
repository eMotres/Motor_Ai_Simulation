/** The Motors catalog by die (owner 2026-11-17: "in the style of the Overview: the die, and under it the motors from it").
 *  Family header (the designation family without the length, e.g. "CILN28 200") -> one block per die (lamination) -> its
 *  motors, each by the standard designation, the per-motor parameters BESIDE the name.  Pure over `GET /api/family/tree`
 *  (+ `GET /api/overview` for admins), so it runs under `node --test`.
 *
 *  Current or old (owner decision 2026-11-17): among motors with the SAME designation, the one a project or a production
 *  links is current; the rest are old (muted, sorted last).  Without any link among them, none is marked old: the catalog
 *  cannot tell.  The one rule is `isLinked` + `markOld` below. */

export interface ByDieBeside { steel?: string | null; magnet?: string | null; magnet_lamination_mm?: number | null; slot_insulation?: string | null }
export interface ByDieCfg {
  name: string; designation?: string | null; designation_complete?: boolean; designation_unknown?: string[];
  family?: string; beside?: ByDieBeside; locked?: boolean;
}
export interface ByDieDie { name: string; created?: string; slots?: number | null; poles?: number | null; stator_diameter?: number | null;
  locked?: boolean; layer?: string; configs: ByDieCfg[] }
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
  /** another motor with the same designation is the linked one */
  old: boolean;
}
export interface DieBlock { die: ByDieDie; motors: MotorRow[] }
export interface FamilyGroup { family: string; named: boolean; blocks: DieBlock[] }

const stripRev = (s: string) => s.replace(/ Rev [A-Z]+$/, '');

/** The family of a die: the first configuration's designation family; the die name when no configuration has one. */
export function familyOf(d: ByDieDie): { family: string; named: boolean } {
  const f = d.configs.map((c) => c.family).find((x) => typeof x === 'string' && x && !x.includes('?'));
  return f ? { family: f, named: true } : { family: d.name, named: false };
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
    if (same.length < 2 || !same.some((r) => r.linked)) continue;
    for (const r of same) r.old = !r.linked;
  }
}

export function buildByDie(dies: ByDieDie[], overview: ByDieOverview | null): FamilyGroup[] {
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
        die: d.name, config: c.name, label: named ? c.designation as string : c.name, named,
        complete: named && c.designation_complete !== false, unknown: c.designation_unknown ?? [],
        beside: c.beside ?? {}, projects, productions, computed: !!it?.computed, linked: false, old: false,
      };
      row.linked = isLinked(row);
      all.push(row);
      return row;
    });
    return { die: d, motors };
  });
  markOld(all);
  const rank = (m: MotorRow) => (m.old ? 2 : m.linked ? 0 : 1);
  for (const b of blocks) b.motors.sort((a, z) => rank(a) - rank(z) || a.label.localeCompare(z.label));
  const groups = new Map<string, FamilyGroup>();
  for (const b of blocks) {
    const { family, named } = familyOf(b.die);
    const g = groups.get(family) ?? { family, named, blocks: [] };
    g.blocks.push(b);
    groups.set(family, g);
  }
  // inside a family: the die with a current motor first, then the newest
  const blockRank = (b: DieBlock) => (b.motors.some((m) => m.linked) ? 0 : b.motors.every((m) => m.old) && b.motors.length ? 2 : 1);
  for (const g of groups.values()) {
    g.blocks.sort((a, z) => blockRank(a) - blockRank(z) || String(z.die.created ?? '').localeCompare(String(a.die.created ?? '')));
  }
  // named families by name, the dies the generator cannot name yet last
  return [...groups.values()].sort((a, z) => Number(a.named === false) - Number(z.named === false) || a.family.localeCompare(z.family));
}

/** The magnet segmentation in one short value: "solid" or the slice length; null when unknown. */
export function segmentationText(mm: number | null | undefined, words: { solid: string; slice: (mm: number) => string }): string | null {
  if (mm === null || mm === undefined) return words.solid;
  return Number.isFinite(mm) && mm > 0 ? words.slice(mm) : words.solid;
}
