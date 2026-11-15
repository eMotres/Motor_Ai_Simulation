// Propellers page (Simulations > Propellers, owner 2026-11-15): the catalogue rows shown from GET /api/propellers.
// Pure, no imports of React or the window: web/src/lib/__tests__/propellersCatalog.test.mjs imports this file.

/** One record of `propeller.summary()` as the API serves it (only what the page reads). */
export interface PropellerSummary {
  id: string;
  vendor?: string | null;
  model?: string | null;
  diameter_in?: number | null;
  pitch_in?: number | null;
  blades?: number | null;
  data_quality?: string | null;
  power_data?: string | null;
  selectable?: boolean | null;
  rpm_range_tested?: number[] | null;
}

export type Quality = 'measured' | 'powerEstimated' | 'geometryOnly';

export interface CatalogRow {
  id: string;
  name: string;
  diameterIn: number | null;
  pitchIn: number | null;
  blades: number | null;
  quality: Quality;
  selectable: boolean;
  maxRpmTested: number | null;
}

const finite = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** How the data of one propeller was obtained.  An unknown value reads as the weakest class, never as measured. */
export function qualityOf(p: Pick<PropellerSummary, 'data_quality' | 'power_data'>): Quality {
  if (p.data_quality === 'torque_measured') return 'measured';
  if (p.data_quality === 'thrust_measured_power_estimated') return 'powerEstimated';
  return 'geometryOnly';
}

/** Table rows: smallest diameter first, then name; a malformed entry (no id) is dropped.  Only the top of the tested rpm
 *  range is kept (the page shows ONE value, never a min-max range). */
export function catalogRows(resp: unknown): CatalogRow[] {
  const list = (resp as { propellers?: unknown } | null | undefined)?.propellers;
  if (!Array.isArray(list)) return [];
  const rows: CatalogRow[] = [];
  for (const raw of list as PropellerSummary[]) {
    if (!raw || typeof raw.id !== 'string' || !raw.id) continue;
    const rng = Array.isArray(raw.rpm_range_tested) ? raw.rpm_range_tested.map(finite).filter((x): x is number => x !== null) : [];
    const name = [raw.vendor, raw.model].filter((s): s is string => typeof s === 'string' && s.trim() !== '').join(' ') || raw.id;
    rows.push({
      id: raw.id, name, diameterIn: finite(raw.diameter_in), pitchIn: finite(raw.pitch_in),
      blades: finite(raw.blades), quality: qualityOf(raw), selectable: raw.selectable === true,
      maxRpmTested: rng.length ? Math.max(...rng) : null,
    });
  }
  return rows.sort((a, b) => (a.diameterIn ?? Infinity) - (b.diameterIn ?? Infinity) || a.name.localeCompare(b.name));
}
