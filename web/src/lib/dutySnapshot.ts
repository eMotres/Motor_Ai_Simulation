// The duty's saved PANEL SETTINGS block (`mesh:` in the duty yaml — every
// mesh.*/sim.* key the run was solved with) written back into localStorage.
//
// ONE implementation for the two moments it happens:
//   • ▶ on a duty (catalog/FamilyCatalog applyDuty), and
//   • a panel that wakes up with an EMPTY browser store while the server still
//     names an active duty (user 2026-09-03: "опять сбилось — 24 шага и
//     демагнитизация выключена").  The settings live only in the browser; a
//     reset profile shows the factory defaults under the duty's own name until
//     somebody presses ▶ again.  Now the panel heals itself from the snapshot.
import { dutyKey, setDutyCycleSnapshot } from './dutySettings';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

// Never restore the run trigger or result caches — writing back a saved
// sim.runNonce made loading a duty AUTO-START a solve (2026-08-25).  The DC
// link belongs to the MACHINE (its battery), not to the panel.
const NEVER = new Set(['sim.runNonce', 'sim.lastTransient', 'sim.lastSummary',
                       'sim.viewSummary', 'sim.vBus']);

/** Write a duty's settings block into localStorage.  Returns the number of
 *  keys written.  Does NOT dispatch events — the caller decides when the
 *  mounted panels should re-read. */
export function applyDutySettingsBlock(mesh: unknown): number {
  if (!mesh || typeof mesh !== 'object') return 0;
  let n = 0;
  try {
    for (const [k, v] of Object.entries(mesh as Record<string, unknown>)) {
      const key = k.includes('.') ? k : `mesh.${k}`;
      if (NEVER.has(key)) continue;
      let val = v;
      // Retired per-part keys: 'coil' (mm, superseded by the Wire cell
      // factor) and 'shaft' (kicked the whole build onto gmsh).  Old duties
      // still carry them; writing them back would steer solves with no UI
      // field left to show it.
      if (key === 'mesh.componentMesh' && val && typeof val === 'object') {
        const cm = { ...(val as Record<string, unknown>) };
        delete cm.coil; delete cm.shaft;
        val = cm;
      }
      localStorage.setItem(key, JSON.stringify(val));
      n++;
    }
  } catch { /* quota — settings stay as they were */ }
  return n;
}

/**
 * ▶ on a duty: the `duty_cycle:` block the yaml carries becomes this duty's
 * SNAPSHOT layer (2026-09-14).
 *
 * Called from `applyDutyLocal` BEFORE the local overlay is restored, exactly
 * where the operating point's snapshot is applied and for the same reason: the
 * catalog states what this duty's cycle IS, and the user's un-saved edit — if
 * there is one — is newer and must win over it.
 *
 * A duty with no block writes `null`, which is an ANSWER: every duty saved
 * before the cycle existed is the continuous point it was always assumed to be,
 * and inventing an S1 for it would put a claim in the editor nobody made.
 */
export function applyDutyCycleBlock(die: string, cfg: string, duty: string,
                                    block: unknown): void {
  const b = (block && typeof block === 'object' && !Array.isArray(block))
    ? (block as Record<string, unknown>) : null;
  try {
    setDutyCycleSnapshot(dutyKey(die, cfg, duty), b);
  } catch { /* the cycle memory is a convenience, never a blocker */ }
}

/** Is the browser store EMPTY of panel settings?  (A reset profile, a new
 *  browser, another origin.)  `sim.stepsPP` is written by the panel on its
 *  first change and by every duty snapshot, so its absence is the tell. */
export function panelSettingsMissing(): boolean {
  try { return localStorage.getItem('sim.stepsPP') == null && localStorage.getItem('sim.demag') == null; }
  catch { return false; }
}

/** Self-heal: when the store is empty and the server names an active duty,
 *  pull that duty's snapshot and apply its settings + operating point.
 *  Resolves true when something was written (the caller then dispatches
 *  'sim-settings-restored'). */
export async function healPanelSettingsFromActiveDuty(force = false): Promise<boolean> {
  // Only on an explicit user click (force) — never applied by itself (user
  // 2026-09-03: nothing is loaded without permission).
  if (!force) return false;
  if (!panelSettingsMissing()) return false;
  let ctx: any = null;
  try { ctx = await fetch(`${API}/api/family/context`).then(r => r.json()); } catch { return false; }
  if (!ctx?.active || !ctx.die || !ctx.config || !ctx.duty) return false;
  let p: any = null;
  try {
    const r = await fetch(`${API}/api/family/payload/${encodeURIComponent(ctx.die)}/`
      + `${encodeURIComponent(ctx.config)}?duty=${encodeURIComponent(ctx.duty)}`);
    if (!r.ok) return false;
    p = await r.json();
  } catch { return false; }
  const dd = p?.duty || {};
  let n = applyDutySettingsBlock(dd.mesh);
  // the duty's own operating point, as the catalog states it
  try {
    const put = (k: string, v: unknown) => { if (v != null) { localStorage.setItem('sim.' + k, JSON.stringify(v)); n++; } };
    put('current', p?.sim?.current_a); put('gamma', p?.sim?.gamma_deg);
    put('rpm', p?.sim?.rpm); put('frequency', p?.sim?.frequency);
    put('opMode', p?.sim?.mode);
    if (p?.sim?.connection) put('connection', p.sim.connection);
    // the duty's own saved setting first (see dutyLocalApply — a configuration
    // without `star_delta` must not flip the panel to star)
    const _sd = String(dd.star_delta
                       ?? (dd.mesh as Record<string, unknown> | undefined)?.['sim.starDelta']
                       ?? p?.sim?.star_delta ?? 'star');
    put('starDelta', _sd.toLowerCase().startsWith('d') ? 'delta' : 'star');
  } catch { /* quota */ }
  if (n > 0) {
    // eslint-disable-next-line no-console
    console.info(`[sim] panel settings were empty — restored ${n} keys from duty `
      + `${ctx.die}/${ctx.config}/${ctx.duty}`);
  }
  return n > 0;
}
