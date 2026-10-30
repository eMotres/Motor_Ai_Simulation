/**
 * ▶ — the WHOLE apply of a duty, server half + local half, in one function.
 *
 * Lifted out of FamilyCatalog.applyDuty (2026-09-20) so that the header
 * strip's "Discard live changes and reload ▶ die / config / duty" offer on a
 * RELEASED context can run exactly the load the Motors catalog runs — same
 * activate, same geometry PUT, same winding / materials / simulation PATCHes,
 * same local half — without the catalog having to be mounted.  One
 * implementation, so a fix to the load order can never reach one entry point
 * and not the other.
 *
 * OWNER (can_write): the duty is loaded into the SHARED server config.
 * ORDINARY USER: the duty becomes THIS CLIENT'S copy; nothing on the server
 * changes.  Both then run the local half (lib/dutyLocalApply).
 */
import { useMotorStore } from '../stores/motorStore';
import { clearDutyMaterialsKeys, setActiveDuty } from './dutySettings';
import {
  applyDutyLocal, fetchDutyPayload, leaveForDuty, type LocalApplyResult,
} from './dutyLocalApply';
import { beginDutyApply, endDutyApply } from './familyFollow';
import { canWriteServer, getStoredUser } from './localAuth';
import { loadStopReason } from './geometryApplyOutcome';
import { uiCanWrite } from './accessUi';
import { clearFamilyTarget } from './familyLoad';
import { beginLastMotorSelection,isCurrentLastMotorOperation, rememberLoadedMotor, restoreLastMotor } from './lastMotor';
import { guardLoad, loadFinished, type LoadPermit } from './loadGuard';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

/** Appended to the load message when the duty's saved results were computed
 *  on another build than the configuration's current one (activate's
 *  `geometry_stale`).  The duty still opens on the CURRENT build — the
 *  configuration is the design truth (owner 2026-10-29); the summary card
 *  marks the old numbers stale and offers Recompute. */
export const STALE_BUILD_NOTICE = ' — saved results are from an older build; Recompute';

/**
 * Apply `die / cfg / duty` everywhere.  Throws with a one-line reason when a
 * server step refuses; the caller shows it.  Resolves to the local half's
 * message ("point 60 A @ 20000 rpm …").
 *
 * FIRST it asks (lib/loadGuard): a running job, a run not saved to its duty or
 * geometry edits not saved all stand to be lost by this load, and nothing is
 * lost silently (owner 2026-10-29).  `permit` = the caller already asked (the
 * Motors tab asks before it switches the view); a person who answers Cancel
 * gets `{ declined: true }` and nothing has been touched.  `released`: the
 * caller's own confirmation already covered the unsaved work (the strip's
 * "discard and reload"), only a running job is still asked about.
 */
