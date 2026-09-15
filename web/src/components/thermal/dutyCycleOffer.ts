/**
 * When a DUTY CYCLE has no Electromagnetic run at its CALIBRATION point.
 *
 * `POST /api/thermal/duty_cycle` fits its four-node network to ONE steady map,
 * and that map is solved at the CALIBRATION duty's own stored point — the rpm,
 * the current, the angle and the coil temperature that duty was saved with
 * (`routes/thermal.py`, the `point = {…}` block).  With no Electromagnetic run
 * at exactly that point, coil temperature and frame count, the route refuses by
 * name: `no_electromagnetic_run`.
 *
 * The Thermal tab's own field Solve has answered that refusal since 2026-09-08
 * by making the run itself through the orchestrator (`stores/thermalStore`,
 * `emRunThroughLoop`).  The duty-cycle editor could not: its point is NOT the
 * one on the Electromagnetic tab — it belongs to another duty entirely — so a
 * run made from that tab's fields would be the wrong run, silently, after
 * minutes.  This module is the difference:
 *
 *   • `calibrationPoint` reads the point out of the calibration duty's STORED
 *     entry, mirroring the route's own `_pt` field-by-field;
 *   • `emRunBodyAt` pins it onto the Electromagnetic run payload, overriding
 *     every field the builder would otherwise have read from the Electromagnetic
 *     tab's persisted settings;
 *   • `offerReduce` is the state machine behind the one line under the Run
 *     button: the run costs minutes and is NOT the point on screen, so it is
 *     OFFERED and never taken silently.
 *
 * Deliberately DEPENDENCY-FREE.  Everything here is pure (the one fetch takes
 * its base URL as an argument and uses the ambient `fetch`), so
 * `__tests__/dutyCycleOffer.test.mjs` imports this very module under
 * `node --test` instead of keeping a second copy of its rules in sync by hand.
 */

/** The calibration duty's own operating point, exactly as the duty-cycle route
 *  reconstructs it from the stored entry.  Every number here comes from the
 *  CATALOGUE — never from the Electromagnetic tab's live fields. */
export interface CalibrationPoint {
  /** which duty this is the point of */
  duty: string;
  rpm: number;
  I_phase_rms: number;
  gamma_deg: number;
  coil_temp_c: number;
  /** the terminal connection the duty was saved with; null = not stated */
  star_delta: string | null;
  /** 'motor' / 'generator'; null = not stated */
  mode: string | null;
}

/** The mesh block, as the cycle request sends it (`thermal/api.meshParams`).
 *  Structurally `ThermalMeshRequest`; restated here so this module imports
 *  nothing. */
export interface EmRunMesh {
  mesh_size_mm: number;
  min_size_mm: number;
  outer_air_factor: number;
  n_sectors: number;
  component_mesh: string;
}

/** Everything the orchestrator run must be pinned to, so the run that gets MADE
 *  is the run the duty-cycle route will LOOK FOR. */
