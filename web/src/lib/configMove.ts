/** The dry run of `POST /api/family/config/{die}/{cfg}/move` (config_move.py) read as one
 *  confirmation text: how many places per store, what the operator moves by hand, and how the
 *  build reads on the target die.  Pure - the catalog's confirm dialog shows it as is. */

export interface MoveChange { store: string; where: string; change: string }
export interface MoveDiff { key: string; label?: string; die: unknown; live: unknown }
export interface MovePlan {
  mode?: string;
  changes?: MoveChange[];
  manual?: MoveChange[];
  build_check?: {
    target_vs_source?: MoveDiff[];
    config_vs_target?: MoveDiff[];
    stale_duties?: Record<string, string[]>;
  };
}

const diffs = (xs: MoveDiff[]) => xs.map((x) => `${x.label || x.key} ${x.die} → ${x.live}`).join(', ');

export function configMoveSummary(plan: MovePlan | null | undefined): string {
  const counts = new Map<string, number>();
  for (const c of plan?.changes ?? []) counts.set(c.store, (counts.get(c.store) ?? 0) + 1);
  const parts = [[...counts].map(([s, n]) => `${s} ${n}`).join(', ') || 'nothing to change'];
  const manual = plan?.manual ?? [];
  if (manual.length) parts.push(`by hand: ${manual.map((m) => `${m.store} (${m.change})`).join('; ')}`);
  const bc = plan?.build_check;
  if (bc?.target_vs_source?.length) parts.push(`the target is another lamination: ${diffs(bc.target_vs_source)}`);
  if (bc?.config_vs_target?.length) {
    parts.push(`the configuration is not the target die (${diffs(bc.config_vs_target)}): duty saves there are refused`);
  }
  const stale = Object.keys(bc?.stale_duties ?? {});
  if (stale.length) parts.push(`${stale.length} duty(ies) saved on another build: ${stale.join(', ')}`);
  return parts.join('. ') + '.';
}
