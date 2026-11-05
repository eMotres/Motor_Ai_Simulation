/** THE one definition of Kv and Kt on the site (owner 2026-11-05); Python mirror: `motor_ai_sim/kv_kt.py`.
 *
 *  - Kv = rpm per volt of the line-line PEAK of the FUNDAMENTAL back-EMF at no load.
 *  - Kt = N·m per A rms of PHASE current, sine current.  At no load Kt = (3/√2)·30 / (√3·π·Kv) = KT_TIMES_KV / Kv
 *    (about 11.695 / Kv), so the two always agree.
 *  - T / I at a load point is a different quantity ("Kt at load"), never "Kt".
 *  - The waveform peak of a voltage is a voltage, never Kv. */

/** Kt · Kv, (N·m/A rms)·(rpm/V line-line peak). */
export const KT_TIMES_KV = (3 / Math.SQRT2) * 30 / (Math.sqrt(3) * Math.PI);

/** Kt per A rms of phase (winding) current from Kv; in delta the winding carries 1/√3 of the line current, so the
 *  per-winding Kt is √3 larger.  null when Kv is missing or not positive. */
export function ktFromKv(kv: number | null | undefined, delta = false): number | null {
  if (typeof kv !== 'number' || !Number.isFinite(kv) || kv <= 0) return null;
  return (KT_TIMES_KV / kv) * (delta ? Math.sqrt(3) : 1);
}

/** Kt per A rms of LINE current: the star formula in both connections. */
export function ktPerLineAmp(kv: number | null | undefined): number | null {
  return ktFromKv(kv, false);
}

/** The inverse. */
export function kvFromKt(kt: number | null | undefined, delta = false): number | null {
  if (typeof kt !== 'number' || !Number.isFinite(kt) || kt <= 0) return null;
  return (KT_TIMES_KV / kt) * (delta ? Math.sqrt(3) : 1);
}
