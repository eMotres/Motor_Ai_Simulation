// The motor Configure shows, and what the server STORES for it (one display system, owner 2026-10-26).
//
// WHICH motor: the one the user just ASKED for (`lib/familyLoad` target: set at the click, so the view is fetched AT ONCE, in
// parallel with the machine load) and, once the account's loaded-motor record (`lib/lastMotor`) names a motor, that one.
// Never the previous motor: while the answer for the requested motor is not here the status is `loading` and `view` is null.
// For it this hook reads the read-only view (`/api/motor-view/{die}/{cfg}`): `mode` says whether Configure opens on the full
// passport or points-limited.  Anything unclear (not signed in, no motor, request failed) leaves the ordinary Configure.
import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { getStoredUser } from './localAuth';
import { useLastMotorState } from './lastMotor';
import { getFamilyLoad, getFamilyTarget, getFamilyVersion, subscribeFamilyLoad } from './familyLoad';
import { fetchMotorView } from './motorViewApi';
import type { MotorView } from '../modules/development/types';

export interface LoadedMotorSelection { die: string; config: string; duty: string | null }
export interface LoadedMotorView {
  selection: LoadedMotorSelection | null;
  status: 'none' | 'loading' | 'ready' | 'error';
  view: MotorView | null;
  error: string;
  /** the machine apply of a load (the Motors tab's own) is still running behind the view */
  applying: boolean;
  reload: () => void;
}

const keyOf = (s: LoadedMotorSelection | null) => (s ? `${s.die}\u0000${s.config}` : '');

export function useLoadedMotorView(): LoadedMotorView {
  const lastMotor = useLastMotorState();
  // one subscription for the whole family-load store (target + progress)
  useSyncExternalStore(subscribeFamilyLoad, getFamilyVersion, getFamilyVersion);
  const requested = getFamilyTarget();
  const applying = getFamilyLoad().status === 'loading';
  const email = getStoredUser()?.email?.trim().toLowerCase() ?? null;
  const picked = lastMotor.status === 'loaded' && lastMotor.selection && lastMotor.email === email ? lastMotor.selection : null;
  const selection: LoadedMotorSelection | null = requested
    ? { die: requested.die, config: requested.config, duty: requested.duty }
    : picked ? { die: picked.die, config: picked.config, duty: picked.duty } : null;
  const key = keyOf(selection);
  const [state, setState] = useState<{ key: string; view: MotorView | null; error: string }>({ key: '', view: null, error: '' });
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    if (!selection) return;
    let cancelled = false;
    fetchMotorView(selection.die, selection.config)
      .then((view) => { if (!cancelled) setState({ key, view, error: '' }); })
      .catch((e) => { if (!cancelled) setState({ key, view: null, error: e instanceof Error ? e.message : String(e) }); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, tick]);

  if (!selection) return { selection: null, status: 'none', view: null, error: '', applying, reload };
  if (state.key !== key) return { selection, status: 'loading', view: null, error: '', applying, reload };
  if (state.error) return { selection, status: 'error', view: null, error: state.error, applying, reload };
  return { selection, status: 'ready', view: state.view, error: '', applying, reload };
}
