/**
 * Server-side memory of a tab's input fields — the same bargain the Simulation
 * tab has with the config.
 *
 * WHY (user 2026-09-07: "запоминай все последние настройки механических и
 * термических моделирований … всё одинаково для всех моделирований"): the
 * Mechanical and Thermal stores persisted their fields to `localStorage` only —
 * one browser's memory — and their `/last` restored just the parameters of the
 * last SOLVE.  A field set and not yet solved, or set in another browser, was
 * gone.  Now every persisted field is written to `/api/panel_settings/<panel>`
 * (debounced) and read back on the tab's first mount, server first; the
 * browser's copy stays the fallback for an offline page.
 *
 * Contract: the server stores the fields AS THE STORE KEEPS THEM (strings for
 * text fields), so the store's own migration is the single interpreter.
 */
const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const BASE = `${API.replace(/\/$/, '')}/api/panel_settings`;

export type PanelName = 'mechanical' | 'thermal' | 'simulation' | 'mesh' | 'sweep';

export interface PanelSettings {
  settings: Record<string, unknown>;
  updated_at: string | null;
}

/** Read the caller's remembered fields; `null` when the server cannot answer
 *  (offline, not signed in) — never throws. */
export async function loadPanelSettings(panel: PanelName): Promise<PanelSettings | null> {
  try {
    const r = await fetch(`${BASE}/${panel}`, { cache: 'no-store' });
    if (!r.ok) return null;
    const j = await r.json() as { settings?: Record<string, unknown>; updated_at?: string | null };
    return { settings: j.settings ?? {}, updated_at: j.updated_at ?? null };
  } catch {
    return null;
  }
}

const _timers: Partial<Record<PanelName, number>> = {};
const _pending: Partial<Record<PanelName, Record<string, unknown>>> = {};

/** Queue a save of the panel's persisted fields; coalesced per panel so a
 *  field typed digit by digit is one PUT, not five.  The LAST snapshot wins. */
export function savePanelSettings(panel: PanelName, settings: Record<string, unknown>,
                                  delayMs = 700): void {
  _pending[panel] = settings;
  if (_timers[panel]) window.clearTimeout(_timers[panel]);
  _timers[panel] = window.setTimeout(() => {
    _timers[panel] = undefined;
    const body = _pending[panel];
    _pending[panel] = undefined;
    if (!body) return;
    void fetch(`${BASE}/${panel}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ settings: body }),
    }).catch(() => { /* offline: the browser copy is what we have */ });
  }, delayMs);
}

/** Pure: which server values to adopt.  Every key the panel persists that the
 *  server has is taken (server first — it is the memory shared by all
 *  browsers); keys the server never saw keep the store's current value. */
export function adoptSettings<T extends Record<string, unknown>>(
  current: T, server: Record<string, unknown> | null | undefined, keys: readonly (keyof T & string)[],
): Partial<T> {
  const out: Partial<T> = {};
  if (!server) return out;
  for (const k of keys) {
    if (!(k in server)) continue;
    const v = server[k];
    if (v === undefined) continue;
    // Same shape as the store keeps: a string field stays a string, a boolean
    // stays a boolean, an object (contacts) stays an object.
    const cur = current[k];
    if (cur !== undefined && cur !== null) {
      if (typeof v !== typeof cur) continue;
      // …and a LIST stays a list.  `typeof []` is 'object', so the line above
      // would happily drop an object where the panel keeps an array — which is
      // where the tabs' local comparison rows live since 2026-09-07, and a
      // `{}` adopted there is a table that throws on its first render.
      if (Array.isArray(cur) !== Array.isArray(v)) continue;
    }
    (out as Record<string, unknown>)[k] = v;
  }
  return out;
}
