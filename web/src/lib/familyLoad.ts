// The motor LOAD that runs in the background (owner 2026-10-13: the model takes very long to
// load; a click must open the menu at once).  A click on a configuration / play glyph opens
// the target tab at once and the load (lib/dutyApply.applyDutyEverywhere) continues behind it;
// this module is the one place that says so, WHATEVER tab is on screen: the Motors panel that
// started the load is unmounted the moment the view switches, so its own `busy` / message state
// cannot carry the progress or - worse - the failure.
//
// Pure, no imports: web/src/lib/__tests__/familyLoad.test.mjs imports this file.

export interface FamilyLoadState {
  status: 'idle' | 'loading' | 'error';
  die: string; config: string; duty: string;
  /** one short line; set when status is 'error' */
  message: string;
}

const IDLE: FamilyLoadState = { status: 'idle', die: '', config: '', duty: '', message: '' };

let cur: FamilyLoadState = IDLE;
/** The motor the user ASKED for, from the click until the account's own loaded-motor record (`lib/lastMotor`) names it - the
 *  record is written only after the whole load and a catalogue read, so without this Configure showed the PREVIOUS motor in between. */
export interface FamilyTarget { die: string; config: string; duty: string }
let target: FamilyTarget | null = null;
let seq = 0;
let owner = 0;                       // the token of the load the state belongs to
const subs = new Set<() => void>();
let version = 0;                     // bumps on every change (target included), for useSyncExternalStore
export const getFamilyVersion = (): number => version;
const emit = () => { version += 1; subs.forEach((f) => { try { f(); } catch { /* a listener must not break a load */ } }); };

export const getFamilyLoad = (): FamilyLoadState => cur;
export const getFamilyTarget = (): FamilyTarget | null => target;
/** The loaded-motor record caught up (or the target is moot): the record is authoritative again. */
/** With `only`, the marker is dropped only when it still names THAT motor (an older load ending must not clear a newer request). */
export function clearFamilyTarget(only?: { die: string; config: string }): void {
  if (!target) return;
  if (only && (target.die !== only.die || target.config !== only.config)) return;
  target = null; emit();
}
export function subscribeFamilyLoad(fn: () => void): () => void {
  subs.add(fn);
  return () => { subs.delete(fn); };
}

/** A load starts: returns its token (pass it to finish / fail). */
export function beginFamilyLoad(die: string, config: string, duty: string): number {
  seq += 1; owner = seq;
  cur = { status: 'loading', die, config, duty, message: '' };
  target = { die, config, duty };
  emit();
  return owner;
}

/** The load ended well: the line disappears (unless a newer load took over). */
export function finishFamilyLoad(token: number): void {
  if (token !== owner) return;
  cur = IDLE; emit();
}

/** The load failed: the reason stays on screen until dismissed or the next load.  A failure of
 *  a load that a newer one has replaced is dropped - the newer one owns the line. */
export function failFamilyLoad(token: number, message: string): void {
  if (token !== owner) return;
  cur = { ...cur, status: 'error', message: String(message || 'load failed').split('\n')[0] };
  target = null;                       // a failed load names no motor: Configure falls back to what is really loaded
  emit();
}

export function dismissFamilyLoad(): void {
  if (cur.status === 'loading') return;
  cur = IDLE; emit();
}

export const familyLoadName = (s: Pick<FamilyLoadState, 'die' | 'config' | 'duty'>): string =>
  [s.die, s.config, s.duty].filter(Boolean).join(' / ');