export interface EmRunAt {
  point: CalibrationPoint;
  /** frames per electrical period — the cycle request's own `n_steps_per_period`
   *  (the loss map is looked up by it, so a run at another count is a miss) */
  n_steps_per_period: number;
  /** the mesh the cycle request sends; null = leave the payload's own */
  mesh: EmRunMesh | null;
  /** what the progress strip says this run is for */
  label?: string;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * The point, out of the stored duty entry
 * ═══════════════════════════════════════════════════════════════════════════ */

/**
 * Mirrors `routes/thermal.py`'s `_pt`: the DUTY ENTRY's own key first, then the
 * named fallbacks in its `summary`, and the default when neither states a
 * usable number.  A value that is present but unreadable STOPS the search (the
 * Python `float()` raises and breaks out of the loop) rather than falling
 * through to the next source — a duty whose rpm is the string "fast" must not
 * quietly borrow the summary's.
 */
function pt(entry: Record<string, unknown>, summary: Record<string, unknown>,
            key: string, alts: string[], def: number): number {
  const sources: [Record<string, unknown>, string][] =
    [[entry, key], ...alts.map((a) => [summary, a] as [Record<string, unknown>, string])];
  for (const [src, k] of sources) {
    const v = src[k];
    if (v === null || v === undefined) continue;
    const n = v === '' ? NaN : Number(v);
    if (!Number.isFinite(n)) break;
    return n;
  }
  return def;
}

const str = (v: unknown): string | null => {
  const s = typeof v === 'string' ? v.trim() : '';
  return s === '' ? null : s;
};

/** The point the calibration map will be solved at, from the duty's stored
 *  entry (`GET /api/family/payload/{die}/{cfg}?duty=…` → `duty`). */
export function calibrationPoint(
  duty: string, entry: Record<string, unknown> | null | undefined,
): CalibrationPoint {
  const e = (entry ?? {}) as Record<string, unknown>;
  const s = (e.summary && typeof e.summary === 'object'
    ? e.summary : {}) as Record<string, unknown>;
  const coil = s.coil_temp_C;
  const coilN = (coil === null || coil === undefined || coil === '')
    ? NaN : Number(coil);
  return {
    duty,
    rpm: pt(e, s, 'rpm', ['rpm'], 0),
    I_phase_rms: pt(e, s, 'current_arms', ['I_phase_rms_A'], 0),
    gamma_deg: pt(e, s, 'gamma_deg', ['gamma_deg'], 0),
    // `float(cal_summary.get("coil_temp_C") or 120.0)` — the route's own line,
    // including the `or`, which reads a 0 °C copper as "not stated".  Copied
    // rather than improved: the temperature the run is made at has to be the
    // temperature the lookup asks for, bug for bug.
    coil_temp_c: (Number.isFinite(coilN) && coilN !== 0) ? coilN : 120,
    star_delta: str(e.star_delta) ?? str(s.star_delta),
    mode: str(e.mode) ?? str(s.op_mode),
  };
}

/** "14.7 A, 1 000 rpm, coil 72.5 °C" — the offer line's parenthesis. */
export function pointWords(p: CalibrationPoint): string {
  const rpm = Math.round(p.rpm).toLocaleString('en-US').replace(/,/g, ' ');
  return `${p.I_phase_rms.toFixed(1)} A, ${rpm} rpm, `
    + `coil ${p.coil_temp_c.toFixed(1)} °C`;
}

/** The whole point, for the tooltip — the angle and the connection too. */
export function pointTooltipWords(p: CalibrationPoint): string {
  return [pointWords(p), `γ ${p.gamma_deg.toFixed(1)}°`,
    p.star_delta ? (p.star_delta === 'delta' ? 'delta' : 'star') : null,
    p.mode && p.mode !== 'motor' ? p.mode : null].filter(Boolean).join(', ');
}

/** Read the calibration duty's stored point.  A LOOKUP — it solves nothing and
 *  writes nothing.  `base` is the API root; the ambient `fetch` carries this
 *  app's auth interceptor (and lets a test mock it). */
export async function fetchCalibrationPoint(
  base: string, die: string, config: string, duty: string,
): Promise<CalibrationPoint> {
  const url = `${String(base).replace(/\/$/, '')}/api/family/payload/`
    + `${encodeURIComponent(die)}/${encodeURIComponent(config)}`
    + `?duty=${encodeURIComponent(duty)}`;
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) {
    throw new Error(`the calibration duty "${duty}" could not be read `
      + `(HTTP ${r.status})`);
  }
  const j = await r.json() as { duty?: Record<string, unknown> } | null;
  const entry = j?.duty;
  if (!entry || typeof entry !== 'object') {
    throw new Error(`the calibration duty "${duty}" has no stored entry, so `
      + 'there is no operating point to make a run at');
  }
  return calibrationPoint(duty, entry);
}

/* ═══════════════════════════════════════════════════════════════════════════
 * The run body
 * ═══════════════════════════════════════════════════════════════════════════ */

/**
 * Pin the calibration point onto an Electromagnetic run payload.
 *
 * `lib/emRunPayload.buildEmRunPayload` reads the speed, the coil temperature,
 * the magnet temperature, the operating mode, the terminal connection and the
 * whole mesh block from where the ELECTROMAGNETIC TAB persists them.  That is
 * right for every other caller and wrong for this one: the map this run feeds
 * is solved at another duty's point.  So every one of those fields is replaced
 * here, and the ones the cycle request does NOT send are REMOVED — the loss map
 * is looked up by an identity that includes the magnet temperature, and a run
 * carrying the Electromagnetic tab's magnet field would be a miss the user pays
 * minutes to discover.
 */
