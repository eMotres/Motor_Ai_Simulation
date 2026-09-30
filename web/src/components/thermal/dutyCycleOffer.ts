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
 * The duty-cycle editor used to answer that refusal by OFFERING to make the
 * run itself, through the orchestrator, at the calibration duty's point.  That
 * offer is GONE (owner rule, 2026-09-07, repeated 2026-09-30: this app never
 * launches electromagnetics from the thermal side — the current Electromagnetic
 * result is used, or the user is told to go make it).  What is left of this
 * module is only the SENTENCE:
 *
 *   • `calibrationPoint` reads the point out of the calibration duty's STORED
 *     entry, mirroring the route's own `_pt` field-by-field, so the notice can
 *     say WHICH point (current, rpm, coil temperature) is missing;
 *   • `pointWords` / `pointTooltipWords` say it in the one line and the
 *     tooltip;
 *   • `fetchCalibrationPoint` is a LOOKUP — it reads the calibration duty's
 *     entry and writes nothing, never runs anything.
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

/* There is no run body and no offer state machine here any more — see the
 * file header.  `calibrationPoint` / `pointWords` / `pointTooltipWords` /
 * `fetchCalibrationPoint` above are the whole of what is left. */
