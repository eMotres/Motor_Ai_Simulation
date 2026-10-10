import { useSyncExternalStore } from 'react';
import { getStoredToken, getStoredUser } from './localAuth';
import { clearActiveDuty, clearDutyMaterialsKeys } from './dutySettings';
import type { MotorGeometryParams } from '../types/motor';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001').replace(/\/$/, '');
const LOCAL_PREFIX = 'family.localContext.user.v1.';

export interface LastMotorSelection {
  ref_id: string | null;
  die: string;
  config: string;
  duty: string | null;
}

/** API catalog references carry the raw ID; Configure prefixes that ID with `cat:`. */
export function configureRefIdForSelection(selection: LastMotorSelection): string | null {
  return selection.ref_id ? `cat:${selection.ref_id}` : null;
}

export type LastMotorState = {
  email: string | null;
  status: 'idle' | 'loading' | 'none' | 'loaded' | 'unavailable';
  selection: LastMotorSelection | null;
};

let current: LastMotorState = { email: null, status: 'idle', selection: null };
const listeners = new Set<() => void>();
let selectionEpoch = 0;

const normalizeEmail = (email: string | null | undefined): string | null =>
  typeof email === 'string' && email.trim() ? email.trim().toLowerCase() : null;

export const lastMotorIdentity = (email: string | null | undefined): string | null =>
  normalizeEmail(email);

export function localContextKey(email: string | null | undefined): string | null {
  const normalized = normalizeEmail(email);
  return normalized ? `${LOCAL_PREFIX}${encodeURIComponent(normalized)}` : null;
}

export function readLocalLastMotor(email: string | null | undefined): LastMotorSelection | null {
  const key = localContextKey(email);
  if (!key) return null;
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const value = JSON.parse(raw) as Record<string, unknown>;
    if (!(typeof value.ref_id === 'string' || value.ref_id === null) || typeof value.die !== 'string'
        || typeof value.config !== 'string'
        || !(typeof value.duty === 'string' || value.duty === null)) return null;
    return { ref_id: value.ref_id, die: value.die, config: value.config, duty: value.duty };
  } catch { return null; }
}

export function writeLocalLastMotor(email: string | null | undefined,
                                    selection: LastMotorSelection): void {
  const key = localContextKey(email);
  if (!key) return;
  try { localStorage.setItem(key, JSON.stringify({ ...selection, at: Date.now() })); }
  catch { /* local persistence is a convenience */ }
}

export function setLastMotorState(next: LastMotorState): void {
  current = next;
  listeners.forEach((listener) => listener());
}

export function getLastMotorState(): LastMotorState { return current; }

export function subscribeLastMotor(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useLastMotorState(): LastMotorState {
  return useSyncExternalStore(subscribeLastMotor, getLastMotorState, getLastMotorState);
}

export function clearLiveDutyContext(): void {
  clearActiveDuty();
  clearDutyMaterialsKeys();
  try {
    for (const key of ['sim.current', 'sim.rpm', 'sim.frequency', 'sim.gamma',
      'sim.opMode', 'sim.targetKind', 'sim.targetValue', 'sim.drive',
      'sim.vPeak', 'sim.vDelta', 'sim.iBlock']) localStorage.removeItem(key);
  } catch { /* browser storage is a convenience */ }
}

export function isCurrentLastMotorUser(email: string): boolean {
  return normalizeEmail(getStoredUser()?.email) === normalizeEmail(email);
}

export function beginLastMotorSelection(email: string | null | undefined): number {
  const normalized = normalizeEmail(email);
  if (normalized) selectionEpoch += 1;
  return selectionEpoch;
}

export function invalidateLastMotorOperations(): void { selectionEpoch += 1; }

export const isCurrentLastMotorOperation = (email: string, epoch: number) =>
  isCurrentLastMotorUser(email) && epoch === selectionEpoch;

export async function fetchLastMotor(email: string): Promise<{
  selection: LastMotorSelection | null; unavailable: boolean;
}> {
  if (!isCurrentLastMotorUser(email)) throw new Error('account changed');
  const token = getStoredToken();
  const r = await fetch(`${API}/api/me/last_motor`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!isCurrentLastMotorUser(email)) throw new Error('account changed');
  if (!r.ok) throw new Error(`last motor HTTP ${r.status}`);
  const body = await r.json() as { selection?: LastMotorSelection | null; unavailable?: boolean };
  if (!isCurrentLastMotorUser(email)) throw new Error('account changed');
  return { selection: body.selection ?? null, unavailable: !!body.unavailable };
}

export async function rememberLastMotor(email: string,
                                        selection: LastMotorSelection): Promise<void> {
  if (!isCurrentLastMotorUser(email)) return;
  const token = getStoredToken();
  const r = await fetch(`${API}/api/me/last_motor`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify({ selection }),
  });
  if (!isCurrentLastMotorUser(email)) return;
  if (!r.ok) throw new Error(`remember motor HTTP ${r.status}`);
}

