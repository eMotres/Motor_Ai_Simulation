// Has the user ALREADY chosen a machine in Configure?  (owner 2026-10-05)
//
// Configure remembers the last machine in localStorage.  Before the first open it
// writes the built-in seed ('ref-...') there automatically, which is not a choice -
// only a catalogue reference ('cat:<id>') is.  The default motor an admin set for
// the account applies while there is no such choice; "reset to my default" in
// Configure applies it again at any time.

export const CONFIGURE_REFID_LS = 'configurator.refId.v1';

/** PURE: is this stored reference id a catalogue machine the user picked? */
export const isOwnChoice = (stored: string | null | undefined): boolean =>
  typeof stored === 'string' && stored.startsWith('cat:');

export function hasOwnConfigureChoice(): boolean {
  try { return isOwnChoice(localStorage.getItem(CONFIGURE_REFID_LS)); } catch { return false; }
}
