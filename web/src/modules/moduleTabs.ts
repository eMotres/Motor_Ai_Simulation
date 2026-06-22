/**
 * Frontend module registry — the portal builds its tab bar FROM the backend
 * module manifests (web-as-module). useModulePanels() reads /api/modules once and
 * returns the UI panels that declare `as_tab`, keyed by panel_id, so App.tsx can
 * drive each module-backed tab's TITLE + ORDER from its manifest instead of
 * hard-coding them. On any failure it returns {} and App falls back to its static
 * tab definitions — so the shell never breaks if /api/modules is unreachable.
 */
import { useEffect, useState } from 'react';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

export interface PanelInfo { title: string; order: number; }

export function useModulePanels(): Record<string, PanelInfo> {
  const [panels, setPanels] = useState<Record<string, PanelInfo>>({});
  useEffect(() => {
    let alive = true;
    fetch(`${API}/api/modules`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d: any) => {
        if (!alive || !d?.modules) return;
        const out: Record<string, PanelInfo> = {};
        for (const m of d.modules) {
          const ui = m.ui;
          if (ui?.as_tab && ui.panel_id) {
            // modules can share a panel (e.g. all solvers -> "simulation"); keep
            // the lowest order so the tab sits where the earliest module wants it.
            const cur = out[ui.panel_id];
            if (!cur || ui.order < cur.order) out[ui.panel_id] = { title: ui.title, order: ui.order };
          }
        }
        setPanels(out);
      })
      .catch(() => { /* keep {} — App uses its static fallback */ });
    return () => { alive = false; };
  }, []);
  return panels;
}
