// The "Simulations" top tab (owner 2026-10-13): Electromagnetic, Thermal and Mechanical
// share ONE top tab, in that order (owner 2026-10-30: a plain tab like the others - no dropdown;
// the members are sub-tabs in the panel header row, see App.tsx and components/common/SubTabs); "Schemas" (owner 2026-10-14: the view-only graph of how the
// simulations are chained) is the last entry.  Geometry, Mesh and Controller stay
// separate tabs, with the same gates and order as before.  The individual panels keep their
// own tab ids and gates - this only decides how the bar is DRAWN, so every
// `setActiveTab('simulation')` elsewhere still works.
//
// Pure, no imports: web/src/lib/__tests__/simulationsMenu.test.mjs imports this file.

/** The panels the dropdown lists, in menu order. */
export const SIMULATION_TAB_IDS: readonly string[] = ['simulation', 'thermal', 'mechanical', 'schemas'];

/** The bar's value while one of them is open. */
export const SIMULATIONS_BAR_ID = 'simulations';

export const isSimulationTab = (id: string): boolean => SIMULATION_TAB_IDS.includes(id);

export interface BarTab { id: string; label: string; order: number; }

export type BarEntry =
  | { kind: 'tab'; id: string; label: string }
  | { kind: 'simulations'; id: typeof SIMULATIONS_BAR_ID; members: { id: string; label: string }[] };

/** The bar from the already GATED tab list (so a member the role may not see is simply
 *  absent): the member tabs collapse into one `simulations` entry at the place of the first
 *  member; with no member visible there is no entry at all.  Members keep MENU order. */
export function barEntries(tabs: readonly BarTab[]): BarEntry[] {
  const sorted = [...tabs].sort((a, b) => a.order - b.order);
  const members = SIMULATION_TAB_IDS
    .map((id) => sorted.find((t) => t.id === id))
    .filter((t): t is BarTab => !!t)
    .map((t) => ({ id: t.id, label: t.label }));
  const out: BarEntry[] = [];
  let placed = false;
  for (const t of sorted) {
    if (isSimulationTab(t.id)) {
      if (!placed && members.length) {
        out.push({ kind: 'simulations', id: SIMULATIONS_BAR_ID, members });
        placed = true;
      }
      continue;
    }
    out.push({ kind: 'tab', id: t.id, label: t.label });
  }
  return out;
}

/** Which bar entry is selected for the active panel. */
export const barValue = (activeTab: string): string =>
  isSimulationTab(activeTab) ? SIMULATIONS_BAR_ID : activeTab;

/** Which panel the plain "Simulations" tab opens: the member the user looked at last when this account still has
 *  it, else the first member it has; null when no member is visible. */
export function simulationsTarget(last: string | null | undefined,
                                  members: readonly { id: string }[]): string | null {
  if (last && members.some((m) => m.id === last)) return last;
  return members[0]?.id ?? null;
}

/** "Simulations" or, while one is open, "Simulations · Electromagnetic". */
export function simulationsLabel(base: string, activeTab: string,
                                 members: readonly { id: string; label: string }[]): string {
  const m = members.find((x) => x.id === activeTab);
  return m ? `${base} · ${m.label}` : base;
}
