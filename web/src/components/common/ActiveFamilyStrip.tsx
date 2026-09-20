/**
 * ActiveFamilyStrip — the always-visible line under the tabs saying WHICH
 * die / configuration / duty is loaded in the editor, whether the Simulation
 * panel has drifted off that duty's operating point, and (for writers) a
 * one-click "Save to duty" that writes the CURRENT point back into it —
 * recording the last run's results too when that run matches the point.
 */
import React, { useEffect, useRef, useState } from 'react';
import { Box, Typography, Button, Tooltip, CircularProgress } from '@mui/material';
import { useMotorStore } from '../../stores/motorStore';
import { pageVisible } from '../../lib/pageVisible';
import {
  ConfirmDialog, TextPromptDialog, type ConfirmState, type TextPromptState,
} from './PromptDialogs';
import { applyDutyEverywhere } from '../../lib/dutyApply';
import { releasedOffers, type ReleasedCtxLike } from '../../lib/releasedContext';
import { rememberDieSettings } from '../../lib/dieSettings';
import {
  clearDutyCycle, clearDutyOp, dutyKey, readDutyCycle, rememberDutyOp,
  setActiveDuty, ASSIGN_KEY, MAGNET_KEY,
} from '../../lib/dutySettings';
import {
  adoptionDecision, dutyApplyInProgress, markContextReleased,
  readAppliedContext, rememberAppliedContext, solveBusyHere, solverRunning,
} from '../../lib/familyFollow';
import { followActiveDuty } from '../../lib/dutyLocalApply';
import { driveLabel } from '../../lib/dutyRuns';
import { assignmentSignature } from '../../lib/dutyMaterials';
import { currentMatJson } from '../../lib/apiAuth';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

/** The efficiency a duty is SAVED with — the one the summary card shows.
 *
 * User 2026-09-11: *"почему у нас сохраняется другая цифра?"* — the card read
 * 98.46 % and the catalog row 98.73 %, and the row was wrong twice over:
 *
 *   1. it saved the ELECTROMAGNETIC efficiency, while the standing rule
 *      (2026-09-09, "one efficiency, at the shaft") is that the one number a
 *      reader sees carries bearings and windage too;
 *   2. it used the MOTORING expression P_mech/(P_mech+losses) on a machine
 *      running as a GENERATOR, where the losses come off the input rather than
 *      adding to it — on the live Ø200 that alone was +0.02 pp.
 *
 * So it is computed here exactly as `SummaryTable` computes the tile: shaft
 * when the mechanical loss is known, electromagnetic when it is not (no
 * bearings on the machine = the mechanical term is UNKNOWN, not zero), and the
 * direction taken from the run's own `op_mode`.  Two places, one formula — if
 * that ever has to move, it moves into a lib both import.
 */
export function dutyEfficiencyPct(s: any, pMechW: number): number {
  const pm = Math.abs(Number(pMechW) || 0);
  const loss = Number(s?.P_loss_total_W) || 0;
  const brg = Number(s?.P_bearings_W);
  const wind = Number(s?.P_windage_W);
  const known = Number.isFinite(brg) || Number.isFinite(wind);
  const extra = (Number.isFinite(brg) ? brg : 0) + (Number.isFinite(wind) ? wind : 0);
  const gen = s?.op_mode === 'generator';
  if (pm <= 0) return (Number(s?.efficiency) || 0) * 100;
  const pElec = gen ? Math.max(0, pm - loss) : pm + loss;
  if (!known) return (gen ? pElec / pm : pm / pElec) * 100;
  const pShaft = gen ? pm + extra : Math.max(0, pm - extra);
  return (gen ? (pShaft > 0 ? pElec / pShaft : 0)
              : (pElec > 0 ? pShaft / pElec : 0)) * 100;
}



interface Ctx {
  active: boolean; die?: string; config?: string; duty?: string | null;
  die_locked?: boolean; config_locked?: boolean; can_write?: boolean;
  duty_point?: { current_arms: number; rpm: number; gamma_deg: number;
                 mode: string } | null;
  build?: { stack_mm?: number | null; wire_height_mm?: number | null;
            turns?: number | null } | null;
}

const readLS = (k: string, d: any) => {
  try { const v = localStorage.getItem('sim.' + k); return v == null ? d : JSON.parse(v); }
  catch { return d; }
};

/** "23:05" — the clock time, which is how the user remembers when they loaded
 *  the other machine. */
const hhmm = (ms: number) => {
  const d = new Date(ms);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
};

