import { useCallback, useEffect, useMemo, useState } from 'react';
import { readView, writeView } from './viewMemory';
import { readLocalContext } from './machineBattery';
import {
  DEFAULT_RECORD, answerKey, choosePreset, recordScope, validRecord, withOverride, withoutOverride,
  type RunMode, type RunPresetName, type RunRecord, type RunResolved,
} from './runSettings';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';
const EVENTS = ['family-changed', 'sim-design-applied', 'sim-settings-restored', 'mesh-preset-applied', 'storage'];

/** The machine + duty the record belongs to ("none" when no machine is loaded). */
export function contextKey(): string {
  try {
    const c = readLocalContext();
    return c ? `${c.die}/${c.config}/${c.duty ?? ''}` : 'none';
  } catch { return 'none'; }
}

export interface RunSettingsState {
  record: RunRecord;
  resolved: RunResolved | null;
  error: string | null;
  /** true only while the held answer is for the current machine + duty + mode + record */
  ready: boolean;
  choose: (name: RunPresetName) => void;
  setOverride: (key: string, value: number | boolean) => void;
  clearOverride: (key: string) => void;
  refresh: () => void;
}

/**
 * The panel's run settings.  The record (preset + overrides) is remembered per machine and duty and never touches the Simulation
 * or Mesh tab; the resolved settings come from the server and are what the panel shows and the run sends.
 */
export function useRunSettings(mode: RunMode): RunSettingsState {
  const [tick, setTick] = useState(0);
  const ctx = useMemo(() => contextKey(), [tick]);   // eslint-disable-line react-hooks/exhaustive-deps
  const [record, setRecord] = useState<RunRecord>(() => readView(recordScope(contextKey()), r => validRecord(r, mode)) ?? DEFAULT_RECORD);
  const [held, setHeld] = useState<{ key: string; data: RunResolved } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const bump = () => setTick(t => t + 1);
    for (const e of EVENTS) window.addEventListener(e, bump);
    return () => { for (const e of EVENTS) window.removeEventListener(e, bump); };
  }, []);
  // another machine or duty: its own record (default Optimization); nothing is written
  useEffect(() => { setRecord(readView(recordScope(ctx), r => validRecord(r, mode)) ?? DEFAULT_RECORD); }, [ctx]);   // eslint-disable-line react-hooks/exhaustive-deps
  const key = answerKey(ctx, mode, record);
  useEffect(() => {
    let live = true;
    setError(null);
    setHeld(null);          // a refresh starts: the held answer is not valid until the new one arrives (Run and Draw baseline wait)
    fetch(`${API}/api/optimization/run-settings/resolve?mode=${mode}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preset: record.preset, overrides: record.overrides }), cache: 'no-store',
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
  const save = useCallback((r: RunRecord) => { setRecord(r); writeView(recordScope(ctx), r); }, [ctx]);
  const resolved = held && held.key === key ? held.data : null;
  return {
    record, resolved, error, ready: !!resolved && !error,
    choose: n => save(choosePreset(n)),
    setOverride: (k, v) => save(withOverride(record, k, v)),
    clearOverride: k => save(withoutOverride(record, k)),
    refresh: () => setTick(t => t + 1),
  };
}
