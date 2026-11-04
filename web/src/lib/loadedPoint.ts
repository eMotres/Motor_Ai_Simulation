/**
 * The catalogue point the editor has loaded RIGHT NOW, and a watch for it changing.
 *
 * `dutySettings.activeDuty()` is the one record every load writes (`applyDutyEverywhere` claims it before the first panel
 * write, and the SimPointSwitcher, the follower and F5 all read it), so the Thermal and Mechanical stores take their
 * "whose result is this?" answer from it (`lib/resultOwner`).  `null` = no catalogue point (a free-form machine, a copy).
 */
import { activeDuty } from './dutySettings';
import { pointKeyOf } from './resultOwner';

/** Key of the loaded point (`pointKeyOf` shape), or `null`. */
export const loadedPointKey = (): string | null => pointKeyOf(activeDuty());

/** The events a load fires at its end: the duty apply (`sim-design-applied`, `family-changed`), the panel re-read
 *  (`sim-settings-restored`) and a my-motors copy (which also clears the active duty). */
const LOAD_EVENTS = ['sim-design-applied', 'family-changed', 'sim-settings-restored'] as const;

/**
 * Call `onChange(now, before)` whenever the loaded point differs from the last one seen.  Idempotent per event (the several
 * events one load fires collapse to one call), and quiet while the point is unchanged.  Returns the unsubscribe.
 */
export function watchLoadedPoint(onChange: (now: string | null, before: string | null) => void): () => void {
  if (typeof window === 'undefined') return () => undefined;
  let seen = loadedPointKey();
  const check = () => {
    const now = loadedPointKey();
    if (now === seen) return;
    const before = seen;
    seen = now;
    onChange(now, before);
  };
  for (const ev of LOAD_EVENTS) window.addEventListener(ev, check);
  // another tab/window of this browser loaded a point: the shared localStorage record moved
  window.addEventListener('storage', check);
  return () => {
    for (const ev of LOAD_EVENTS) window.removeEventListener(ev, check);
    window.removeEventListener('storage', check);
  };
}
