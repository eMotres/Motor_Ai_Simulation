/**
 * The mesh preset of a run's own panel (Sweep study, Optimization), owner 2026-11-24: a preset is a BUTTON pressed before the
 * run; it sets every preset-controlled setting in the stored fields the run is built from (lib/meshPresets.applyPreset), and
 * the user may change any of them afterwards ("custom").  This hook gives the panel
 *  - the presets resolved for the loaded machine (refetched when another machine is loaded) with a retry on failure,
 *  - the stored settings the run WILL send, read from the same fields the request body is built from,
 *  - which preset they equal, or "custom" with what differs,
 *  - the default: with no choice made yet for this machine, the Optimization preset is applied once and remembered.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  applyPreset, fetchMeshPresets, matchPreset, presetDiff, readCurrentSettings,
  type CurrentSettings, type PresetChoice, type PresetDiff, type PresetSet,
} from './meshPresets';
import { readLocalContext } from './machineBattery';
import { machineKey } from './machineBearings';
import { readView, writeView } from './viewMemory';

const EVENTS = ['sim-settings-restored', 'mesh-preset-applied', 'storage'];
const MACHINE_EVENTS = ['family-changed', 'sim-design-applied'];

export interface RunPreset {
  set: PresetSet | null;
  error: string | null;
  retry: () => void;
  current: CurrentSettings;
  choice: PresetChoice;
  diff: PresetDiff[];
  /** true once the presets are resolved and the settings shown are the ones the run will send */
  ready: boolean;
}

export const rememberedKey = (scope: string, machine: string | null): string => `runPreset.${scope}.${machine ?? 'none'}`;

export function useRunPreset(scope: string): RunPreset {
  const [set, setSet] = useState<PresetSet | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    setSet(null); setError(null);
    fetchMeshPresets().then(s => { if (live) setSet(s); }).catch(e => { if (live) setError(String(e?.message ?? e)); });
    return () => { live = false; };
  }, [attempt]);
  useEffect(() => {
    const bump = () => setTick(t => t + 1);
    const machine = () => { setAttempt(n => n + 1); bump(); };
    for (const e of EVENTS) window.addEventListener(e, bump);
    for (const e of MACHINE_EVENTS) window.addEventListener(e, machine);
    return () => {
      for (const e of EVENTS) window.removeEventListener(e, bump);
      for (const e of MACHINE_EVENTS) window.removeEventListener(e, machine);
    };
  }, []);
  // DEFAULT: no choice made yet for this machine -> the Optimization preset is applied (a new sweep is fast by default)
  useEffect(() => {
    if (!set) return;
    const key = rememberedKey(scope, machineKey(readLocalContext()));
    const done = readView<boolean>(key, raw => (raw === true ? true : null));
    if (done) return;
    const opt = set.presets.find(p => p.name === 'optimization');
    if (!opt?.values) return;
    writeView(key, true);
    void applyPreset(opt).then(() => setTick(t => t + 1));
  }, [set, scope]);
  const current = useMemo(() => readCurrentSettings(), [tick, set]);          // eslint-disable-line react-hooks/exhaustive-deps
  const choice = useMemo(() => matchPreset(current, set), [current, set]);
  const diff = useMemo(() => {
    if (!set || choice !== 'custom') return [];
    // custom: say what differs from the Optimization preset (the default one)
    return presetDiff(current, set.presets.find(p => p.name === 'optimization'));
  }, [current, set, choice]);
  const retry = useCallback(() => setAttempt(n => n + 1), []);
  return { set, error, retry, current, choice, diff, ready: !!set && !error };
}
