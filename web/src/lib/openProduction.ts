/** "Open in Production" from another page (a development project, owner 2026-11-02): select that die/config in the Production
 *  tab and switch to it.  The Production tab stays mounted once visited, so it listens for the event; the remembered selection
 *  (`production.last.v1`, the same key ProductionTab reads at mount) covers the first visit.  Opening grants nothing: the server
 *  decides what the caller may see (a production member, an admin, or a member of the project that links the motor). */
import { useUIStore } from '../stores/uiStore';

export const OPEN_PRODUCTION_EVENT = 'motres:open-production';
export interface OpenProductionDetail { die: string; config: string }

export function openProduction(die: string, config: string): void {
  try { window.localStorage.setItem('production.last.v1', JSON.stringify({ die, config })); } catch { /* storage off */ }
  try { window.dispatchEvent(new CustomEvent<OpenProductionDetail>(OPEN_PRODUCTION_EVENT, { detail: { die, config } })); } catch { /* no events */ }
  useUIStore.getState().setActiveTab('production' as never);
}
