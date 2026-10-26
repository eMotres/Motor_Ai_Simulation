// The magnetic pull of the air-gap field on the rotor (2026-10-26) — the
// Mechanical tab's one control turned into the request's query parameters.
//
// `none` sends NOTHING, so a request without a pull is byte for byte the one
// this app has always made (the backend keys its cache on the pull only when
// it is given).  `em` reads the duty's own stored EM field server-side.
// `uniform` is an explicit estimate and needs a pressure: an empty or invalid
// box is refused here, by name, rather than sent as a 0 or a NaN.

export type PullChoice = 'none' | 'em' | 'uniform';

/** Bounds the backend enforces too: 5 MPa is B = 3.5 T in the gap. */
export const PULL_MPA_MAX = 5;

/** null when `text` is a usable uniform pressure, else the reason. */
export function pullMpaError(text: string): string | null {
  const t = String(text ?? '').trim();
  if (t === '') return 'empty';
  const v = Number(t);
  if (!Number.isFinite(v) || v <= 0 || v > PULL_MPA_MAX) return 'range';
  return null;
}

export function pullParams(pull: PullChoice, pullMpa: string):
    { pull?: 'em' | 'uniform'; pull_mpa?: number } {
  if (pull === 'em') return { pull: 'em' };
  if (pull === 'uniform') {
    if (pullMpaError(pullMpa) !== null) {
      throw new Error(`Magnetic pull: enter the pressure in MPa (0 < p ≤ ${PULL_MPA_MAX})`);
    }
    return { pull: 'uniform', pull_mpa: Number(String(pullMpa).trim()) };
  }
  return {};
}
