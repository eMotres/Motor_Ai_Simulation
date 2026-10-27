// Configure's per-machine physical limits as the server states them
// (src/motor_ai_sim/configure_limits.py): GET /api/catalog/{id}/configure_context
// and, admin only, PATCH /api/catalog/{id}/configure_limits.
import type { Preset } from './configuratorPresets';
import type { BearingSpeed } from './configuratorLimits';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

export interface ConfigureContext {
  motor_id: string;
  /** the two limits an admin sets BY HAND per motor: stack length [mm] and speed maximum [rpm] (owner 2026-10-28) */
  limits: { L_max_mm: number | null; rpm_max?: number | null; set_by?: string | null; set_at?: string | null };
  /** the speed limits the machine's records state beside the pack's: the bearings (owner 2026-10-28) */
  speed?: { bearing?: BearingSpeed | null };
  current: {
    set: boolean; reason?: string; device?: string; devices_parallel?: number;
    i_d_rating_A?: number; t_case_c?: number; t_j_max_c?: number; basis?: string;
    /** the transistor's DATASHEET figure as a phase current - information only, never a slider limit */
    i_phase_rms_max_A?: number;
    /** the REAL limits this machine's records state (owner 2026-10-20), at its saved build */
    caps?: {
      demag: { A: number; ref_magnet_C?: number | null; source?: string } | null;
      board: { A: number; device?: string | null; n_parallel?: number; variant?: string | null;
               cooling?: string; source?: string } | null;
    };
  };
  modulation: { m: number; source: 'controller' | 'default' };
  battery: {
    v_max: number | null; v_nom: number | null; v_min: number | null;
    /** series cells + chemistry + per-cell voltages: what the Battery panel seeds from */
    cells?: number | null; chemistry?: string | null;
    v_cell_min?: number | null; v_cell_nom?: number | null; v_cell_max?: number | null;
  } | null;
  has_family_doc: boolean;
  /** what the machine is for (owner 2026-10-13): the configuration's `applications`, first = default;
   *  `[]` = not set (today's behaviour).  `applications_error` names a malformed value. */
  applications?: string[];
  application?: string | null;
  applications_error?: string | null;
  /** what cooling the die may offer (config/cooling_options.yaml) — `propeller_air` for the Ø40 drone motors */
  cooling?: import('./configuratorPropeller').CoolingInfo;
  /** the temperatures a winding / magnet is judged against */
  thermal_limits?: { winding_C?: number | null; magnet_C?: number | null; winding_basis?: string; magnet_card?: string | null;
    /** the winding temperature RATED aims at (owner 2026-10-19: 180 °C), below the class limit `winding_C` */
    rated_winding_C?: number | null };
  /** one per configuration of the machine's die (read from the machines themselves) */
  presets?: Preset[];
}

/** The catalogue id behind a Configure reference (`cat:<id>`), or null for a built-in. */
export const catalogIdOf = (refId: string): string | null =>
  refId.startsWith('cat:') ? refId.slice(4) : null;

export async function fetchConfigureContext(motorId: string): Promise<ConfigureContext | null> {
  try {
    const r = await fetch(`${API}/api/catalog/${encodeURIComponent(motorId)}/configure_context`,
      { cache: 'no-store' });
    return r.ok ? ((await r.json()) as ConfigureContext) : null;
  } catch { return null; }
}

/** Admin: set (number) or clear (null) the stack-length maximum of one machine. */
export const saveLMax = (motorId: string, lMaxMm: number | null): Promise<void> => saveHandLimit(motorId, { L_max_mm: lMaxMm });

/** Admin: set (number) or clear (null) the speed maximum [rpm] of one machine. */
export const saveRpmMax = (motorId: string, rpmMax: number | null): Promise<void> => saveHandLimit(motorId, { rpm_max: rpmMax });

async function saveHandLimit(motorId: string, body: { L_max_mm: number | null } | { rpm_max: number | null }): Promise<void> {
  const r = await fetch(`${API}/api/catalog/${encodeURIComponent(motorId)}/configure_limits`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const b = await r.json().catch(() => ({}));
    throw new Error(typeof b?.detail === 'string' ? b.detail : `HTTP ${r.status}`);
  }
}