export async function applyDutyEverywhere(die: string, cfg: string, duty: string,
                                          canWrite: boolean,
                                          permit?: LoadPermit | null,
                                          opts: { released?: boolean } = {}): Promise<LocalApplyResult> {
  if (!permit) {
    const asked = await guardLoad({ die, config: cfg, duty }, { released: opts.released });
    if (!asked) return { message: '', machineChanged: false, declined: true };
  }
  const rememberedEmail = getStoredUser()?.email ?? null;
  const selectionEpoch = beginLastMotorSelection(rememberedEmail);
  let completed = false;
  // A poll in another part of THIS browser must not read the activate below
  // as "the machine changed elsewhere" and start following the duty we are
  // in the middle of applying (lib/familyFollow).
  beginDutyApply();
  // The owner's load writes the SHARED config (activate, geometry PUT, winding /
  // materials / simulation PATCH) - admin-only server-side.  The server's own
  // can_write is true for every registered account (own workspace), so the caller's
  // flag alone is not enough: a standard account takes the local-copy path below,
  // which writes nothing.  (Live 2026-10-05: the owner path ran for a regular user,
  // the geometry PUT was a client-local no-op, and the winding PATCH was then
  // validated against the workspace's OLD 24-slot machine - "Connection '2S'
  // invalid for 24 slots" - before simulation/mesh PATCHes answered 403.)
  canWrite = uiCanWrite(canWrite, canWriteServer());
  try {
    // Step 0, shared with the follower: file the panel state under the die
    // and the duty being LEFT, and say what they were (the machine-change
    // test the local half needs for the coupled-loop temperatures).
    const prev = leaveForDuty(die, cfg, duty);
    const p = await fetchDutyPayload(die, cfg, duty);
    if (rememberedEmail
        && !isCurrentLastMotorOperation(rememberedEmail, selectionEpoch)) {
      throw new Error('account changed while loading motor duty');
    }
    // From this line on, the panel is editing THIS duty: every
    // operating-point field it writes is filed under this key (and never
    // under the duty we just left, which is why the marker moves BEFORE the
    // first sim.* write rather than after the last one).  Claimed HERE, not
    // only inside the local half below, because the server writes in between
    // can move panel fields (the battery's V_bus prefill) and those belong to
    // the incoming duty.  applyDutyLocal repeats both — idempotent.
    try { setActiveDuty(die, cfg, duty); } catch { /* quota */ }
    // The MATERIALS are per-duty too, and their default is "the machine's
    // own" — an ABSENT key, not a value.  So they are cleared here, before
    // this duty's snapshot (the `materials:` dict applied below) and its
    // overlay (restoreDutyOp, inside applyDutyLocal) get to state their own: without
    // the clear, a duty that never picked any would keep solving with the
    // magnet and the steel the PREVIOUS duty chose.  lib/dutySettings.ts.
    try { clearDutyMaterialsKeys(); } catch { /* nothing to clear */ }
    const { updateGeometryViaApi } = useMotorStore.getState();
    // Set when activate says the duty's saved results were computed on another
    // build — appended to the local half's message once the load lands, so the
    // panel says the dashboard is stale before the owner wonders why Run gives
    // different numbers than the catalog just showed.
    let staleGeometryNotice = '';
    if (canWrite) {
      // ── OWNER: load the duty into the SHARED server config ─────────────
      // -1) DROP any queued geometry edits: they belong to the machine that
      //     is being replaced, and a debounced replay landing after the
      //     context switch would save a foreign machine into the new die
      //     (the backend's stranger guard refuses it too — this closes the
      //     race at the source; incident 2026-08-24).
      useMotorStore.setState({ pendingGeometryEdits: null });
      // 0) mark WHICH die/config/duty the editor is about to become — the
      //    geometry route enforces the locks against this context, and
      //    applying the configuration's own canonical values passes.
      //    A duty ALWAYS opens on the configuration's current build (owner
      //    2026-10-29): there is no choice to make and nothing is written.
      //    Results saved on another build come back as `geometry_stale` and
      //    are shown stale, never as valid.  `keep_die` is sent so a server
      //    that still asks (the old 409 dialog) takes the same path.
      const ar = await fetch(`${API}/api/family/activate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ die, config: cfg, duty, geometry_choice: 'keep_die' }),
      });
      if (ar.ok) {
        try {
          const aj = await ar.clone().json();
          if (aj?.geometry_stale || (aj?.geometry_diffs ?? []).some(
            (x: { scope?: string }) => x?.scope && x.scope !== 'free')) {
            staleGeometryNotice = STALE_BUILD_NOTICE;
          }
        } catch { /* no body — nothing to say */ }
      }
      // A failed activate used to be SILENT and the geometry PUT below still
      // ran — the live editor then held this machine while the server
      // context still named the previous die, and the die sync wrote this
      // machine into THAT die.  Nothing may be applied when the context
      // could not follow.  "sign in again and retry" is added ONLY for an
      // auth failure (401/403): appending it to every refusal — a locked die
      // (423), a foreign lamination (422) — told the owner to re-authenticate
      // for a problem that had nothing to do with his session (incident
      // 2026-09-29: "die 'CIANO14 40 new' is locked — unlock it … — sign in
      // again and retry" on a plain lock).
      if (!ar.ok) {
        let why = `HTTP ${ar.status}`;
        try {
          const dt = (await ar.json()).detail;
          why = (dt && typeof dt === 'object' ? dt.message : dt) ?? why;
        } catch { /* no body */ }
        const authHint = (ar.status === 401 || ar.status === 403)
          ? ' — sign in again and retry' : '';
        throw new Error(`cannot activate ${die} / ${cfg}: ${why}${authHint}`);
      }
      // 1+2) geometry AND winding connection, as ONE change.  The winding is
      // validated against the incoming slot count before anything is written
      // and both land together (PUT /api/geometry/load), so the previous
      // machine's winding never meets the new geometry - the "winding
      // connection '4S' is not valid for 48 slots" snap of a 3-phase 24-slot ->
      // six-phase 48-slot switch (owner 2026-10-30).  A refused geometry write
      // (die/configuration lock, bad value) or winding stops the load here.
      // phases is always sent, so a 3-phase load also clears a previous
      // 6-phase one; the six-phase split travels with the connection.
      const six = Number(p.winding?.phases) === 6;
      const windingForLoad = p.sim.connection ? {
        connection: p.sim.connection,
        layers: p.winding?.layers,
        phases: six ? 6 : 3,
        ...(six ? {
          set1_paths: String(p.winding?.set1_paths ?? ''),
          set_neutrals: String(p.winding?.set_neutrals || 'isolated'),
        } : {}),
      } : undefined;
      const stop = loadStopReason(die, cfg,
        await updateGeometryViaApi(p.geometry, { winding: windingForLoad }));
      if (stop) throw new Error(stop);
      // 2b) the build's materials — a configuration is a physical product;
      //     loading it must load what it is made of.  EVERY part the payload
      //     names (2026-09-09): it now spells out the liner and the enamel
      //     too, because loading only the three build parts left the
      //     previous machine's Al2O3 liner on every motor after the Ø200.
      for (const [part, mat] of Object.entries(p.materials ?? {})) {
        if (!mat) continue;
        const mr = await fetch(`${API}/api/materials`, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ part, material: mat }),
        });
        if (!mr.ok) throw new Error((await mr.json()).detail ?? `materials HTTP ${mr.status}`);
      }
      // The PATCHes above bypass useMotorAssignments.assign(), so NOTHING
      // told the other hook instances — including the one that feeds ?mat=
      // to the solver (MaterialOverrideSync).  It kept the PREVIOUS machine's
      // assignment and every run after this load was solved with it: the G2
      // generator (B15AHV950M) ran on the 85 mm die's 20SW1200 all morning
      // 2026-09-02 (+3.9 % iron, +3.8 % torque, +10 % core loss), invisible
      // on the panel because only the magnet is shown there.  Same bug class
      // as 2026-08-25, from the other entry point.  Broadcast, always.
      try { window.dispatchEvent(new CustomEvent('mat-assign-changed')); } catch { /* SSR */ }
      // 3) shared simulation config — what the sweep/optimizer read off-tab
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const simPatch: any = {
        max_current: p.sim.current_a, rpm: p.sim.rpm, frequency: p.sim.frequency,
        phase_offset_deg: p.sim.gamma_deg, mode: p.sim.mode,
        connection: p.sim.connection,
        star_delta: p.sim.star_delta ?? 'star',
      };
      Object.keys(simPatch).forEach(k => simPatch[k] == null && delete simPatch[k]);
      // The d-axis pin is a property of the DIE's topology, so it is sent
      // ALWAYS — a blank ('' = measure) when the new machine carries none.
      // Skipping it left the previous machine's pin in the shared config:
      // the G2's 60° (24s/28p) was applied to the CIANO10 200 opt (12s/10p,
      // its own d-axis 120°) and every Run was refused as "d-axis pin 60°
      // does not belong to this machine" while the field on screen was
      // empty (user 2026-09-09: "the simulation won't start").  The local
      // field was already cleared (lib/dutyLocalApply) — the config was not.
      simPatch.daxis_deg = (p.sim.daxis_deg != null && Number.isFinite(Number(p.sim.daxis_deg)))
        ? p.sim.daxis_deg : '';
      const sr = await fetch(`${API}/api/simulation/config`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(simPatch),
      });
      if (!sr.ok) throw new Error((await sr.json()).detail ?? `sim HTTP ${sr.status}`);
    } else {
      // ── ORDINARY USER: the duty becomes THIS CLIENT'S COPY ─────────────
      // Nothing on the server changes.  The geometry goes into the local
      // store (every compute request carries it as ?geo=), the operating
      // point into the panel's localStorage below, the materials into the
      // local assignment overlay (sent as ?mat=), and the strip shows the
      // copied die/config/duty from local context.
      await updateGeometryViaApi(p.geometry);   // local-mode merge, no PUT
      try {
        const cur = JSON.parse(localStorage.getItem('mat.assign.local') || '{}');
        for (const [part, mat] of Object.entries(p.materials ?? {})) {
          if (mat) cur[part] = mat;
        }
        localStorage.setItem('mat.assign.local', JSON.stringify(cur));
        window.dispatchEvent(new CustomEvent('mat-assign-local-changed'));
      } catch { /* quota — materials stay whatever they were */ }
    }
    // 4) THE LOCAL HALF — panel-owned persisted values, the live nudges,
    //    the duty's settings block, its materials and its stored runs.  It
    //    is the SAME function a second browser runs when it notices this
    //    load in /api/family/context (lib/dutyLocalApply, lib/familyFollow),
    //    so the two paths cannot drift apart.
    const result = await applyDutyLocal(die, cfg, duty, p, prev, canWrite);
    completed = true;
    // The requested-motor marker (lib/familyLoad) stays until the account's record names the motor; where no record will
    // ever be written (anonymous, or the remember step fails) it is dropped here so it cannot outlive the load.
    if (rememberedEmail) void rememberLoadedMotor(rememberedEmail, die, cfg, duty, selectionEpoch).catch(() => {
      // The local load is complete even if cross-device memory is unavailable.
      clearFamilyTarget({ die, config: cfg });
    });
    else clearFamilyTarget({ die, config: cfg });
    if (staleGeometryNotice) result.message += staleGeometryNotice;
    loadFinished();
    return result;
  } finally {
    endDutyApply();
    if (!completed && rememberedEmail
        && isCurrentLastMotorOperation(rememberedEmail, selectionEpoch)) {
      void restoreLastMotor(rememberedEmail).catch(() => {});
    }
  }
}
