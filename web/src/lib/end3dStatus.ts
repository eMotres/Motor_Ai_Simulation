/** The Stage A 2-D check status of a run's 3-D factor (end3d.k_flux_status; twin of end3d_verdict.STATUSES), as one
 *  short label + tooltip.  verified = the end-deficit check passed; unverified = it could not be checked (one usable stack
 *  length, a lent factor) or the passport predates the check; failed = 3-D and 2-D disagree; invalid = no 2-D ratio,
 *  k_flux_self applied; fallback = k_flux itself was not a usable factor, k_flux_self applied; recompute = an older run
 *  that cannot be read safely, k_flux_self applied until Stage A is rerun.  Absent (older passports) -> null. */
export type End3dStatus = 'verified' | 'unverified' | 'failed' | 'invalid' | 'fallback' | 'recompute';

export const END3D_STATUSES: readonly End3dStatus[] = ['verified', 'unverified', 'failed', 'invalid', 'fallback', 'recompute'];
const KNOWN = END3D_STATUSES;

export function end3dStatusOf(end3d: { k_flux_status?: unknown } | null | undefined): End3dStatus | null {
  const s = String(end3d?.k_flux_status ?? '').trim().toLowerCase();
  return (KNOWN as readonly string[]).includes(s) ? (s as End3dStatus) : null;
}

/** locale keys (namespace "results") and the colour of the label */
export function end3dStatusView(s: End3dStatus): { labelKey: string; tipKey: string; color: string } {
  const color = s === 'verified' ? 'var(--text-2)'
    : (s === 'unverified' || s === 'fallback' || s === 'recompute') ? '#fbbf24' : '#f87171';
  return { labelKey: `end3dStatus_${s}`, tipKey: `end3dStatusTip_${s}`, color };
}
