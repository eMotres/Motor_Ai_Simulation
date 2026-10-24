export type LoadType = 'air_propeller' | 'marine_propeller' | 'water_jet' | 'wheel' | 'pump' | 'engine_driven_generator' | 'unknown';
export type MissionSource = 'manual' | 'catalog';
export interface MissionRow {
  row_id: string; phase: string; speed_rpm: number | null; torque_Nm: number | null;
  duration_s: number | null; battery_voltage_V: number | null; source: MissionSource;
  sample_id?: string; source_snapshot?: Readonly<Record<string, unknown>>;
  conditions?: Readonly<Record<string, number>>;
  stale?: boolean; override?: { rpm?: boolean; torque_Nm?: boolean };
  shaft_side?: 'load' | 'motor'; direct_drive_confirmed?: boolean;
}
export interface LoadDraft {
  load_type: LoadType; source: 'catalog' | 'manual' | 'unknown'; catalog_id?: string;
  unknown_fields?: string[];
  temperature_C?: number; altitude_m?: number; pressure_Pa?: number;
  load_inertia_kg_m2?: number | null; startup_mode?: string; shaft_side?: string;
  vendor?: string; model?: string; data_quality?: string; power_data?: string;
  catalog_metadata?: Readonly<Record<string, unknown>>;
  answer_status?: Readonly<Record<string, 'filled' | 'missing' | 'not_known_yet'>>;
  series?: Readonly<{ rpm: number[]; torque_Nm: number[]; shaft_power_W: number[]; extrapolated: boolean[]; quality: string; ambient_C: number; altitude_m: number; pressure_Pa: number | null; tested_domain_rpm?: [number, number] | null; pitch_in?: number | null; fit?: unknown; source_urls?: string[]; content_digest?: string }>;
}
export const shaftPowerW = (torqueNm: number, rpm: number): number => torqueNm * 2 * Math.PI * rpm / 60;
export function missionSourceSnapshot(load: LoadDraft, extrapolated: boolean): Readonly<Record<string, unknown>> {
  const s = load.series;
  return Object.freeze({ catalog_id: load.catalog_id ?? '', catalog_type: load.load_type, calculation_model: 'server_propeller_series', vendor: load.vendor ?? '', model: load.model ?? '', data_quality: load.data_quality ?? 'unknown', power_data: load.power_data ?? 'unknown',
    temperature_C: s?.ambient_C ?? load.temperature_C ?? 25, altitude_m: s?.altitude_m ?? load.altitude_m ?? 0,
    ...(s?.pressure_Pa != null || load.pressure_Pa != null ? { pressure_Pa: s?.pressure_Pa ?? load.pressure_Pa } : {}),
    extrapolated, ...(s?.tested_domain_rpm ? { rpm_range_tested: s.tested_domain_rpm } : {}), ...(s?.pitch_in != null ? { pitch_in: s.pitch_in } : {}), shaft_side: 'load',
    digest: s?.content_digest ?? 'digest-unavailable' });
}
export function missionSampleId(snapshot: Readonly<Record<string, unknown>>, rpm: number): string {
  const nullable = (key: string) => snapshot[key] ?? null;
  return JSON.stringify([snapshot.catalog_id ?? 'catalog', snapshot.content_digest ?? snapshot.digest ?? snapshot.sha256 ?? 'digest-unavailable', snapshot.catalog_type ?? '', snapshot.calculation_model ?? '', snapshot.shaft_side === 'motor' ? 'motor' : 'load', nullable('temperature_C'), nullable('altitude_m'), nullable('pressure_Pa'), snapshot.direct_drive_confirmed === true, rpm]);
}
export function appendSamples(rows: MissionRow[], points: Array<{ rpm: number; torque_Nm: number; extrapolated?: boolean }>, snapshot: Readonly<Record<string, unknown>>, defaults: { duration_s?: number | null; voltage_V?: number | null; battery_voltage_V?: number | null; phaseLabel?: string } = {}): MissionRow[] {
  const next = [...rows];
  for (const point of points) {
    const digest = snapshot.content_digest ?? snapshot.digest ?? snapshot.sha256;
    const sampleId = missionSampleId(snapshot, point.rpm);
    if (next.some((row) => row.source === 'catalog' && row.sample_id === sampleId)) continue;
    const sourceSnapshot = { catalog_id: snapshot.catalog_id, catalog_type: snapshot.catalog_type, calculation_model: snapshot.calculation_model, vendor: snapshot.vendor, model: snapshot.model, data_quality: snapshot.data_quality, power_data: snapshot.power_data, digest, sha256: digest, extrapolated: !!point.extrapolated, ...(snapshot.rpm_range_tested != null ? { rpm_range_tested: snapshot.rpm_range_tested } : {}), ...(snapshot.pitch_in != null ? { pitch_in: snapshot.pitch_in } : {}), shaft_side: snapshot.shaft_side === 'motor' ? 'motor' : 'load', direct_drive_confirmed: snapshot.direct_drive_confirmed === true };
    const conditions = Object.fromEntries(['temperature_C', 'altitude_m', 'pressure_Pa'].flatMap((key) => typeof snapshot[key] === 'number' ? [[key, snapshot[key] as number]] : []));
    next.push({ row_id: globalThis.crypto?.randomUUID?.() ?? `mission-${Date.now()}-${next.length}`, phase: `${defaults.phaseLabel ?? 'Catalog'} ${point.rpm} rpm`, speed_rpm: point.rpm, torque_Nm: point.torque_Nm, duration_s: defaults.duration_s ?? null, battery_voltage_V: defaults.battery_voltage_V ?? defaults.voltage_V ?? null, source: 'catalog', sample_id: sampleId, source_snapshot: Object.freeze(sourceSnapshot), conditions: Object.freeze(conditions), shaft_side: snapshot.shaft_side === 'motor' ? 'motor' : 'load', direct_drive_confirmed: snapshot.direct_drive_confirmed === true, override: {} });
  }
  return next;
}
export function updateMissionRow(rows: MissionRow[], rowId: string, patch: Partial<MissionRow>): MissionRow[] {
  return rows.map((row) => row.row_id === rowId ? { ...row, ...patch, override: row.source === 'catalog' ? { ...row.override, ...(patch.speed_rpm !== undefined ? { rpm: true } : {}), ...(patch.torque_Nm !== undefined ? { torque_Nm: true } : {}) } : row.override } : row);
}
const sameOptionalNumber = (a: unknown, b: unknown) => (a == null ? null : a) === (b == null ? null : b);
export function markCatalogRowsStale(rows: MissionRow[], catalogId: string | undefined, conditions: { temperature_C?: number; altitude_m?: number; pressure_Pa?: number | null; catalog_type?: string; content_digest?: string; calculation_model?: string; source?: string }): MissionRow[] {
  return rows.map((row) => { if (row.source !== 'catalog') return row; const s = row.source_snapshot ?? {}, c = row.conditions ?? {}; return { ...row, stale: s.catalog_id !== catalogId || conditions.source != null && conditions.source !== 'catalog' || conditions.catalog_type != null && s.catalog_type !== conditions.catalog_type || conditions.calculation_model != null && s.calculation_model !== conditions.calculation_model || conditions.content_digest != null && (s.digest ?? s.sha256) !== conditions.content_digest || conditions.temperature_C != null && c.temperature_C !== conditions.temperature_C || conditions.altitude_m != null && c.altitude_m !== conditions.altitude_m || !sameOptionalNumber(c.pressure_Pa, conditions.pressure_Pa) }; });
}
export function refreshCatalogRows(rows: MissionRow[], points: Array<{ rpm: number; torque_Nm: number; extrapolated?: boolean }>, snapshot: Readonly<Record<string, unknown>>): MissionRow[] {
  const byRpm = new Map(points.map((p) => [p.rpm, p]));
  return rows.map((row) => {
    if (row.source !== 'catalog' || !row.stale) return row;
    const point = byRpm.get(row.speed_rpm ?? NaN); if (!point) return row;
    const digest = snapshot.content_digest ?? snapshot.digest ?? snapshot.sha256;
    const sourceSnapshot: Record<string, unknown> = { ...row.source_snapshot, ...Object.fromEntries(Object.entries(snapshot).filter(([key, value]) => !(['pitch_in', 'rpm_range_tested', 'pressure_Pa'].includes(key) && value == null))), catalog_id: snapshot.catalog_id ?? row.source_snapshot?.catalog_id, catalog_type: snapshot.catalog_type ?? row.source_snapshot?.catalog_type, calculation_model: snapshot.calculation_model ?? row.source_snapshot?.calculation_model, vendor: snapshot.vendor ?? row.source_snapshot?.vendor, model: snapshot.model ?? row.source_snapshot?.model, data_quality: snapshot.data_quality ?? row.source_snapshot?.data_quality, power_data: snapshot.power_data ?? row.source_snapshot?.power_data, digest, sha256: digest, extrapolated: !!point.extrapolated, shaft_side: snapshot.shaft_side ?? row.source_snapshot?.shaft_side, direct_drive_confirmed: snapshot.direct_drive_confirmed ?? row.source_snapshot?.direct_drive_confirmed };
    if (snapshot.pitch_in === null || snapshot.pitch_in === undefined) delete sourceSnapshot.pitch_in;
    if (snapshot.rpm_range_tested === null || snapshot.rpm_range_tested === undefined) delete sourceSnapshot.rpm_range_tested;
    const conditions = Object.fromEntries(['temperature_C', 'altitude_m', 'pressure_Pa'].flatMap((key) => typeof snapshot[key] === 'number' ? [[key, snapshot[key] as number]] : []));
    return { ...row, torque_Nm: row.override?.torque_Nm ? row.torque_Nm : point.torque_Nm, stale: false, sample_id: missionSampleId({ ...snapshot, ...conditions, digest, shaft_side: sourceSnapshot.shaft_side, direct_drive_confirmed: sourceSnapshot.direct_drive_confirmed }, point.rpm), source_snapshot: Object.freeze(sourceSnapshot), conditions: Object.freeze(conditions) };
  });
}

