/**
 * The Electromagnetic run the Thermal tab uses — how it is NAMED on screen.
 *
 * Owner rule (2026-09-30): the Thermal tab always takes the LATEST
 * Electromagnetic run of the loaded machine (`routes/thermal.latest_em_run`)
 * and adopts that run's point and settings; it keeps no copy of its own.  The
 * panel names the run in ONE line, details in a tooltip:
 *
 *     EM: 81 A · γ 10° · 25 000 rpm · 36 steps · 11:50
 *
 * Dependency-free on purpose (no React, no `import.meta.env`), so node's own
 * type stripping can load it in `__tests__/emRun.test.mjs`.
 */

/** `latest_em_run(...)["summary"]` — the fields the backend sends. */
export interface EmRunSummary {
  run_id?: string | null;
  /** ISO stamp WITH its offset (the backend converts the server's local time) */
  computed_at?: string | null;
  I_phase_rms: number;
  /** the PANEL's angle (a generator run is keyed on γ + 180°; this is not) */
  gamma_deg: number;
  rpm: number;
  coil_temp_c: number;
  magnet_temp_c?: number | null;
  n_steps_per_period: number;
  n_periods?: number;
  mesh_size_mm?: number;
  min_size_mm?: number;
  n_sectors?: number;
  op_mode?: string;
  demag?: boolean;
  drive?: string;
  solve_time_s?: number | null;
  /** false = a run stored before runs recorded their machine inputs */
  inputs_recorded?: boolean;
}

/** `GET /api/thermal/em_run` — the run the next Solve will use, or the refusal
 *  Solve would give (one line + its reason). */
export interface EmRunStatus {
  ok: boolean;
  em_run?: EmRunSummary | null;
  error?: string | null;
  reason?: string | null;
  error_code?: string | null;
  em_refusal?: string | null;
}

const isNum = (v: unknown): v is number =>
  typeof v === 'number' && Number.isFinite(v);

/** A number with at most `d` decimals and no trailing zeros ("10", "10.5"). */
function short(v: number, d: number): string {
  const s = v.toFixed(d);
  return s.includes('.') ? s.replace(/\.?0+$/, '') : s;
}

/** Thousands with a thin space, the way the rest of this app prints rpm. */
function thousands(v: number): string {
  return String(Math.round(v)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
}

/** "11:50" in the viewer's own clock, or null for a stamp that does not parse. */
export function emRunClock(stamp: string | null | undefined): string | null {
  if (!stamp) return null;
  const t = Date.parse(stamp);
  if (!Number.isFinite(t)) return null;
  const d = new Date(t);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${hh}:${mm}`;
}

/** THE one line: "EM: 81 A · γ 10° · 25 000 rpm · 36 steps · 11:50". */
export function emRunLine(s: EmRunSummary | null | undefined): string | null {
  if (!s || !isNum(s.I_phase_rms)) return null;
  const parts = [
    `${short(s.I_phase_rms, 0)} A`,
    `γ ${short(isNum(s.gamma_deg) ? s.gamma_deg : 0, 1)}°`,
    `${thousands(isNum(s.rpm) ? s.rpm : 0)} rpm`,
    `${isNum(s.n_steps_per_period) ? s.n_steps_per_period : '?'} steps`,
  ];
  const clock = emRunClock(s.computed_at ?? null);
  if (clock) parts.push(clock);
  return `EM: ${parts.join(' · ')}`;
}

/** The tooltip: every setting the map was solved with, in plain words. */
export function emRunTooltip(s: EmRunSummary | null | undefined): string {
  if (!s) return '';
  const bits: string[] = [
    `Loss map from the latest Electromagnetic run of this machine${s.computed_at ? ` (${s.computed_at})` : ''}.`,
    `Current ${short(s.I_phase_rms, 2)} A rms, γ ${short(s.gamma_deg, 2)}°, ${thousands(s.rpm)} rpm${s.op_mode ? `, ${s.op_mode}` : ''}.`,
    `Coil ${short(s.coil_temp_c, 1)} °C, magnets ${isNum(s.magnet_temp_c) ? `${short(s.magnet_temp_c, 1)} °C` : 'at the card temperature'}.`,
    `${s.n_steps_per_period} steps/period × ${isNum(s.n_periods) ? short(s.n_periods, 2) : 1} period, mesh ${isNum(s.mesh_size_mm) ? short(s.mesh_size_mm, 2) : '?'} mm (min ${isNum(s.min_size_mm) ? short(s.min_size_mm, 2) : '?'} mm), ${isNum(s.n_sectors) && s.n_sectors > 1 ? `1/${s.n_sectors} sector` : 'full ring'}, demag ${s.demag ? 'on' : 'off'}${s.drive && s.drive !== 'current' ? `, ${s.drive} drive` : ''}.`,
    'Thermal takes all of these from that run and never runs Electromagnetic itself — change them there and Run.',
  ];
  return bits.join(' ');
}

/** The run's point in the shape the local comparison row reads
 *  (`compare/resultRows.localThermalRow`'s `op`), or null. */
export function emRunPoint(s: EmRunSummary | null | undefined): {
  rpm: number; gamma_deg: number; I_phase_rms: number;
  coil_temp_c: number; n_steps_per_period: number;
} | null {
  if (!s || !isNum(s.I_phase_rms)) return null;
  return {
    rpm: s.rpm, gamma_deg: s.gamma_deg, I_phase_rms: s.I_phase_rms,
    coil_temp_c: s.coil_temp_c, n_steps_per_period: s.n_steps_per_period,
  };
}
