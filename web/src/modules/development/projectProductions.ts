/** The Production view of a project (owner 2026-11-02): one block per linked motor that has a production (a motor M1 and generators G1 / G2 =
 *  three blocks).  There is no manual production <-> project link: the server derives it from the project's linked motors
 *  (`GET /api/projects/{id}/productions`, `development/productions.py`) and answers only what the caller may see.
 *
 *  Pure (no React): `node --test` imports this file. */

export interface ProjectProductionEntry {
  die: string;
  config: string;
  linked_die?: string;
  linked_config?: string;
  status_in_project?: string;
  title: { en: string; zh: string };
  revision: string;
  status: 'active' | 'paused' | 'archived' | string;
  /** member = the caller is a production member or an admin; project = derived read-only viewer; none = names only */
  access: 'member' | 'project' | 'none';
  role: string | null;
  product_revision?: { rev: string; label?: string } | null;
  documents?: number;
  discussions?: number;
  tests?: { count: number; by_kind: Record<string, number> };
  readiness?: { parts_total_count: number; parts_received_count: number; received_pct: number; late_count: number; ready_for_assembly: boolean };
}

export interface ProductionBlock {
  key: string;
  die: string;
  config: string;
  title: string;
  revision: string;
  productRevision: string;
  status: string;
  canOpen: boolean;
  /** [label key, value] chips in a fixed order; a count the caller may not see is absent, never 0 */
  counts: { key: 'tests' | 'documents' | 'discussions'; value: number }[];
  /** "parameters 2 · free_run 1 · load 3" pieces of the tests summary, in the Tests sub-tab order */
  testKinds: { kind: string; count: number }[];
  readiness: { received: number; total: number; pct: number; late: number; ready: boolean } | null;
}

const TEST_KIND_ORDER = ['parameters', 'free_run', 'load'];

const pickTitle = (title: { en?: string; zh?: string } | undefined, lang: string): string => {
  const en = String(title?.en ?? '').trim();
  const zh = String(title?.zh ?? '').trim();
  return lang.startsWith('zh') ? (zh || en) : (en || zh);
};

/** One block per entry, in the project's linked-motor order (the server keeps it); duplicates dropped. */
export function productionBlocks(entries: readonly ProjectProductionEntry[] | null | undefined, lang = 'en'): ProductionBlock[] {
  const seen = new Set<string>();
  const out: ProductionBlock[] = [];
  for (const e of entries ?? []) {
    if (!e || !e.die || !e.config) continue;
    const key = `${e.die.toLowerCase()}/${e.config.toLowerCase()}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const counts: ProductionBlock['counts'] = [];
    if (e.tests) counts.push({ key: 'tests', value: Math.max(0, Number(e.tests.count) || 0) });
    if (typeof e.documents === 'number') counts.push({ key: 'documents', value: Math.max(0, e.documents) });
    if (typeof e.discussions === 'number') counts.push({ key: 'discussions', value: Math.max(0, e.discussions) });
    const byKind = e.tests?.by_kind ?? {};
    const kinds = [...TEST_KIND_ORDER, ...Object.keys(byKind).filter((k) => !TEST_KIND_ORDER.includes(k)).sort()]
      .filter((k) => (Number(byKind[k]) || 0) > 0).map((kind) => ({ kind, count: Number(byKind[kind]) }));
    const r = e.readiness;
    out.push({
      key, die: e.die, config: e.config,
      title: pickTitle(e.title, lang) || `${e.die} / ${e.config}`,
      revision: String(e.revision ?? ''),
      productRevision: String(e.product_revision?.rev ?? ''),
      status: String(e.status ?? 'active'),
      canOpen: e.access === 'member' || e.access === 'project',
      counts, testKinds: kinds,
      readiness: r && r.parts_total_count > 0
        ? { received: r.parts_received_count, total: r.parts_total_count, pct: Math.round(r.received_pct), late: r.late_count, ready: !!r.ready_for_assembly }
        : null,
    });
  }
  return out;
}

/** The project page shows its Production view only when at least one linked motor has a production the caller may open. */
export const hasProductionView = (entries: readonly ProjectProductionEntry[] | null | undefined): boolean =>
  productionBlocks(entries).some((b) => b.canOpen);
