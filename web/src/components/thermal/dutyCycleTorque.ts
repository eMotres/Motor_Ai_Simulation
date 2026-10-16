/**
 * The TORQUE the duty cycle asks the shaft for (2026-09-15).
 *
 * The cycle editor answers "how hot does it get".  The other half of the same
 * question — what the machine is DELIVERING while it gets there — was nowhere
 * on the panel: a robot integrator sizing a gearbox reads the peak torque and
 * the time-weighted mean off the cycle, and both were only implicit in the duty
 * names printed under the segments.
 *
 * WHERE THE TORQUE COMES FROM.  Not from the cycle result — the thermal solver
 * is told losses and speeds, never a torque.  It comes from the CONFIGURATION's
 * duty entries (`/api/family/tree`: each duty's `torque_nm`, the same number
 * the catalog row prints), and a duty carrying none falls back to its run's own
 * 2-D mean times the 3-D end-effect factor, which is the arithmetic every shaft
 * power in this project is built on.
 *
 * A NAMED DUTY WHOSE TORQUE NOBODY STORED IS NOT A ZERO.  The profile is
 * refused instead, with the names said out loud, because a step drawn at zero
 * for a duty that pulls 7 N·m is a wrong answer that looks like an answer.  An
 * UNPOWERED segment is a real 0 N·m and is drawn as one.
 *
 * Dependency-free on purpose — no React, no `import.meta.env` — so node's own
 * type stripping can run the test beside it (see `dutyCycleOffer.ts`).
 */

/** A duty as the family tree describes it — the two fields this module reads. */
export interface TorqueDuty {
  name?: string | null;
  /** the configuration's own target/rated torque for this point, N·m */
  torque_nm?: number | null;
  /** the run summary, when a caller has one: `T_em_avg_Nm` × `end3d.k_flux` */
  summary?: Record<string, unknown> | null;
}

/** One segment of the solved cycle — `result.spec.segments`. */
export interface TorqueSegment {
  duty?: string | null;
  t_s?: number | null;
}

/** One step of the profile: `[t0, t1)` at `nm`, run by `duty`. */
export interface TorqueStep {
  t0: number;
  t1: number;
  nm: number;
  duty: string;
}

export interface TorqueProfile {
  /** the step line, as recharts eats it (`type="stepAfter"`) */
  rows: { t: number; nm: number }[];
  steps: TorqueStep[];
  /** time-weighted over the whole span — the number the chip prints */
  meanNm: number;
  peakNm: number;
  spanS: number;
}

const num = (v: unknown): number | null => {
  if (v === null || v === undefined || v === '' || typeof v === 'boolean') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

/** This duty's shaft torque, N·m — its own number first, its run's second. */
export function dutyTorqueNm(d: TorqueDuty | null | undefined): number | null {
  const own = num(d?.torque_nm);
  if (own !== null) return own;
  const s = d?.summary;
  if (!s || typeof s !== 'object') return null;
  const t2d = num((s as Record<string, unknown>).T_em_avg_Nm);
  if (t2d === null) return null;
  const e3 = (s as Record<string, unknown>).end3d;
  const k = (e3 && typeof e3 === 'object')
    ? num((e3 as Record<string, unknown>).k_flux) : null;
  return t2d * (k ?? 1);
}

/** `{ name: N·m }` for every duty of the configuration that states one. */
export function torqueByDuty(duties: TorqueDuty[] | null | undefined
                            ): Record<string, number> {
  const out: Record<string, number> = {};
  for (const d of (duties ?? [])) {
    const n = String(d?.name ?? '').trim();
    if (!n) continue;
    const t = dutyTorqueNm(d);
    if (t !== null) out[n] = t;
  }
  return out;
}

/** The duties this cycle names but nobody stored a torque for. */
export function missingTorques(segments: TorqueSegment[] | null | undefined,
                               torques: Record<string, number>): string[] {
  const out: string[] = [];
  for (const s of (segments ?? [])) {
    const n = String(s?.duty ?? '').trim();
    if (n && !(n in torques) && !out.includes(n)) out.push(n);
  }
  return out;
}

/**
 * The torque step over one cycle, on the SAME time axis as T(t).
 *
 * `spanS` (the cycle result's own `t_s` span) decides where the axis ends: the
 * segments are laid end to end from zero, the last one is held out to the end
 * of the span when they fall short, and anything past it is clipped — the two
 * charts must not disagree about how long the cycle is.
 */
export function torqueProfile(segments: TorqueSegment[] | null | undefined,
                              torques: Record<string, number>,
                              spanS?: number | null,
                              samplesS?: number[] | null): TorqueProfile | null {
  const segs = (segments ?? []).filter((s) => (num(s?.t_s) ?? 0) > 0);
  if (!segs.length) return null;
  if (missingTorques(segs, torques).length) return null;
  const steps: TorqueStep[] = [];
  let t = 0;
  for (const s of segs) {
    const name = String(s?.duty ?? '').trim();
    const nm = name ? torques[name] : 0;
    const d = num(s.t_s) as number;
    steps.push({ t0: t, t1: t + d, nm, duty: name || 'unpowered' });
    t += d;
  }
  const end = (num(spanS) ?? 0) > 0 ? (num(spanS) as number) : t;
  // clip to the axis the temperature chart is drawn on, and hold the last
  // segment out to it when the segments stop short
  const kept: TorqueStep[] = [];
  for (const st of steps) {
    if (st.t0 >= end) break;
    kept.push({ ...st, t1: Math.min(st.t1, end) });
  }
  if (!kept.length) return null;
  kept[kept.length - 1].t1 = end;
  /** The step, read at any instant: the segment that CONTAINS t. */
  const at = (t: number): number => {
    for (const st of kept) if (t >= st.t0 && t < st.t1) return st.nm;
    return kept[kept.length - 1].nm;
  };
  // `stepAfter`: each point holds until the next one, so the segment starts
  // alone would draw the right picture — but a chart is also READ, and three
  // points mean the hover can only answer about three instants (and a shared
  // crosshair with T(t) would have nothing to line up with).  So the rows are
  // the segment boundaries UNION the cycle's own sample grid, each carrying
  // the value of the segment it falls in: the same step, answerable anywhere.
  const marks = new Set<number>();
  for (const st of kept) { marks.add(st.t0); marks.add(st.t1); }
  for (const t of (samplesS ?? [])) {
    const v = num(t);
    if (v !== null && v >= kept[0].t0 && v <= end) marks.add(v);
  }
  const rows = [...marks].sort((a, b) => a - b).map((t) => ({ t, nm: at(t) }));
  const span = end - kept[0].t0;
  const meanNm = span > 0
    ? kept.reduce((a, s) => a + s.nm * (s.t1 - s.t0), 0) / span
    : kept[0].nm;
  return {
    rows,
    steps: kept,
    meanNm,
    peakNm: kept.reduce((a, s) => Math.max(a, s.nm), kept[0].nm),
    spanS: span,
  };
}