export async function rememberLoadedMotor(email: string, die: string,
                                           config: string, duty: string | null,
                                           epoch = beginLastMotorSelection(email)): Promise<void> {
  const identity = normalizeEmail(email);
  if (!identity || !isCurrentLastMotorOperation(identity, epoch)) return;
  const token = getStoredToken();
  const r = await fetch(`${API}/api/catalog/references`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!isCurrentLastMotorOperation(identity, epoch)) return;
  if (!r.ok) throw new Error(`catalog references HTTP ${r.status}`);
  const data = await r.json() as { motors?: Array<{ id?: string; card?: { die?: string; config?: string } | null }> };
  if (!isCurrentLastMotorOperation(identity, epoch)) return;
  const candidates = (data.motors ?? []).filter((motor) =>
    motor.card?.die === die && typeof motor.id === 'string');
  const ref = candidates.find((motor) => motor.card?.config === config) ?? candidates[0];
  const selection: LastMotorSelection = { ref_id: ref?.id ?? null, die, config, duty };
  writeLocalLastMotor(identity, selection);
  setLastMotorState({ email: identity, status: 'loaded', selection });
  try { await rememberLastMotor(identity, selection); } catch { /* local copy remains available */ }
  if (!isCurrentLastMotorOperation(identity, epoch)) return;
  window.dispatchEvent(new CustomEvent('family-changed'));
}

export async function restoreLastMotor(email: string): Promise<LastMotorState> {
  const identity = normalizeEmail(email);
  if (!identity || !isCurrentLastMotorUser(identity)) throw new Error('account changed');
  const epoch = ++selectionEpoch;
  setLastMotorState({ email: identity, status: 'loading', selection: null });
  clearLiveDutyContext();
  const local = readLocalLastMotor(identity);
  let candidate = local;
  let serverUnavailable = false;
  try {
    const server = await fetchLastMotor(identity);
    if (!isCurrentLastMotorOperation(identity, epoch)) throw new Error('account changed');
    serverUnavailable = server.unavailable;
    if (server.unavailable) {
      const state: LastMotorState = { email: identity, status: 'unavailable', selection: local };
      setLastMotorState(state);
      return state;
    }
    candidate = server.selection ?? local;
  } catch (error) {
    if (!isCurrentLastMotorOperation(identity, epoch)) throw error;
    // Same-account browser memory remains useful if the preference service is
    // temporarily offline. It is already namespaced; never fall back to the
    // old global family.localContext.
  }
  if (!candidate) {
    const state: LastMotorState = { email: identity,
      status: serverUnavailable ? 'unavailable' : 'none', selection: null };
    setLastMotorState(state);
    return state;
  }
  try {
    const { applyDutyLocal } = await import('./dutyLocalApply');
    if (!isCurrentLastMotorOperation(identity, epoch)) throw new Error('account changed');
    const dutyQuery = candidate.duty == null ? '' : `?duty=${encodeURIComponent(candidate.duty)}`;
    const token = getStoredToken();
    const payloadResponse = await fetch(`${API}/api/family/payload/`
      + `${encodeURIComponent(candidate.die)}/${encodeURIComponent(candidate.config)}${dutyQuery}`,
      { headers: token ? { Authorization: `Bearer ${token}` } : {} });
    if (!isCurrentLastMotorOperation(identity, epoch)) throw new Error('account changed');
    if (!payloadResponse.ok) throw new Error(`family payload HTTP ${payloadResponse.status}`);
    const payload = await payloadResponse.json() as import('./dutyLocalApply').DutyPayload;
    if (!isCurrentLastMotorOperation(identity, epoch)) throw new Error('account changed');
    if (payload.die !== candidate.die || payload.config !== candidate.config
        || (candidate.duty && payload.duty?.name !== candidate.duty)) {
      throw new Error('saved motor selection no longer matches its family payload');
    }
    const { useMotorStore } = await import('../stores/motorStore');
    if (!isCurrentLastMotorOperation(identity, epoch)) throw new Error('account changed');
    useMotorStore.setState({ geometry: payload.geometry as MotorGeometryParams });
    if (candidate.duty && payload.duty) {
      await applyDutyLocal(candidate.die, candidate.config, candidate.duty,
        payload, null, false, () => isCurrentLastMotorOperation(identity, epoch));
    } else {
      // A configuration without a selected duty must not inherit the previous
      // account's/global duty pointer or operating point. Keep saved per-duty
      // overlays intact; only clear the live pointer and its ambient fields.
      const { clearActiveDuty, clearDutyMaterialsKeys } = await import('./dutySettings');
      if (!isCurrentLastMotorOperation(identity, epoch)) throw new Error('account changed');
      clearActiveDuty();
      clearDutyMaterialsKeys();
      try {
        if (payload.materials) localStorage.setItem('mat.assign', JSON.stringify(payload.materials));
        for (const key of ['sim.current', 'sim.rpm', 'sim.frequency', 'sim.gamma',
          'sim.opMode', 'sim.targetKind', 'sim.targetValue', 'sim.drive',
          'sim.vPeak', 'sim.vDelta', 'sim.iBlock']) localStorage.removeItem(key);
      } catch { /* browser storage is a convenience */ }
      window.dispatchEvent(new CustomEvent('sim-settings-restored'));
      window.dispatchEvent(new CustomEvent('sim-operating-point', { detail: {} }));
    }
    if (!isCurrentLastMotorOperation(identity, epoch)) throw new Error('account changed');
    writeLocalLastMotor(identity, candidate);
    const state: LastMotorState = { email: identity, status: 'loaded', selection: candidate };
    setLastMotorState(state);
    window.dispatchEvent(new CustomEvent('family-changed'));
    return state;
  } catch {
    if (!isCurrentLastMotorOperation(identity, epoch)) throw new Error('account changed');
    const state: LastMotorState = { email: identity, status: 'unavailable', selection: candidate };
    setLastMotorState(state);
    return state;
  }
}
