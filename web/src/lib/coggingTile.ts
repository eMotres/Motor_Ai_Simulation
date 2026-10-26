/** The cogging-torque tile, shared by every screen that draws a motor's parameters (owner 2026-10-26): the points-limited
 *  Configure grid (`modules/development/configureGrid.ts`), the ordinary Configure tile (`ConfiguratorPanel`), the project
 *  comparison and the Motors row.  Pure functions: `node --test` imports this file.
 *
 *  Headline = peak-to-peak no-load torque in N·m; the tooltip carries the half-amplitude, the share of the continuous
 *  torque, the magnet temperature, the method and (amber) a staleness sentence.  Nothing is drawn without a value. */

/** What the server's `cogging.view` sends for a configuration (`GET /api/simulation/cogging/{die}/{cfg}`, the family tree's
 *  `cogging`, the references' `cogging`). */
export interface CoggingView {
  pkpk_Nm: number;
  half_amp_Nm?: number | null;
  pct_of_continuous?: number | null;
  magnet_temp_c?: number | null;
  fundamental_order?: number | null;
  method?: string | null;
  computed_at?: string | null;
  stale?: boolean;
}

export interface TipPart { key: string; args?: Record<string, string | number> }

/** Digits for a cogging value: it spans 0.001 N·m (small motors) to a few N·m (large ones). */
export const coggingDigits = (v: number): number => (Math.abs(v) >= 10 ? 2 : Math.abs(v) >= 1 ? 3 : 4);

const finite = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** A stored view that can be drawn: a finite, non-zero peak-to-peak.  (No zero / empty rows.) */
export function coggingValue(view: Pick<CoggingView, 'pkpk_Nm'> | null | undefined): number | null {
  const v = finite(view?.pkpk_Nm);
  return v !== null && v !== 0 ? v : null;
}

/** The tooltip sentences after the base one (`configure.coggingTip`): locale keys of the `controller` namespace + values. */
export function coggingTipParts(view: CoggingView | null | undefined): TipPart[] {
  const pk = coggingValue(view);
  if (pk === null || !view) return [];
  const half = finite(view.half_amp_Nm) ?? pk / 2;
  const pct = finite(view.pct_of_continuous);
  const temp = finite(view.magnet_temp_c);
  const parts: TipPart[] = [{ key: 'configure.coggingTipHalf', args: { half: half.toFixed(coggingDigits(half)) } }];
  if (pct !== null) parts.push({ key: 'configure.coggingTipPct', args: { pct: pct.toFixed(2) } });
  if (temp !== null) parts.push({ key: 'configure.coggingTipTemp', args: { temp: temp.toFixed(0) } });
  parts.push({ key: 'configure.coggingTipMethod' });
  if (view.stale === true) parts.push({ key: 'configure.coggingTipStale' });
  return parts;
}

/** The flat `cogging_*` keys of a project / Configure comparison row (`development/motors.py`) as a view. */
export function coggingFromFlat(c: Record<string, unknown>): CoggingView | null {
  const pk = finite(c.cogging_pkpk_nm);
  if (pk === null) return null;
  return { pkpk_Nm: pk, half_amp_Nm: finite(c.cogging_half_nm), pct_of_continuous: finite(c.cogging_pct_of_continuous),
    magnet_temp_c: finite(c.cogging_magnet_temp_c), fundamental_order: finite(c.cogging_order), stale: c.cogging_stale === true };
}

/** The whole tooltip text: the base sentence and the parts, in one line. */
export function coggingTipText(view: CoggingView | null | undefined, t: (key: string, args?: Record<string, string | number>) => string): string {
  return [t('configure.coggingTip'), ...coggingTipParts(view).map((p) => t(p.key, p.args))].join(' ');
}
