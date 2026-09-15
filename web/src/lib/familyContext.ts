/**
 * The ONE rule that ends the die-clobber family for good (incidents
 * 2026-08-24 and 2026-09-01 22:33): only ▶ in the Motors catalog
 * (FamilyCatalog.applyDuty → /api/family/activate) may make a die ACTIVE.
 * Every other whole-machine load — a Compare row, a private "my motor" copy,
 * a preset — releases the server context FIRST, so the geometry save that
 * follows has no die to sync into and the header strip says "no die active"
 * instead of naming a machine that is no longer on screen.
 *
 * Why not "activate the right die instead"?  Those loads do not know which die
 * they are (a Compare row keeps the identity only in its free-text name), and
 * a wrong guess is exactly the bug.  Releasing is always correct; the user
 * makes a die active again with one ▶ click.
 */
import { canWriteServer } from './localAuth';
import { clearActiveDuty } from './dutySettings';
import { clearDutyRuns } from './dutyRuns';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

/** Release the active die/config/duty before a whole-machine load.
 *  Owner: server context; everyone: the client-side duty marker (so the
 *  operating point of the loaded machine is not filed under the old duty). */
export async function releaseFamilyContext(reason: string): Promise<void> {
  try { clearActiveDuty(); } catch { /* memory is a convenience */ }
  // …and the duty's saved-run list with it: the Simulation panel's run selector
  // must not offer the PWM run of a duty that is no longer on screen.
  try { clearDutyRuns(); } catch { /* memory is a convenience */ }
  if (!canWriteServer()) return;          // ordinary user: server context is the owner's
  try {
    const r = await fetch(`${API}/api/family/deactivate`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason }),
    });
    if (!r.ok) {
      // A load must NEVER proceed on a context that could not be released —
      // that is the 22:33 clobber in one sentence.
      let why = `HTTP ${r.status}`;
      try { why = (await r.json()).detail ?? why; } catch { /* no body */ }
      throw new Error(`cannot release the active die (${why}) — sign in again and retry`);
    }
    window.dispatchEvent(new CustomEvent('family-changed'));
  } catch (e) {
    if (e instanceof Error && /release the active die/.test(e.message)) throw e;
    // backend away: a failed release is fatal for the same reason
    throw new Error('cannot reach the server to release the active die — the load was not applied');
  }
}