export function emRunBodyAt(
  base: Record<string, unknown>, at: EmRunAt,
): Record<string, unknown> {
  const p = at.point;
  const out: Record<string, unknown> = {
    ...base,
    I_phase_rms: p.I_phase_rms,
    gamma_deg: p.gamma_deg,
    coil_temp_c: p.coil_temp_c,
    n_steps_per_period: at.n_steps_per_period,
    // Same key the Electromagnetic tab's own Run sends, for the same reason:
    // both panels must hit one backend cache entry, not two.
    n_frames: at.n_steps_per_period,
    // The duty's own connection, else whatever the payload already carried.
    ...(p.star_delta ? { star_delta: p.star_delta } : {}),
    // The route resolves the calibration map's mode from the DUTY
    // (`cal_entry["mode"]`), so the run is made in that mode — 'motor' when the
    // entry does not say, which is what the catalogue stores for every duty.
    mode: p.mode ?? 'motor',
  };
  // SPEED.  Sent when the duty states one; when it does not, the key is dropped
  // entirely rather than left at the Electromagnetic tab's value, and the
  // backend falls back to the shared configuration — the same speed its own
  // loss-map probe resolves.
  if (p.rpm > 0) out.rpm = p.rpm;
  else delete out.rpm;
  // MAGNET TEMPERATURE.  The cycle request sends none, so the map is looked up
  // with `magnet_temp_c = None`; a run stored at the Electromagnetic tab's
  // magnet field would carry a different physics identity and never match.
  delete out.magnet_temp_c;
  if (at.mesh) {
    out.mesh_size_mm = at.mesh.mesh_size_mm;
    out.min_size_mm = at.mesh.min_size_mm;
    out.outer_air_factor = at.mesh.outer_air_factor;
    out.n_sectors = at.mesh.n_sectors;
    out.component_mesh = at.mesh.component_mesh;
  }
  return out;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * The offer
 *
 * NEVER silent.  The run is minutes long and it is made at a point that is not
 * the one on the Electromagnetic tab, so the editor asks first — and asks ONCE:
 * `attempted` makes a second `no_electromagnetic_run` after a completed run a
 * plain refusal, not another offer, so a machine that cannot be satisfied
 * cannot cost two runs.
 * ═══════════════════════════════════════════════════════════════════════════ */

export type OfferPhase = 'idle' | 'offered' | 'running';

export interface OfferState {
  phase: OfferPhase;
  /** the refusal's own sentence, shown verbatim when the offer is declined */
  why: string | null;
  point: CalibrationPoint | null;
  /** Stop was pressed and the loop is winding down */
  stopping: boolean;
  /** a run has already been made for this Run press */
  attempted: boolean;
}

export const OFFER_IDLE: OfferState = {
  phase: 'idle', why: null, point: null, stopping: false, attempted: false,
};

export type OfferAction =
  /** a fresh Run cycle press */
  | { type: 'run' }
  /** the route refused for want of an Electromagnetic run, and this is the
   *  point it would have to be made at */
  | { type: 'refused'; why: string; point: CalibrationPoint }
  /** [Make the run] */
  | { type: 'accept' }
  /** [Cancel] — the offer is declined and the refusal stands */
  | { type: 'cancel' }
  /** [Stop] — the orchestrator run is cancelled by run-id */
  | { type: 'stop' }
  /** the run ended, however it ended */
  | { type: 'settled' };

export function offerReduce(s: OfferState, a: OfferAction): OfferState {
  switch (a.type) {
    case 'run':
      // A run in flight is not restarted by a Run press — the button is
      // disabled meanwhile, and this is the second lock on that door.
      return s.phase === 'running' ? s : { ...OFFER_IDLE };
    case 'refused':
      // A refusal cannot arrive while the run it would offer is in flight —
      // the editor sends nothing meanwhile — and if one did, it is stale.
      if (s.phase === 'running') return s;
      // Already answered once for this press: the refusal is the answer now,
      // shown verbatim.  A second offer would cost a second run for the same
      // words.
      if (s.attempted) {
        return { ...s, phase: 'idle', why: a.why, point: a.point,
                 stopping: false };
      }
      return { phase: 'offered', why: a.why, point: a.point, stopping: false,
               attempted: s.attempted };
    case 'accept':
      if (s.phase !== 'offered') return s;
      return { ...s, phase: 'running', stopping: false, attempted: true };
    case 'cancel':
      if (s.phase !== 'offered') return s;
      // `why` survives: the editor shows the refusal it just declined.
      return { ...s, phase: 'idle', point: s.point, stopping: false };
    case 'stop':
      if (s.phase !== 'running' || s.stopping) return s;
      return { ...s, stopping: true };
    case 'settled':
      return { phase: 'idle', why: null, point: null, stopping: false,
               attempted: s.attempted };
    default:
      return s;
  }
}

/** Is the offer line on screen? */
export const isOffering = (s: OfferState): boolean =>
  s.phase === 'offered' && s.point !== null;
