// The motor Configure shows, and what the server STORES for it (one display system, owner 2026-10-26).
//
// "The loaded motor" is the one the last Motors-tab-style load made current (`lib/lastMotor`, set by `applyDutyEverywhere`
// for a catalog load AND for a project-motor open, because both go through the same load).  For it this hook reads the
// read-only view (`/api/motor-view/{die}/{cfg}`): `mode` says whether Configure opens on the full passport or points-limited.
// Anything unclear (not signed in, no loaded motor, request failed, still loading) leaves the ordinary Configure untouched.
import { useCallback, useEffect, useState } from 'react';
import { getStoredUser } from './localAuth';
import { useLastMotorState } from './lastMotor';
import { fetchMotorView } from './motorViewApi';
import type { MotorView } from '../modules/development/types';

export interface LoadedMotorSelection { die: string; config: string; duty: string | null }
export interface LoadedMotorView {
  selection: LoadedMotorSelection | null;
  status: 'none' | 'loading' | 'ready' | 'error';
  view: MotorView | null;
  error: string;
  reload: () => void;
}

const keyOf = (s: LoadedMotorSelection | null) => (s ? `${s.die}\u0000${s.config}` : '');

export function useLoadedMotorView(): LoadedMotorView {
  const lastMotor = useLastMotorState();
  const email = getStoredUser()?.email?.trim().toLowerCase() ?? null;
  const picked = lastMotor.status === 'loaded' && lastMotor.selection && lastMotor.email === email ? lastMotor.selection : null;
  const selection: LoadedMotorSelection | null = picked ? { die: picked.die, config: picked.config, duty: picked.duty } : null;
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

  if (!selection) return { selection: null, status: 'none', view: null, error: '', reload };
  if (state.key !== key) return { selection, status: 'loading', view: null, error: '', reload };
  if (state.error) return { selection, status: 'error', view: null, error: state.error, reload };
  return { selection, status: 'ready', view: state.view, error: '', reload };
}
