// Propellers > Designer: the calls to /api/propellers/design (auth headers are attached by the global fetch wrapper).
import { formatApiErrorDetail } from './apiErrorDetail';
import type { EvalRecord, OutlinePoint, Vector, VarRow } from './propellerDesign';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const BASE = `${API.replace(/\/$/, '')}/api/propellers/design`;

export interface Family {
  id: string; banner: string; diameters_in: number[]; blades_options: number[]; scaling_rule: string;
  operating_points: Record<string, { die: string; config: string; torque_limit_Nm: number; rated_rpm_ref: number; rpm_search_max: number; motor_radius_mm: number; source: string }>;
  tip_mach_limit: { value: number; confirmed: boolean; source: string };
  manufacturing: { process: string | null; unset: string[]; unconfirmed: string[] };
  inputs_unconfirmed: boolean; seed: Vector; variables: VarRow[];
}
export interface Violation { rule: string; name: string | null; value: unknown; limit: unknown }
export interface ZoneReport {
  zone: string; airfoil: string; station_r_mm: number; chord_mm: number; t_c_ideal: number; t_c_manufactured: number;
  te_thickness_mm: number; le_radius_mm: number; active_limits: string[]; flags: string[];
}
export interface Geometry { outline: OutlinePoint[]; zones: ZoneReport[]; banner: string }
export interface CurveRow { rpm: number; thrust_N: number; torque_Nm: number; shaft_power_W: number; air_speed_ms: number | null; tip_mach: number }
export interface Curve { torque_limit_Nm: number; rated_rpm_ref: number; rows: CurveRow[]; tip_mach_limit: number }
export interface SweepRow { value: number | string; valid: boolean; violations?: Violation[]; objectives?: EvalRecord['objectives']; constraints_met?: boolean; rpm?: number; tip_mach?: number; design_id?: string }
export interface DraftInfo { name: string; saved_at: string | null; operating_point: string | null; diameter_in: number | null; blades: number | null }

export class DesignApiError extends Error {
  violations: Violation[];
  status: number;
  constructor(status: number, message: string, violations: Violation[] = []) { super(message); this.status = status; this.violations = violations; }
}

async function call<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method, signal, cache: 'no-store',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let j: unknown = null;
  try { j = await r.json(); } catch { /* not JSON */ }
  if (!r.ok) {
    const detail = (j as { detail?: unknown } | null)?.detail;
    const viol = (detail && typeof detail === 'object' && Array.isArray((detail as { violations?: unknown }).violations))
      ? ((detail as { violations: Violation[] }).violations) : [];
    throw new DesignApiError(r.status, formatApiErrorDetail(typeof detail === 'object' && viol.length ? (detail as { message?: string }).message : detail) || `HTTP ${r.status}`, viol);
  }
  return j as T;
}

export const getFamily = (diameter: number, blades: number, signal?: AbortSignal) =>
  call<Family>('GET', `/family?diameter_in=${diameter}&blades=${blades}`, undefined, signal);
export const checkVector = (vector: Vector) => call<{ valid: boolean; violations: Violation[] }>('POST', '/check', { vector });
export const getGeometry = (vector: Vector) => call<Geometry>('POST', '/geometry', { vector });
export const evaluate = (vector: Vector, op: string, signal?: AbortSignal) => call<EvalRecord>('POST', '/evaluate', { vector, op }, signal);
export const getCurve = (vector: Vector, op: string, n = 11) => call<Curve>('POST', '/curve', { vector, op, n });
export const sweep = (vector: Vector, op: string, variable: string, lo: number, hi: number, n: number) =>
  call<{ rows: SweepRow[] }>('POST', '/sweep', { vector, op, variable, lo, hi, n });
export interface OptEval { index: number; generation: number; vector: Vector; objectives: Record<string, number | null>; violation: number; note: string;
  design_id: string | null; rpm: number | null; tip_mach: number | null; constraints_met: boolean; flags: string[] }
export interface OptResult { run_id: string; front: number[]; evaluations: OptEval[]; elapsed_s: number; objectives: string[]; settings: Record<string, unknown> }
export const optimize = (vector: Vector, op: string, o: { pop: number; generations: number; seed: number; free: string[]; objectives: string[];
  enforce_tip_mach: boolean; tip_mach_limit: number | null }) => call<OptResult>('POST', '/optimize', { vector, op, ...o });
export const listDrafts = () => call<{ drafts: DraftInfo[] }>('GET', '/drafts');
export const getDraft = (name: string) => call<{ vector: Vector; operating_point: string }>('GET', `/drafts/${encodeURIComponent(name)}`);
export const putDraft = (name: string, vector: Vector, op: string) => call<{ saved: boolean; valid: boolean }>('PUT', `/drafts/${encodeURIComponent(name)}`, { vector, op });
export const deleteDraft = (name: string) => call<{ deleted: boolean }>('DELETE', `/drafts/${encodeURIComponent(name)}`);
