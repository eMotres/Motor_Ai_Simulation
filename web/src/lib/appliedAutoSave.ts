// Every design applied from the Optimization tab is archived as a NEW motor,
// automatically, at the moment it is applied.
//
// Why this exists: an applied design lived ONLY in the editor's config until
// somebody remembered to press Save.  A backend restart threw away a full day of
// optimization exactly that way.  "The user will save it" is not a mechanism —
// the apply itself is, so the apply does it.
//
// Rules this helper keeps:
//   * a NEW motor every time — the source motor and the editor's active preset
//     are never written (the backend mints the id, see /api/presets/from_applied);
//   * the geometry filed is the one that ACTUALLY LANDED in config, compared
//     field by field against the overrides the apply asked for, so a value the
//     geometry validator clamped is reported instead of filed silently;
//   * a failed save is LOUD.  The caller shows the reason; the apply stands.

import { getActiveMotor, readMeshSettings, readSimSettings } from '../components/common/motorSettings';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

/** Which apply produced the design — goes into the motor's NAME and provenance. */
export type ApplyMode = 'auto' | 'cmaes' | 'screen' | 'descent' | 'sweep' | 'picked';

export interface ApplyProvenance {
  mode: ApplyMode;
  runId?: string;
  objective?: string;
  operatingPoint?: { current_a?: number; rpm?: number; gamma_deg?: number };
  rippleMaxPct?: number;
  /** Signed perpendicular distance above the current-only baseline line. */
  F?: number;
  /** The applied point's own numbers (torque, ripple, efficiency, mass, td…). */
  metrics?: Record<string, unknown>;
  /** The knobs the apply wrote — checked server-side against what landed. */
  overrides?: Record<string, unknown>;
}

export interface AppliedSaveResult {
  ok: boolean;
  id?: string;
  name?: string;
  error?: string;
  /** Fields the geometry validator clamped: applied ≠ saved. */
  deviations?: { field: string; applied: number; saved: number }[];
}

const num = (v: unknown): number | undefined => {
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
};

/** Only the finite numbers — a NaN in a provenance field is not evidence. */
function numbersOnly(o?: Record<string, unknown>): Record<string, number> | undefined {
  if (!o) return undefined;
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(o)) { const n = num(v); if (n !== undefined) out[k] = n; }
  return Object.keys(out).length ? out : undefined;
}

/**
 * Archive the design that was just applied.  NEVER throws: an archiving failure
 * must not turn into an exception inside an apply handler that has already
 * succeeded — it is returned, and the card shows it.
 */
export async function autoSaveAppliedDesign(prov: ApplyProvenance): Promise<AppliedSaveResult> {
  const active = getActiveMotor();
  const body = {
    mode: prov.mode,
    source_name: active?.name ?? '',
    source_id: active?.id ?? '',
    run_id: prov.runId ?? '',
    objective: prov.objective ?? '',
    operating_point: prov.operatingPoint
      ? {
          current_a: num(prov.operatingPoint.current_a),
          rpm: num(prov.operatingPoint.rpm),
          gamma_deg: num(prov.operatingPoint.gamma_deg),
        }
      : undefined,
    ripple_max_pct: num(prov.rippleMaxPct),
    f_above_baseline: num(prov.F),
    metrics: numbersOnly(prov.metrics),
    overrides: numbersOnly(prov.overrides),
    // Geometry is NOT sent: the backend snapshots the config the apply landed
    // in, which is the only version of the truth both sides can check.
    mesh: readMeshSettings(),
    simulation: readSimSettings(),
  };
  let res: AppliedSaveResult;
  try {
    const r = await fetch(`${API}/api/presets/from_applied`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const j = await r.json().catch(() => null);
    if (!r.ok) {
      const detail = typeof j?.detail === 'string' ? j.detail : JSON.stringify(j?.detail ?? '');
      throw new Error(detail || `HTTP ${r.status}`);
    }
    res = { ok: true, id: j?.id, name: j?.name, deviations: j?.deviations ?? [] };
  } catch (e: unknown) {
    res = { ok: false, error: String((e as Error)?.message ?? e) };
  }
  // The Motors tab refreshes its cards on this; the panels show the one line.
  try {
    window.dispatchEvent(new CustomEvent('applied-design-saved', { detail: res }));
  } catch { /* SSR/no-window */ }
  return res;
}

/** "saved as “X” · Motors" / the failure, in one line — see the panels. */
export function appliedSaveLine(r: AppliedSaveResult | null): string {
  if (!r) return '';
  if (!r.ok) return `NOT SAVED: ${r.error ?? 'unknown error'} — save it by hand`;
  const dev = r.deviations?.length ? ` · ${r.deviations.length} value(s) clamped` : '';
  return `saved as “${r.name}” · Motors${dev}`;
}
