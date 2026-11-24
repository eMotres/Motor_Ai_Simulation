import { useCallback, useEffect, useRef, useState } from 'react';
import { readLocalContext } from './machineBattery';
import { readCurrentSettings, readStepsSource } from './currentSettings';
import { answerKey, sameRequest, tabRequest, type RunMode, type RunRequest, type RunResolved } from './runSettings';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';
const EVENTS = ['family-changed', 'sim-design-applied', 'sim-settings-restored', 'mesh-preset-applied', 'storage', 'focus'];
/** The tabs write their fields to localStorage without an event: the line re-reads them at this rhythm while it is shown. */
const POLL_MS = 1500;

/** The remembered record of the former per-panel buttons (`view.runSettings.<die>/<config>/<duty>`): dropped silently. */
function dropOldRecords(): void {
  try {
    const old: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith('view.runSettings.')) old.push(k);
    }
    for (const k of old) localStorage.removeItem(k);
  } catch { /* storage blocked: nothing to drop */ }
}

/** The machine + duty the answer belongs to ("none" when no machine is loaded). */
export function contextKey(): string {
  try {
    const c = readLocalContext();
    return c ? `${c.die}/${c.config}/${c.duty ?? ''}` : 'none';
  } catch { return 'none'; }
}

export interface RunSettingsState {
  /** The values the tabs hold now (null while a value is missing). */
  request: RunRequest | null;
  /** What the tabs do not hold: the line reads "unknown" and Run is disabled. */
  missing: string[];
  resolved: RunResolved | null;
  error: string | null;
  /** true only while the held answer is for the current machine + duty + mode + values */
  ready: boolean;
  /** Who set the steps on the Simulation tab ('eddy_default' | 'user' | null): for the reason line. */
  stepsSource: unknown;
  /** At Run time: the request when the tabs still hold exactly what the line showed, else null (and the line refreshes). */
  verify: () => RunRequest | null;
  refresh: () => void;
}

/**
 * The panel's run settings: read-only.  The tab values (Mesh and Simulation) are read from the browser, the server resolves and
 * labels them (the same resolver the run uses), the panel shows the answer and the run sends the same values.  Nothing is
 * written, nothing remembered.
 */
export function useRunSettings(mode: RunMode): RunSettingsState {
  const [tick, setTick] = useState(0);
  const [ctx, setCtx] = useState(contextKey);
  const [tab, setTab] = useState(() => tabRequest(readCurrentSettings(), mode));
  const [held, setHeld] = useState<{ key: string; data: RunResolved } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const tabRef = useRef(tab);
  tabRef.current = tab;
  // re-read the tab values and the machine; state changes ONLY when something differs, so the panel does not re-render at the poll rhythm
  const check = useCallback(() => {
    const c = contextKey();
    setCtx(prev => (prev === c ? prev : c));
    const next = tabRequest(readCurrentSettings(), mode);
    const cur = tabRef.current;
    if (!sameRequest(next.request, cur.request) || next.missing.join() !== cur.missing.join()) setTab(next);
  }, [mode]);
  useEffect(() => { dropOldRecords(); }, []);
  useEffect(() => {
    for (const e of EVENTS) window.addEventListener(e, check);
    const id = setInterval(check, POLL_MS);
    return () => { for (const e of EVENTS) window.removeEventListener(e, check); clearInterval(id); };
  }, [check]);
  const key = answerKey(ctx, mode, tab.request);
  useEffect(() => {
    let live = true;
    setError(null);
    setHeld(null);          // a refresh starts: the held answer is not valid until the new one arrives (Run and Draw baseline wait)
    if (!tab.request) return () => { live = false; };
    fetch(`${API}/api/optimization/run-settings/resolve?mode=${mode}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preset: tab.request.preset, overrides: tab.request.overrides }), cache: 'no-store',
    }).then(async r => {
      if (!r.ok) {
        let d = `HTTP ${r.status}`;
        try { const j = await r.json(); if (typeof j?.detail === 'string') d = j.detail; } catch { /* no body */ }
        throw new Error(d);
      }
      return (await r.json()) as RunResolved;
    }).then(data => { if (live) setHeld({ key, data }); })
      .catch(e => { if (live) { setHeld(null); setError(String(e?.message ?? e)); } });
    return () => { live = false; };
  }, [key, tick]);                                  // eslint-disable-line react-hooks/exhaustive-deps
  const resolved = held && held.key === key ? held.data : null;
  const verify = useCallback((): RunRequest | null => {
    const now = tabRequest(readCurrentSettings(), mode).request;
    if (!now || !sameRequest(now, tabRef.current.request)) { check(); return null; }
    return now;
  }, [mode, check]);
  return {
    request: tab.request, missing: tab.missing, resolved, error, ready: !!resolved && !error && !!tab.request,
    stepsSource: readStepsSource(), verify, refresh: () => { check(); setTick(t => t + 1); },
  };
}