const ActiveFamilyStrip: React.FC = () => {
  const [ctx, setCtx] = useState<Ctx | null>(null);
  const liveGeometry = useMotorStore(s => s.geometry) as Record<string, unknown>;
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  // A refused save because the on-screen BUILD differs from the configuration
  // — offers "save as new configuration" instead of leaving a dead end.
  const [buildClash, setBuildClash] = useState(false);
  const [askCfg, setAskCfg] = useState<TextPromptState | null>(null);
  // The RELEASED-context offers (2026-09-20): a new die's name, a new
  // configuration's name, or the "discard and reload" confirmation.
  const [askDie, setAskDie] = useState<TextPromptState | null>(null);
  const [askReload, setAskReload] = useState<ConfirmState | null>(null);
  // ticks so the drift marker re-evaluates while the user types in the panel
  const [, setTick] = useState(0);

  const load = () => fetch(`${API}/api/family/context`)
    .then(r => r.json()).then((j: Ctx) => {
      // Ordinary user (no write rights): the server context is the OWNER's
      // machine, not this client's.  When the user has ▶-copied a duty, the
      // strip names THEIR copy from local context instead.
      if (!j?.can_write) {
        try {
          const loc = JSON.parse(localStorage.getItem('family.localContext') || 'null');
          if (loc?.die && loc?.config) {
            setCtx({ active: true, die: loc.die, config: loc.config,
                     duty: loc.duty ?? null, can_write: false });
            return;
          }
        } catch { /* fall through to the server context */ }
      }
      setCtx(j);
    }).catch(() => setCtx(null));
  useEffect(() => {
    load();
    const onChange = () => { void load(); };
    window.addEventListener('family-changed', onChange);
    window.addEventListener('sim-design-applied', onChange);
    const onOp = () => setTick(t => t + 1);
    window.addEventListener('sim-operating-point', onOp);
    // The context is RE-FETCHED on the tick, not just re-rendered: the events
    // above are same-document, so a duty loaded in ANOTHER browser (or another
    // tab) reached this strip only on an F5.  It is what makes the follower
    // below possible at all — and what the header has to do anyway to stop
    // naming a machine somebody else replaced.
    const id = setInterval(() => {
      if (!pageVisible()) return;        // a hidden tab polls nothing (lib/pageVisible)
      setTick(t => t + 1); void load();
    }, 5000);
    return () => {
      window.removeEventListener('family-changed', onChange);
      window.removeEventListener('sim-design-applied', onChange);
      window.removeEventListener('sim-operating-point', onOp);
      clearInterval(id);
    };
  }, []);

  // ── FOLLOW a machine loaded in another browser ──────────────────────────────
  // The header already showed the new die; the Electromagnetic panel did not
  // know, because its operating point lives in THIS browser's localStorage and
  // only ▶ writes it.  The first Run then PATCHed the previous machine's point
  // into the shared config and solved it here (2026-09-08 23:23 — the Ø200's
  // 687 A / 20 900 rpm on a 40 mm generator: 194 kW, 11 891 V, "thermal
  // runaway" to 48 873 °C).  Now: when the polled context names a die/config/
  // duty this browser's panel has not applied, that duty's point is adopted —
  // the LOCAL half of ▶ and nothing else (lib/dutyLocalApply): no activate, no
  // geometry PUT, no winding / materials / simulation PATCH, because the
  // browser that pressed ▶ already made every one of them.
  const [adopted, setAdopted] =
    useState<{ config: string; duty: string; at: number } | null>(null);
  const following = useRef(false);
  useEffect(() => {
    if (!ctx) return;
    const applied = readAppliedContext();
    const verdict = adoptionDecision(ctx, applied);
    if (verdict === 'release') { markContextReleased(); return; }
    if (verdict === 'seed') {
      // Baseline only — a browser that has never applied a duty must not have
      // its own un-saved point reset by a page reload ("≠ point changed" is a
      // legitimate state and F5 must not undo it).
      rememberAppliedContext(String(ctx.die), String(ctx.config), ctx.duty ?? null,
                             (ctx as { at?: string | null }).at ?? null);
      return;
    }
    if (verdict !== 'adopt') return;
    if (following.current || dutyApplyInProgress()) return;
    // NEVER under a solve in flight: a run's fetch is already out with the
    // fields as they are, and swapping them mid-flight leaves the charts
    // describing one point and the panel another.  Postponed, not dropped —
    // the 5 s poll above brings this effect straight back.
    if (solveBusyHere()) return;
    following.current = true;
    void (async () => {
      try {
        if (await solverRunning()) return;
        // Queued geometry edits belong to the machine that was just replaced;
        // a debounced replay landing now would save a foreign machine into the
        // new die (same guard the catalog's own ▶ raises).  Dropping them also
        // makes the refresh below a plain GET.
        useMotorStore.setState({ pendingGeometryEdits: null });
        await useMotorStore.getState().fetchGeometryFromApi();
        await followActiveDuty(String(ctx.die), String(ctx.config), String(ctx.duty));
        setAdopted({ config: String(ctx.config), duty: String(ctx.duty),
                     at: Date.now() });
      } catch { /* a failed follow simply retries on the next poll */ }
      finally { following.current = false; }
    })();
  }, [ctx]);

  // Has the Simulation panel drifted off the loaded duty's operating point?
  // (`ctx` may still be null or RELEASED here — the released branch renders
  // below, after the save closures it shares are defined.)
  const p = ctx?.duty_point ?? null;
  const cur = Number(readLS('current', NaN));
  const rpm = Number(readLS('rpm', NaN));
  const gam = Number(readLS('gamma', NaN));
  const mode = readLS('opMode', 'motor');
  const near = (a: number, b: number, tol: number) =>
    Number.isFinite(a) && Number.isFinite(b) && Math.abs(a - b) <= tol;
  const drifted = !!p && !(
    near(cur, p.current_arms, Math.max(0.05, 0.001 * p.current_arms))
    && near(rpm, p.rpm, 0.5) && near(gam, p.gamma_deg, 0.01)
    && mode === p.mode);

  const lockGlyph = ctx?.die_locked && ctx?.config_locked ? '🔒🔒'
                  : ctx?.die_locked ? '🔒' : '🔓';
  const lockTip = ctx?.die_locked && ctx?.config_locked
    ? 'Die AND configuration locked — geometry is read-only; only Simulation parameters move'
    : ctx?.die_locked
      ? 'Die locked — stack length, wire and turns are editable; stamped geometry is not'
      : 'Nothing locked — the whole geometry is editable';

  // Save the CURRENT Simulation point back into the loaded duty; then, when
  // the LAST finished run sits exactly on that point, record its results too.
  const save = async (target?: { die?: string; config?: string; duty?: string }) => {
    // `setCtx` is asynchronous.  The "save as new configuration" and "save as
    // new die" flows must therefore pass the freshly-created canonical names
    // explicitly; otherwise this closure still writes to the triple that was
    // active before (or to none at all, on a released context).
    const tDie = String(target?.die || ctx?.die || '');
    const tDuty = String(target?.duty || ctx?.duty || '');
    const targetConfig = String(target?.config || ctx?.config || '');
    if (!tDie || !tDuty || !targetConfig) return;
    setBusy(true); setMsg(null);
    try {
      // Does the LAST finished run sit on the point being saved?  Then its
      // MEASURED torque/power replace the duty's stored kW/Nm (an old target
      // must not outlive a real run at the new point), and its results are
      // recorded right after.
      const s = readLS('lastSummary', null);
      // Match the run by its INPUTS (current, speed, γ, the REQUESTED mode).
      // s.op_mode is derived from the power-flow sign — a generator-mode run
      // near a zero-crossing reads "motor" there, and comparing it against
      // the panel toggle silently refused to record honest results (seen
      // live: γ=197° generator point saved twice with no results).  γ is
      // compared too — same I and rpm at a different angle is a different
      // point.  Older summaries lack op_mode_requested: the γ+I+rpm match is
      // then decisive.
      const runMatches = !!(s
        && near(Number(s.I_terminal_rms_A ?? s.I_phase_rms_A), cur, Math.max(0.5, 0.002 * cur))
        && near(Number(s.rpm), rpm, 1)
        && near(Number(s.gamma_deg), gam, 0.05)
        && (s.op_mode_requested == null || s.op_mode_requested === mode));
      // ── WHICH EXCITATION produced it ──────────────────────────────────────
      // The duty's PRIMARY result is the sine-current run — that is the row the
      // catalog shows and what everything is compared against.  A PWM (or BLDC)
      // run of the same point is a different question about that point, so it
      // is filed under `runs[<drive>]` and leaves the primary alone.  Saving it
      // over the primary is what turned every later Run into a twelve-minute
      // PWM solve, because loading the duty restored the PWM settings
      // (2026-09-02).  The summary itself names the source it was solved from
      // (backend `summary.drive`); the panel toggle is only the fallback for a
      // summary old enough not to carry it.
      const drive = String((s as any)?.drive || readLS('drive', 'current') || 'current');
      // The WAVEFORMS of that same run, for the stored-run sidecar — but only
      // when the cached transient IS this summary's run.  `sim.lastSummary` can
      // hold an APPLIED summary (a Sweep design, a restored duty) while
      // `sim.lastTransient` still holds an older solve; storing that pair would
      // file one run's charts under another run's numbers.  The summary inside
      // the transient is the one this card was built from, so the two have to
      // be the SAME RUN, not merely describe the same point (2026-09-13: two
      // duties were saved with the numbers of one run and no waveforms at all
      // because a same-point test is the only question the save could ask).
      //
      // Is THIS payload the run `s` describes?  `computed_at` is the run's own
      // identity and the summary now carries it (`_runAt`, stamped in
      // TransientCharts); when either side predates the stamp, fall back to the
      // old same-point test, which is all those runs can offer.
      const isRunOf = (t: any): boolean => {
        if (!t?.summary || !s) return false;
        if (!Array.isArray(t.time_s) || !t.time_s.length) return false;
        const at = (s as any)._runAt;
        if (at && t.computed_at) return String(t.computed_at) === String(at);
        return near(Number(t.summary.rpm), Number(s.rpm), 1e-6)
          && near(Number(t.summary.I_phase_rms_A), Number(s.I_phase_rms_A), 1e-6)
          && near(Number(t.summary.gamma_deg), Number(s.gamma_deg), 1e-6)
          && String(t.summary.drive ?? 'current') === drive;
      };
      const lastT = readLS('lastTransient', null) as any;
      let tOfRun: any = isRunOf(lastT) ? lastT : null;
      // The browser copy can be BEHIND the server's: a coupled run whose
      // payload never reached localStorage, a run made in another tab, a
      // reload mid-run.  The save then filed the numbers without their charts
      // and the report lost its torque / current / voltage figures (user
      // 2026-09-13, twice: 14:29 and 21:01, both right after a coupled run —
      // the backend's own copy of that very run was correct both times).  The
      // server's last transient IS the run this card was built from — ask for
      // it (restore=true solves nothing) and take it when it is that run.
      if (!tOfRun && s) {
        try {
          const rr = await fetch(`${API}/api/kernel/run`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ capability: 'solver.em_transient',
                                   payload: { restore: true } }),
          });
          const jj = rr.ok ? await rr.json() : null;
          const bt = jj?.result?.raw;
          if (isRunOf(bt)) tOfRun = bt;
        } catch { /* offline — reported as "no waveforms" below */ }
      }
      // WYSIWYG save (user's call): if the 3D ×k_flux toggle is ON, the
      // catalog gets the SAME corrected numbers the screen shows — torque,
      // power and voltages ×k, efficiency recomputed on the corrected power
      // (losses stay 2D, same as the display), and the applied k recorded
      // in the result so the row never hides that it is corrected.
      const k3d = ((): number | null => {
        try {
          return localStorage.getItem('sim.apply3d') === '1' && s?.end3d?.k_flux
            ? Number(s.end3d.k_flux) : null;
        } catch { return null; }
      })();
      const Tn = Math.abs(Number(s?.T_em_avg_Nm)) * (k3d ?? 1);
      // The duty's kW is the SHAFT power — the number the card's "Mech power"
      // tile shows (SummaryTable `pShaft`): rotor T·ω plus the bearings and
      // windage a generator's prime mover must also supply, minus them on a
      // motor.  It used to be the rotor T·ω alone, so the catalog row printed
      // 496.8 kW beside a loss column that already included the 1.3 kW of
      // friction and a shaft efficiency — three numbers that did not agree
      // with each other or with the card (user 2026-09-13: "почему разные
      // значения, я только что сохранил мотор").  One efficiency, at the
      // shaft (2026-09-09) — and one power to go with it.  Unknown mechanical
      // loss (no bearings named) leaves the rotor number, as the card does.
      const PmW = ((): number => {
        const rotor = Math.abs(Number(s?.P_mech_W)) * (k3d ?? 1);
        const extra = Number(s?.P_mech_extra_W);
        if (!Number.isFinite(extra)) return rotor;
        return mode === 'generator' ? rotor + extra : Math.max(0, rotor - extra);
      })();
      const dutyBody: any = { name: tDuty, mode, from_current: true };
      if (runMatches) {
        // The backend routes the snapshot on this: `current` owns the primary,
        // everything else is stored beside it.
        dutyBody.drive = drive;
        // The MATERIAL ASSIGNMENT this run was solved with — the run's own
        // stamp when it has one (TransientCharts writes `_matSig` on a fresh
        // solve), the live assignment otherwise.  Stored so a re-assignment
        // under a saved run can flag it instead of silently changing what its
        // numbers mean.
        try {
          const ms = String(tOfRun?._matSig || assignmentSignature(currentMatJson()) || '');
          if (ms) dutyBody.assignment_sig = ms;
        } catch { /* the signature is a staleness hint, never a blocker */ }
        dutyBody.torque_nm = Tn || undefined;
        dutyBody.power_kw = PmW / 1000 || undefined;
        // COMPLETE computed state rides the duty (user: "это всё должно
        // сохраняться"): the mesh the numbers were solved on + the raw
        // summary (raw, not the 3D-scaled view — the display toggles
        // re-derive their corrections from it on load).
        try {
          // EVERY panel setting (user: "сохранять всё что можно"): the full
          // mesh.* and sim.* localStorage state, keyed verbatim so restore is
          // a plain write-back.  Heavy result caches are excluded — they are
          // not settings.
          // runNonce is the RUN TRIGGER, not a setting: saving it and then
          // writing it back on load made the panel see a changed nonce and
          // START A SOLVE BY ITSELF (user 2026-08-25: "кто опять включил
          // расчёт?").  Result caches are excluded for the same "not a
          // setting" reason.
          const SKIP = new Set(['sim.lastTransient', 'sim.lastSummary',
                                'sim.viewSummary', 'sim.runNonce',
                                // machine-owned, not a panel setting: the DC
                                // link derives from the battery of whatever
                                // machine is loaded
                                'sim.vBus']);
          const settings: Record<string, unknown> = {};
          for (let i = 0; i < localStorage.length; i++) {
            const k = localStorage.key(i)!;
            if (!k.startsWith('mesh.') && !k.startsWith('sim.')) continue;
            if (SKIP.has(k)) continue;
            try { settings[k] = JSON.parse(localStorage.getItem(k)!); }
            catch { settings[k] = localStorage.getItem(k); }
          }
          dutyBody.mesh = settings;
        } catch { /* settings block optional — the save itself must not fail */ }
        dutyBody.summary = s;
      }
      // ── the duty's MATERIALS ────────────────────────────────────────────────
      // A separate dict on the duty entry, NOT part of the mesh/settings block:
      // it is not a panel setting, it is what this duty is made of (user
      // 2026-09-01: "все материалы, для каждого duty"), and it must be saved
      // even when no matching run backs the click — the settings block is
      // written only on a matching run, and a material pick would otherwise
      // never reach the yaml.  A `null` entry is this duty SAYING "the
      // machine's own" and is sent as such.  Sent only when the duty actually
      // has an opinion: a duty that runs the machine's materials keeps a body
      // byte-identical to the ones written before this feature existed, and the
      // restore side reads an absent dict as "the machine's".
      try {
        const raw = localStorage.getItem(ASSIGN_KEY);
        const map = raw == null ? null : JSON.parse(raw);
        if (map && typeof map === 'object' && Object.keys(map).length) {
          dutyBody.materials = map;
        } else {
          // LEGACY (2026-09-01, morning): a magnet-only pick under its own key,
          // from a session that predates the map.  Saved in the new shape so
          // the yaml never learns the retired key.
          const mg = localStorage.getItem(MAGNET_KEY);
          const mv = mg == null ? null : JSON.parse(mg);
          if (typeof mv === 'string' && mv) dutyBody.materials = { magnet: mv };
        }
      } catch { /* no picks — the machine's materials it is */ }
      // ── the duty's DUTY CYCLE ───────────────────────────────────────────────
      // (2026-09-14)  Sent exactly the way `materials` above is, and for the
      // same reason: it is not a panel setting and not excitation-specific —
      // it is what the machine DOES with this point — so it must reach the yaml
      // even when no matching run backs the click.  The settings block is
      // written only on a matching run, and a cycle the user just wrote in the
      // editor would otherwise never be saved at all.  The backend validates it
      // by name (routes/family._validate_duty_cycle) and writes it on every
      // save; sent only when the duty actually has one, so a duty that has
      // never defined a cycle keeps a body byte-identical to the ones written
      // before this feature existed.
      let dcSent: Record<string, unknown> | null = null;
      try {
        dcSent = readDutyCycle(dutyKey(tDie, targetConfig, tDuty));
        if (dcSent) dutyBody.duty_cycle = dcSent;
      } catch { /* no cycle — the continuous point it stays */ }
      // NOTE for the overlay bookkeeping further down: `mesh` is the ONLY
      // place the snapshot keeps coil temp / drive / voltages, it is written
      // ONLY on the matching-run branch above, and the backend REPLACES it
      // wholesale (routes/family.py: `entry[k] = dict(v)`).  So a save
      // without a matching run must not send a partial block — it would
      // erase the mesh this duty was solved on — and it must not be treated
      // as having captured the point either.
      const r = await fetch(`${API}/api/family/duty`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ die: tDie, config: targetConfig, duty: dutyBody }),
      });
      if (!r.ok) throw new Error((await r.json()).detail ?? `HTTP ${r.status}`);
      // The save may have AUTO-RENAMED the configuration (M1-L200 -> M1-L220
      // when the stack changed) — the result recording must follow the NEW
      // name, or it 404s and the fresh run's numbers are silently lost
      // (measured: exactly that happened on the first renamed save).
      const rj = await r.json().catch(() => ({} as any));
      // A successful save is the strongest possible statement that the panel's
      // settings ARE this die's settings — remember them, so loading any duty
      // of this die later restores the user's own mesh, not a stray snapshot.
      try { rememberDieSettings(tDie); } catch { /* convenience */ }
      const cfgName = (rj && rj.config) ? String(rj.config) : targetConfig;
      // The duty's SNAPSHOT now states this point, so the local per-duty
      // overlay has nothing left to say — dropping it is what makes a save
      // stick: otherwise the pre-save edit would keep winning on every later
      // selection of this duty (lib/dutySettings.ts).  Both names are cleared
      // because the save may have auto-renamed the configuration.
      //
      // ONLY when a matching run put the settings block into the snapshot.
      // Without one the yaml took the canonical point (I / rpm / γ / mode,
      // via from_current) but NOT the coil temp or the drive — the overlay is
      // still the only record of those, and clearing it would hand this duty
      // back to whatever its sibling last left on the panel.
      try {
        if (runMatches) {
          clearDutyOp(dutyKey(tDie, targetConfig, tDuty));
          if (cfgName !== targetConfig) clearDutyOp(dutyKey(tDie, cfgName, tDuty));
        }
        // An auto-rename (M1-L200 → M1-L220) moves the duty to a new
        // configuration, and its per-duty memory has to move with it — the
        // point on the panel IS that memory, so re-filing it under the new
        // name is the whole migration.
        if (cfgName !== targetConfig) {
          setActiveDuty(tDie, cfgName, tDuty);
          if (!runMatches) rememberDutyOp(dutyKey(tDie, cfgName, tDuty));
          clearDutyOp(dutyKey(tDie, targetConfig, tDuty));
        }
        // The DUTY CYCLE overlay, on the same rule as the point's but WITHOUT
        // the matching-run condition: the block was sent on this save whatever
        // the run said, so the yaml now states it and the un-saved edit has
        // nothing left to add.  It becomes this duty's snapshot layer in the
        // same breath, so the editor keeps showing what was just saved.
        if (dcSent) {
          clearDutyCycle(dutyKey(tDie, targetConfig, tDuty),
                         cfgName === targetConfig ? dcSent : null);
          if (cfgName !== targetConfig) {
            clearDutyCycle(dutyKey(tDie, cfgName, tDuty), dcSent);
          }
        }
      } catch { /* memory is a convenience, never a blocker */ }
      let extra = rj && rj.renamed_to ? ` · renamed to ${rj.renamed_to}` : '';
      // KV as DISPLAYED (user: with the pressed buttons): the summary card
      // publishes its view copy (sim.viewSummary) with the KV button already
      // applied and view_flags saying which convention is on screen.
      const vw = ((): any => {
        try {
          const v = JSON.parse(localStorage.getItem('sim.viewSummary') || 'null');
          return (v && s && v.rpm === s.rpm && v.I_phase_rms_A === s.I_phase_rms_A
                  && v.gamma_deg === s.gamma_deg) ? v : null;
        } catch { return null; }
      })();
      if (runMatches) {
        const rr = await fetch(`${API}/api/family/duty_result`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ die: tDie, config: cfgName, duty: tDuty,
            drive, assignment_sig: dutyBody.assignment_sig,
            result: { efficiency_pct: dutyEfficiencyPct(s, PmW),
                      ripple_pct: s.T_ripple_pct,
                      v_ll_peak_v: Number(s.V_line_peak_V) * (k3d ?? 1),
                      loss_w: s.P_loss_total_W, mass_kg: s.mass_total_kg,
                      // …and the mechanical watts that η above already carries,
                      // so the catalog's loss column can be read either way.
                      ...(Number.isFinite(Number(s.P_bearings_W))
                          || Number.isFinite(Number(s.P_windage_W))
                          ? { loss_mech_w: Number(s.P_bearings_W ?? 0)
                                         + Number(s.P_windage_W ?? 0) } : {}),
                      p_core_w: s.P_core_W, p_stranded_w: s.P_stranded_W,
                      p_solid_w: s.P_solid_W,
                      v_phase_peak_v: Number(s.V_phase_peak_V) * (k3d ?? 1),
                      j_coil_a_mm2: s.J_coil_A_per_mm2,
                      ...(k3d ? { end3d_k: k3d } : {}),
                      ...(vw && Number.isFinite(Number(vw.KV_rpm_per_V_line))
                        ? { kv_rpm_per_v: Number(vw.KV_rpm_per_V_line),
                            kv_is_noload: vw.view_flags?.kv === 'no-load' ? 1 : 0 }
                        : {}) } }),
        });
        extra += rr.ok
          ? (k3d ? ` + run results (3D ×${k3d.toFixed(3)})` : ' + run results')
          : ' (result recording failed)';
        // ── the WAVEFORMS ────────────────────────────────────────────────────
        // Stored so this run can be LOADED again instead of re-solved — twelve
        // minutes for a PWM point.  The per-element arrays no chart reads
        // (animation frames, the field, the demagnetisation map) are dropped
        // here rather than shipped and dropped server-side: a 700 MB request is
        // one nobody wants to see attempted.
        if (tOfRun) {
          try {
            const HEAVY = ['frames', 'field', 'demag_field', 'demag_coef_per_tri'];
            const payload: Record<string, unknown> = {};
            for (const [k, v] of Object.entries(tOfRun)) {
              if (!HEAVY.includes(k)) payload[k] = v;
            }
            const pr = await fetch(`${API}/api/family/duty_run`, {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ die: tDie, config: cfgName, duty: tDuty,
                drive, settings: dutyBody.mesh, summary: s,
                assignment_sig: dutyBody.assignment_sig, payload }),
            });
            if (!pr.ok) {
              let why = `HTTP ${pr.status}`;
              try { why = (await pr.json()).detail ?? why; } catch { /* no body */ }
              extra += ` (waveforms NOT stored: ${why})`;
            }
          } catch (pe: any) {
            extra += ` (waveforms NOT stored: ${pe?.message ?? pe})`;
          }
        } else {
          // SAY IT.  A save that files the numbers and drops the charts used to
          // look exactly like a complete one, and the report's torque / phase
          // current / line voltage figures were simply missing hours later
          // (user 2026-09-13).  The backend drops the duty's sidecar pointer in
          // this case rather than keep a foreign run's waveforms — correct, and
          // worth one line on screen.
          extra += ' (waveforms NOT stored — this run\'s transient is neither in '
                 + 'this browser nor the backend\'s last run; press Run, then save again)';
        }
      } else {
        extra += ' (no matching run to record — Run, then save again)';
      }
      // WHERE the save went.  A PWM save that correctly leaves the sine numbers
      // standing is indistinguishable from one that did nothing unless it says
      // so — and "why didn't my result change?" is the question this answers.
      // `rj.primary` is the backend's own verdict — a duty with no result yet
      // ADOPTS the first run it is given, whatever ran it, and saying "sine
      // result kept" there would name a result that does not exist.
      const where = (runMatches && drive !== 'current')
        ? (rj?.primary
            ? ` (${driveLabel(drive)} run stored — now this duty's primary)`
            : ` (${driveLabel(drive)} run stored; primary sine result kept)`)
        : '';
      setMsg(`✓ saved to ${tDie} / ${cfgName} / ${tDuty}${extra}${where}`);
      setBuildClash(false);
      window.dispatchEvent(new CustomEvent('family-changed'));
    } catch (e: any) {
      const m = String(e?.message ?? e);
      setMsg(`✗ ${m}`);
      // A BUILD clash is not a dead end — it means "this is a different
      // product".  Offer the one legal way forward right here (user
      // 2026-08-25, third time hitting the wall: "эта проблема уже была,
      // нужно решить её"): a new configuration under the same die, built
      // from the machine on screen.
      setBuildClash(m.includes('cannot carry its own geometry')
                    || m.includes('build differs from configuration'));
    }
    setBusy(false);
  };

  /** Save the ON-SCREEN build as a NEW configuration of the same die, then
   *  put this duty into it — the escape hatch from a build clash. */
  const saveAsNewConfig = async (name: string, target?: { die: string; duty: string }) => {
    const die = String(target?.die || ctx?.die || '');
    const duty = String(target?.duty || ctx?.duty || '');
    if (!die || !duty) return;
    setBusy(true); setMsg(null);
    try {
      // A fresh configuration has NO stored build, so the duty save below
      // DEFINES it from the live machine (family.py's `_defined_build` path).
      const r = await fetch(`${API}/api/family/config`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ die, name }),
      });
      if (!r.ok) throw new Error((await r.json()).detail ?? `HTTP ${r.status}`);
      const rj = await r.json().catch(() => ({} as any));
      // The API returns the canonical name in `config` (for example an entered
      // L12 becomes L15 when the live stack is 15 mm).  Keep `name` only for
      // compatibility with older servers.
      const created = String(rj?.config || rj?.name || name);
      // On a RELEASED context this is also the re-attach: the die becomes
      // active again, on the new configuration, with the live geometry
      // untouched (activate loads nothing — ▶ does).
      const activated = await fetch(`${API}/api/family/activate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ die, config: created, duty }),
      });
      if (!activated.ok) {
        throw new Error((await activated.json()).detail ?? `HTTP ${activated.status}`);
      }
      setBuildClash(false);
      // The duty moved to a new configuration: its per-duty operating-point
      // memory has to follow, or the panel keeps filing edits under the
      // configuration the duty no longer lives in.
      try { setActiveDuty(die, created, duty); }
      catch { /* memory is a convenience, never a blocker */ }
      window.dispatchEvent(new CustomEvent('family-changed'));
      setMsg(`✓ configuration '${created}' created — saving the duty…`);
      // Save with the server's canonical names.  Updating React state alone is
      // insufficient here because this function still closes over the old ctx.
      setCtx((c) => (c ? { ...c, active: true, die, config: created, duty } : c));
      await save({ die, config: created, duty });
    } catch (e: any) { setMsg(`✗ ${e?.message ?? e}`); }
    setBusy(false);
  };

  /** RELEASED context, the machine on screen is a NEW lamination: keep it as
   *  a new die (backend: die from the live geometry + one configuration from
   *  the live build + one duty at the live point, context made active), then
   *  record the last run's results into that duty exactly as "Save to duty"
   *  does.  The released die is untouched. */
  const saveAsNewDie = async (name: string) => {
    setBusy(true); setMsg(null);
    try {
      const r = await fetch(`${API}/api/family/save_as_new_die`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      if (!r.ok) throw new Error((await r.json()).detail ?? `HTTP ${r.status}`);
      const rj = await r.json();
      const die = String(rj.die), config = String(rj.config), duty = String(rj.duty);
      try { setActiveDuty(die, config, duty); } catch { /* convenience */ }
      window.dispatchEvent(new CustomEvent('family-changed'));
      setMsg(`✓ new die '${die}' saved (${config} / ${duty}) — recording the last run…`);
      setCtx((c) => ({ ...(c ?? { active: true }), active: true, die, config, duty,
                       can_write: true, die_locked: false, config_locked: false }));
      await save({ die, config, duty });
    } catch (e: any) { setMsg(`✗ ${e?.message ?? e}`); }
    setBusy(false);
  };

  /** RELEASED context, the live machine IS the die's lamination again: make
   *  the released triple active WITHOUT loading anything (backend
   *  POST /reattach — no geometry PUT, no point overwrite), so "Save to
   *  <duty>" works on the optimised geometry on screen. */
  const reattachReleased = async (die: string, config: string, duty: string | null) => {
    setBusy(true); setMsg(null);
    try {
      const r = await fetch(`${API}/api/family/reattach`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ die, config, duty }),
      });
      if (!r.ok) throw new Error((await r.json()).detail ?? `HTTP ${r.status}`);
      const rj = await r.json();
      if (duty) { try { setActiveDuty(die, config, duty); } catch { /* convenience */ } }
      setCtx((c) => ({ ...(c ?? { active: true }), active: true, die, config, duty,
                       can_write: true, die_locked: rj?.die_locked === true,
                       config_locked: rj?.config_locked === true }));
      setMsg(`✓ re-attached to ${die} / ${config}${duty ? ` / ${duty}` : ''} — machine on screen kept`
        + (rj?.die_synced ? ' (die snapshot updated)' : ''));
      window.dispatchEvent(new CustomEvent('family-changed'));
    } catch (e: any) { setMsg(`✗ re-attach: ${e?.message ?? e}`); }
    setBusy(false);
  };

  /** RELEASED context: throw the live changes away and load the released
   *  duty again — the very ▶ the Motors catalog runs (lib/dutyApply). */
  const reloadReleased = async (die: string, config: string, duty: string) => {
    setBusy(true); setMsg(null);
    try {
      const done = await applyDutyEverywhere(die, config, duty, true);
      setMsg(`✓ reloaded ${die} / ${config} / ${duty} — ${done.message}`);
      window.dispatchEvent(new CustomEvent('family-changed'));
    } catch (e: any) { setMsg(`✗ reload: ${e?.message ?? e}`); }
    setBusy(false);
  };

  if (!ctx?.active) {
    // RELEASED context (the identity guard, a Compare / My-motors / preset
    // load): never a dead end (2026-09-20).  One line saying WHAT changed,
    // and the ways to keep the work — lib/releasedContext decides which.
    const offers = releasedOffers(ctx as ReleasedCtxLike | null);
    if (!offers) return null;
    const btnSx = { textTransform: 'none', fontSize: 11, py: 0, minHeight: 22 } as const;
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 1.5, py: 0.25,
                 fontSize: 11, color: '#f59e0b', borderBottom: '1px solid var(--line)',
                 flexWrap: 'wrap' }}>
        <Tooltip title={offers.tip}>
          <span style={{ cursor: 'help' }}>{offers.line}</span>
        </Tooltip>
        <Box sx={{ flex: 1 }} />
        {msg && (
          <Typography sx={{ fontSize: 11,
            color: msg.startsWith('✗') ? '#fca5a5' : '#34d399' }}>{msg}</Typography>
        )}
        {offers.saveNewDie && (
          <Button size="small" variant="contained" disabled={busy}
            onClick={() => setAskDie({
              title: 'Save the machine on screen as a NEW die',
              label: 'Die name',
              initial: offers.saveNewDie!.initialName,
              hint: offers.saveNewDie!.hint,
              onSubmit: (n) => { void saveAsNewDie(n.trim()); },
            })}
            sx={{ ...btnSx, bgcolor: '#1d4ed8', '&:hover': { bgcolor: '#2563eb' } }}>
            {busy ? <CircularProgress size={11} /> : offers.saveNewDie.label}
          </Button>
        )}
        {offers.reattach && (
          <Button size="small" variant="contained" disabled={busy}
            onClick={() => void reattachReleased(offers.reattach!.die, offers.reattach!.config,
                                                 offers.reattach!.duty)}
            sx={{ ...btnSx, bgcolor: '#047857', '&:hover': { bgcolor: '#059669' } }}>
            {offers.reattach.label}
          </Button>
        )}
        {offers.saveNewConfig && (
          <Button size="small" variant="outlined" disabled={busy}
            onClick={() => setAskCfg({
              title: `Save the machine on screen as a NEW configuration of ${offers.saveNewConfig!.die}`,
              label: 'Configuration name',
              initial: (() => {
                try {
                  const L = Number((useMotorStore.getState().geometry as any)?.motor_length);
                  return Number.isFinite(L) ? `L${Math.round(L)}` : 'L-new';
                } catch { return 'L-new'; }
              })(),
              hint: `The current build (stack, wire, turns, winding, materials) defines it; duty '${offers.saveNewConfig!.duty}' is saved into it and the die becomes active again`,
              onSubmit: (n) => { void saveAsNewConfig(n.trim(), offers.saveNewConfig!); },
            })}
            sx={btnSx}>
            {offers.saveNewConfig.label}
          </Button>
        )}
        {offers.reload && (
          <Button size="small" variant="outlined" color="warning" disabled={busy}
            onClick={() => setAskReload({
              title: 'Discard the live changes?',
              body: `The geometry on screen is replaced by ${offers.reload!.die} / ${offers.reload!.config} / ${offers.reload!.duty} as saved in the catalog. Nothing of the current machine is kept — save it as a new die first if you want it.`,
              confirmLabel: 'Discard & reload',
              onConfirm: () => { void reloadReleased(offers.reload!.die, offers.reload!.config, offers.reload!.duty); },
            })}
            sx={btnSx}>
            {offers.reload.label}
          </Button>
        )}
        <TextPromptDialog state={askDie} onClose={() => setAskDie(null)} />
        <TextPromptDialog state={askCfg} onClose={() => setAskCfg(null)} />
        <ConfirmDialog state={askReload} onClose={() => setAskReload(null)} />
      </Box>
    );
  }

  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 1.5, py: 0.35,
               bgcolor: 'var(--panel-2)', borderBottom: '1px solid var(--line-soft)',
               flexShrink: 0, minHeight: 28 }}>
      <Tooltip title={lockTip}>
        <Typography component="span" sx={{ fontSize: 12, cursor: 'help' }}>
          {lockGlyph}
        </Typography>
      </Tooltip>
      {(() => {
        // EFFECTIVE config name from the LIVE geometry: change the stack in
        // Geometry and the strip reads M1-L220 (amber = unsaved) at once —
        // the catalog keeps the saved name until Save (user rule).
        const liveL = Number(liveGeometry?.motor_length);
        const savedL = Number(ctx.build?.stack_mm);
        const liveTurns = Number(liveGeometry?.num_wires_per_slot);
        const savedTurns = Number(ctx.build?.turns);
        const liveWh = Number(liveGeometry?.wire_height);
        const savedWh = Number(ctx.build?.wire_height_mm);
        const lDrift = Number.isFinite(liveL) && Number.isFinite(savedL)
          && Math.abs(liveL - savedL) > 1e-6;
        const otherDrift = (Number.isFinite(liveTurns) && Number.isFinite(savedTurns)
            && Math.abs(liveTurns - savedTurns) > 1e-6)
          || (Number.isFinite(liveWh) && Number.isFinite(savedWh)
            && Math.abs(liveWh - savedWh) > 1e-6);
        let cfgLabel = ctx.config ?? '';
        if (lDrift) {
          const lTxt = `L${Number(liveL.toFixed(1))}`;
          cfgLabel = /-L\d+(\.\d+)?/.test(cfgLabel)
            ? cfgLabel.replace(/-L\d+(\.\d+)?/, `-${lTxt}`)
            : `${cfgLabel} (${lTxt})`;
        }
        const drifted2 = lDrift || otherDrift;
        const tip = drifted2
          ? `Live build differs from the saved configuration:`
            + (lDrift ? ` stack ${savedL} → ${liveL} mm;` : '')
            + (Number.isFinite(liveTurns) && Number.isFinite(savedTurns) && liveTurns !== savedTurns
               ? ` turns ${savedTurns} → ${liveTurns};` : '')
            + (Number.isFinite(liveWh) && Number.isFinite(savedWh) && Math.abs(liveWh - savedWh) > 1e-6
               ? ` wire h ${savedWh} → ${liveWh} mm;` : '')
            + ' Save to duty re-snapshots the build (siblings will flag stale).'
          : '';
        const cfgEl = (
          <b style={{ color: drifted2 ? '#fbbf24' : 'var(--text-1)' }}>{cfgLabel}</b>
        );
        // The DIE name never tracks the stack — it names the stamped
        // lamination and does not depend on the lamination LENGTH (user
        // rule); only the configuration part follows the live build.
        return (
          <Typography sx={{ fontSize: 12, color: 'var(--text-2)',
                            fontVariantNumeric: 'tabular-nums' }}>
            <b style={{ color: 'var(--text-1)' }}>{ctx.die}</b>
            {' / '}{drifted2
              ? <Tooltip title={tip}><span style={{ cursor: 'help' }}>{cfgEl}</span></Tooltip>
              : cfgEl}
            {ctx.duty ? <>{' / '}<b style={{ color: '#60a5fa' }}>{ctx.duty}</b></> : null}
          </Typography>
        );
      })()}
      {/* This browser's panel was CAUGHT UP with a duty loaded somewhere else.
          One short line + tooltip (the UI rule) — the answer to "why did my
          current change while I was on another tab?". */}
      {adopted && adopted.config === ctx.config && adopted.duty === ctx.duty && (
        <Tooltip title={`The active machine was loaded in another window at `
          + `${hhmm(adopted.at)}, so this panel adopted that duty's operating `
          + `point, its saved panel settings and its materials — the point it `
          + `held belonged to the machine that was replaced. Nothing was solved `
          + `and nothing was written to the server; the point you were on is `
          + `filed under the duty you left and comes back with it.`}>
          <Typography component="span" sx={{ fontSize: 11, color: '#38bdf8',
                                             cursor: 'help' }}>
            ⟳ point taken from {adopted.config} / {adopted.duty}, loaded {hhmm(adopted.at)}
          </Typography>
        </Tooltip>
      )}
      {p && (
        <Tooltip title={drifted
          ? `The panel is OFF this duty's stored point (${p.current_arms} A @ ${p.rpm} rpm, γ=${p.gamma_deg}°, ${p.mode}) — Save to duty writes the panel's point into it`
          : 'The Simulation panel sits exactly on this duty’s stored point'}>
          <Typography component="span" sx={{ fontSize: 11, cursor: 'help',
            color: drifted ? '#fbbf24' : '#34d399' }}>
            {drifted ? '≠ point changed' : '= on point'}
          </Typography>
        </Tooltip>
      )}
      <Box sx={{ flex: 1 }} />
      {msg && (
        <Typography sx={{ fontSize: 11,
          color: msg.startsWith('✗') ? '#fca5a5' : '#34d399' }}>{msg}</Typography>
      )}
      {/* Build clash → the way out, right where the refusal is (user
          2026-08-25): one click creates a new configuration of the SAME die
          from the machine on screen and saves this duty into it. */}
      {buildClash && ctx.can_write && ctx.duty && (
        <Button size="small" variant="contained" disabled={busy}
          onClick={() => setAskCfg({
            title: 'Save the machine on screen as a NEW configuration',
            label: 'Configuration name',
            initial: (() => {
              try {
                const L = Number((useMotorStore.getState().geometry as any)?.motor_length);
                return Number.isFinite(L) ? `L${Math.round(L)}` : 'L-new';
              } catch { return 'L-new'; }
            })(),
            hint: 'The current build (stack, wire, turns, winding, materials) defines it; the duty is saved into it',
            onSubmit: (n) => { void saveAsNewConfig(n.trim()); },
          })}
          sx={{ textTransform: 'none', fontSize: 11, py: 0, minHeight: 22,
                bgcolor: '#1d4ed8', '&:hover': { bgcolor: '#2563eb' } }}>
          ＋ Save as new configuration
        </Button>
      )}
      {ctx.can_write && ctx.duty && (
        <Button size="small" variant="outlined" disabled={busy} onClick={() => void save()}
          sx={{ textTransform: 'none', fontSize: 11, py: 0, minHeight: 22 }}>
          {busy ? <CircularProgress size={11} /> : `💾 Save to ${ctx.duty}`}
        </Button>
      )}
      <TextPromptDialog state={askCfg} onClose={() => setAskCfg(null)} />
    </Box>
  );
};

export default ActiveFamilyStrip;
