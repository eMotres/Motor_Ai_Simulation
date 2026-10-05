// Configure's per-machine physical limits as the server states them
// (src/motor_ai_sim/configure_limits.py): GET /api/catalog/{id}/configure_context
// and, admin only, PATCH /api/catalog/{id}/configure_limits.
const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

export interface ConfigureContext {
  motor_id: string;
  limits: { L_max_mm: number | null; set_by?: string | null; set_at?: string | null };
  current: {
    set: boolean; reason?: string; device?: string; devices_parallel?: number;
    i_d_rating_A?: number; t_case_c?: number; t_j_max_c?: number; basis?: string;
    i_phase_rms_max_A?: number;
  };
  modulation: { m: number; source: 'controller' | 'default' };
  battery: { v_max: number | null; v_nom: number | null; v_min: number | null } | null;
  has_family_doc: boolean;
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
export async function saveLMax(motorId: string, lMaxMm: number | null): Promise<void> {
  const r = await fetch(`${API}/api/catalog/${encodeURIComponent(motorId)}/configure_limits`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ L_max_mm: lMaxMm }),
  });
  if (!r.ok) {
    const b = await r.json().catch(() => ({}));
    throw new Error(typeof b?.detail === 'string' ? b.detail : `HTTP ${r.status}`);
  }
}
