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
import { dutyGeometryCancelledMessage, type DutyGeometryDiffRow } from './dutyGeometryDiff';
import { askDutyGeometryChoice } from './dutyGeometryDialogService';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

/**
 * Ask the owner which geometry wins when the duty's saved machine differs
 * from the die: a real modal (DutyGeometryDialog, via
 * lib/dutyGeometryDialogService) with the rounded diff table and three
 * explicit buttons — "load with die geometry" (nothing written), "apply duty
 * geometry" (writes the die / configuration, history-snapshotted, disabled
 * when the die is locked or the lamination itself differs) and Cancel.
 * Replaces a raw `window.confirm()` (incident 2026-09-29: unrounded floats,
 * an ambiguous OK/Cancel the owner read backwards).  Returns null only when
 * no dialog could be shown, or it was dismissed without an answer — the load
 * then stops exactly as a Cancel would.
 */
export async function chooseDutyGeometry(message: string, diffs: DutyGeometryDiffRow[],
                                         dieLocked: boolean): Promise<'apply_duty' | 'keep_die' | null> {
  try {
    return await askDutyGeometryChoice({ message, diffs, dieLocked });
  } catch {
    return null;
  }
}

/**
 * Apply `die / cfg / duty` everywhere.  Throws with a one-line reason when a
 * server step refuses; the caller shows it.  Resolves to the local half's
 * message ("point 60 A @ 20000 rpm …").
 */
export async function applyDutyEverywhere(die: string, cfg: string, duty: string,
                                          canWrite: boolean): Promise<LocalApplyResult> {
  // A poll in another part of THIS browser must not read the activate below
  // as "the machine changed elsewhere" and start following the duty we are
  // in the middle of applying (lib/familyFollow).
  beginDutyApply();
  try {
    // Step 0, shared with the follower: file the panel state under the die
    // and the duty being LEFT, and say what they were (the machine-change
    // test the local half needs for the coupled-loop temperatures).
    const prev = leaveForDuty(die, cfg, duty);
    const p = await fetchDutyPayload(die, cfg, duty);
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
    // Set inside the 409 branch below when the owner picks "keep die
    // geometry" — appended to the local half's message once the load lands,
    // so the panel says the dashboard is stale before the owner wonders why
    // Run gives different numbers than the catalog just showed.
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
      const activate = (geometry_choice?: string) => fetch(`${API}/api/family/activate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ die, config: cfg, duty, geometry_choice }),
      });
      let ar = await activate();
      // 409 = the duty was saved on a different geometry than the die (+config).
      // The server wrote NOTHING; the owner chooses, explicitly (incident
      // 2026-09-27: a load silently rewrote the die's winding and stack).
      // "keep_die" leaves the results the panel will show STALE — the
      // dashboard was computed on the duty's own (different) geometry — so
      // the local half's message says so once the load lands (below).
      if (ar.status === 409) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        let det: any = {};
        try { det = (await ar.json()).detail ?? {}; } catch { /* no body */ }
        if (det?.code !== 'duty_geometry_differs') {
          throw new Error(String(det?.message ?? det ?? `HTTP ${ar.status}`));
        }
        const choice = await chooseDutyGeometry(String(det.message ?? ''),
          det.diffs ?? [], det.die_locked === true);
        if (!choice) throw new Error(dutyGeometryCancelledMessage());
        if (choice === 'keep_die') {
          staleGeometryNotice = ` — saved results of ${duty} were computed on `
            + 'a different geometry — re-run to refresh them';
        }
        ar = await activate(choice);
      }
      // with apply_duty the files changed — reread what the load will PUT
      if (ar.ok) {
        try {
          const aj = await ar.clone().json();
          if (aj?.geometry_applied) Object.assign(p, await fetchDutyPayload(die, cfg, duty));
        } catch { /* keep the payload read before */ }
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
      // 1) geometry — the die's stamped section + this configuration's stack/wire
      await updateGeometryViaApi(p.geometry);
      // 2) winding connection (authoritative endpoint; validates against layout)
      if (p.sim.connection) {
        const wr = await fetch(`${API}/api/winding/config`, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ connection: p.sim.connection,
                                 layers: p.winding?.layers }),
        });
        if (!wr.ok) throw new Error((await wr.json()).detail ?? `winding HTTP ${wr.status}`);
      }
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
      try {
        localStorage.setItem('family.localContext',
          JSON.stringify({ die, config: cfg, duty, at: Date.now() }));
      } catch { /* quota */ }
    }
    // 4) THE LOCAL HALF — panel-owned persisted values, the live nudges,
    //    the duty's settings block, its materials and its stored runs.  It
    //    is the SAME function a second browser runs when it notices this
    //    load in /api/family/context (lib/dutyLocalApply, lib/familyFollow),
    //    so the two paths cannot drift apart.
    const result = await applyDutyLocal(die, cfg, duty, p, prev, canWrite);
    if (staleGeometryNotice) result.message += staleGeometryNotice;
    return result;
  } finally {
    endDutyApply();
  }
}