export function isValidLoadCurve(points: Array<{ rpm: number; torque_Nm: number }>): boolean {
  if (points.length < 2) return false;
  let previous = -Infinity;
  for (const point of points) {
    if (!Number.isFinite(point.rpm) || point.rpm < 0 || point.rpm <= previous || !Number.isFinite(point.torque_Nm) || point.torque_Nm < 0) return false;
    previous = point.rpm;
  }
  return true;
}

export function selectCatalogSamples<T extends { rpm: number }>(points: T[], domain?: [number, number] | null, count = 9): T[] {
  const available = points.filter((p) => Number.isFinite(p.rpm) && p.rpm > 0 && (!domain || (p.rpm >= domain[0] && p.rpm <= domain[1]))).sort((a, b) => a.rpm - b.rpm);
  if (!available.length) return [];
  const low = Math.max(available[0].rpm, domain?.[0] ?? available[0].rpm), high = Math.min(available[available.length - 1].rpm, domain?.[1] ?? available[available.length - 1].rpm);
  if (high < low) return [];
  const n = Math.max(1, Math.min(count, available.length));
  const selected = new Set<T>();
  for (let i = 0; i < n; i += 1) {
    const target = n === 1 ? (low + high) / 2 : low + (high - low) * i / (n - 1);
    const nearest = available.reduce((best, point) => Math.abs(point.rpm - target) < Math.abs(best.rpm - target) ? point : best);
    selected.add(nearest);
  }
  return [...selected].sort((a, b) => a.rpm - b.rpm);
}
