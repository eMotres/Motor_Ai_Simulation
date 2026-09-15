// The LOCAL half of ▶ — everything loading a duty does INSIDE one browser.
//
// FamilyCatalog.applyDuty does two different jobs.  One is a SERVER job and
// only the browser that pressed ▶ may do it: activate the context, PUT the
// geometry, PATCH the winding, the materials and the shared simulation config.
// The other is a BROWSER job — the panel's own persisted operating point, the
// duty's settings block, its materials, its stored runs and the events that
// make the mounted panels re-read.  Every browser has to do that one for
// itself, because localStorage is per browser (incident 2026-09-08 23:23, see
// lib/familyFollow).
//
// So the browser job lives HERE, in one function, called by both:
//   • FamilyCatalog.applyDuty  — after it has done the server writes;
//   • the follower in ActiveFamilyStrip — with no server writes at all, because
//     the browser that pressed ▶ already made them.
// One implementation, so a fix to the load order can never reach one path and
// not the other.
import { applyDutyCycleBlock, applyDutySettingsBlock } from './dutySnapshot';
import {
  rememberDieSettings, restoreDieMeshSettings, restoreDieSettings,
} from './dieSettings';
import {
  activeDuty, clearDutyMaterialsKeys, dutyKey, rememberDutyOp, restoreDutyOp,
  setActiveDuty, ASSIGN_KEY,
} from './dutySettings';
import {
  applyStoredRun, clearDutyRuns, driveLabel, fetchDutyRuns, setDutyRuns,
  setPickedRun, type StoredRun,
} from './dutyRuns';
import {
  beginDutyApply, endDutyApply, rememberAppliedContext,
} from './familyFollow';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

/** "the machine on the panel is not the machine it was" — fired by the apply
 *  below when the die or the configuration changed, so tabs holding results of
 *  the PREVIOUS machine can drop them (SimulationPanel re-checks the coupled
 *  loop's last answer against it). */
export const MACHINE_CHANGED_EVENT = 'family-machine-changed';

/** SimulationPanel's own default — usePersisted('coilTemp', 120). */
export const PANEL_COIL_TEMP_C = 120;

/** The duty entry as the payload route returns it (a verbatim copy of the yaml
 *  entry, minus `runs`).  Loose on purpose: this module reads six fields of it
 *  and the yaml owns the rest. */
export interface DutyEntry {
  mesh?: unknown;
  materials?: unknown;
  /** what the machine DOES with this point — S1 / S2 / S3 / segments
   *  (2026-09-14).  Absent on every duty saved before the cycle existed. */
  duty_cycle?: unknown;
  summary?: Record<string, unknown> | null;
  result?: Record<string, unknown> | null;
  torque_nm?: number | null;
  power_kw?: number | null;
}

export interface DutySimBlock {
  current_a?: number | null;
  rpm?: number | null;
  gamma_deg?: number | null;
  frequency?: number | null;
  mode?: string | null;
  connection?: string | null;
  star_delta?: string | null;
  daxis_deg?: number | null;
}

export interface DutyPayload {
  sim: DutySimBlock;
  duty?: DutyEntry | null;
  // The three blocks only the OWNER path writes to the server.  Loosely typed
  // on purpose: the store owns MotorGeometryParams and the winding/materials
  // shapes belong to their own endpoints — this module never reads them.
  /* eslint-disable @typescript-eslint/no-explicit-any */
  geometry?: any;
  winding?: any;
  materials?: any;
  /* eslint-enable @typescript-eslint/no-explicit-any */
}

/** What the panel held BEFORE this apply — the answer to "did the machine
 *  change?".  `die: null` = this browser had no duty loaded at all. */
export interface OutgoingRef {
  die: string | null;
  config: string | null;
  duty: string | null;
}

/** PURE.  Is the duty being loaded on a DIFFERENT machine than the one the
 *  panel was on?  Unknown previous machine ⇒ false: the numbers on the panel
 *  are then the user's own work (a Compare design, a fresh profile), not
 *  another duty's, and resetting them would be the clobber this file exists to
 *  prevent, pointed the other way. */
export function machineChanged(prev: OutgoingRef | null | undefined,
                               die: string, cfg: string): boolean {
  if (!prev || !prev.die) return false;
  if (prev.die !== die) return true;
  return prev.config != null && prev.config !== cfg;
}

/** PURE.  The two COUPLED-LOOP temperature fields for a machine that just
 *  changed.
 *
 *  With the "Coupled thermal" switch ON these two are OUTPUTS of the last loop,
 *  not inputs — read-only on the panel — so carrying them into a new machine's
 *  first pass states another motor's answer as this one's starting point (the
 *  Ø200's 111 °C / 137 °C went into a 40 mm generator, 2026-09-08).  The duty's
 *  own saved summary is the best statement of ITS winding temperature; failing
 *  that the panel default.  The magnet field is always CLEARED — empty means
 *  "the card exactly as the library quotes it", which is the only honest
 *  starting point for a magnet nobody has solved for yet.
 *
 *  Both are written BEFORE the duty's settings block and its local overlay, so
 *  anything either of those has to say about this duty still wins. */
export function coupledTempsForNewMachine(duty: DutyEntry | null | undefined):
    { coilTemp: number; magnetTempC: string } {
  const s = duty?.summary ?? null;
  const raw = s ? (s.coil_temp_C ?? s.coil_temp_c) : undefined;
  const c = Number(raw);
  return {
    coilTemp: raw != null && Number.isFinite(c) ? Math.round(c * 10) / 10
                                                : PANEL_COIL_TEMP_C,
    magnetTempC: '',
  };
}

/** Step 0 of every duty load: file the panel state under what it was
 *  demonstrably in use on, and say what that was.
 *
 *  Leaving another DIE: the mesh/sim block on the panel belongs to that die, so
 *  coming back restores the user's own state instead of whatever machine was
 *  visited in between (user 2026-08-31: "всегда было 1/4 и max 2 mm").
 *  Leaving another DUTY: same argument one level down — the panel writes each
 *  field through to its duty's overlay as it is typed, so this only catches
 *  points written by something else (a Compare apply, a descent restore), but
 *  it is what makes A → B → A exact. */
export function leaveForDuty(die: string, cfg: string, duty: string): OutgoingRef {
  let lastDie: string | null = null;
  try {
    lastDie = localStorage.getItem('family.lastDie');
    if (lastDie && lastDie !== die) rememberDieSettings(lastDie);
  } catch { /* memory is a convenience, never a blocker */ }
  let a: { die: string; config: string; duty: string } | null = null;
  try {
    a = activeDuty();
    const prevKey = dutyKey(a?.die, a?.config, a?.duty);
    const nextKey = dutyKey(die, cfg, duty);
    if (prevKey && prevKey !== nextKey) rememberDutyOp(prevKey);
  } catch { /* memory is a convenience, never a blocker */ }
  return { die: a?.die ?? lastDie ?? null,
           config: a?.config ?? null,
           duty: a?.duty ?? null };
}

/** The apply payload — geometry, winding, materials and the duty's point. */
export async function fetchDutyPayload(die: string, cfg: string,
                                       duty: string): Promise<DutyPayload> {
  const r = await fetch(`${API}/api/family/payload/${encodeURIComponent(die)}/`
    + `${encodeURIComponent(cfg)}?duty=${encodeURIComponent(duty)}`);
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try { detail = (await r.json()).detail ?? detail; } catch { /* no body */ }
    throw new Error(detail);
  }
  return (await r.json()) as DutyPayload;
}

export interface LocalApplyResult {
  /** the tail of the catalog's "✓ applied X — …" line */
  message: string;
  /** the die or the configuration changed with this load */
  machineChanged: boolean;
}

/**
 * Put the duty on THIS browser's panel.  Writes localStorage and fires the
 * events the mounted panels listen to; touches no server state whatsoever.
 *
 * `prev` is what `leaveForDuty` returned at the start of the same load.
 */
/** The five mesh fidelity keys the server `mesh:` block mirrors — the same
 *  pairs `lib/meshConfigSync` adopts at boot. */
const MESH_SERVER_KEYS: ReadonlyArray<readonly [local: string, server: string]> = [
  ['meshSize', 'mesh_size_mm'], ['minSize', 'min_size_mm'],
  ['outerAir', 'outer_air_factor'], ['gapLayers', 'gap_layers'],
  ['nSectors', 'n_sectors'],
];

/**
 * Write the mesh block this browser just settled on (the duty's snapshot, then
 * the die's own memory) to the server, so the NEXT boot of any browser adopts
 * the loaded machine's mesh and not the previous machine's.
 *
 * 2026-09-09: the server block is adopted once per boot (meshConfigSync) but
 * was written only by the Mesh tab while mounted — a ▶ on the Electromagnetic
 * tab left "4 sectors / 2.97 mm" from the G2 on the server while the 40 mm was
 * live, and F5 then put the wrong symmetry back on the panel.  OWNER ONLY: an
 * ordinary user's ▶ is a client-side copy and must not touch the shared block.
 */
async function persistMeshBlock(): Promise<void> {
  const body: Record<string, number> = {};
  for (const [lk, sk] of MESH_SERVER_KEYS) {
    try {
      const raw = localStorage.getItem(`mesh.${lk}`);
      if (raw == null) continue;
      const v = Number(JSON.parse(raw));
      if (Number.isFinite(v)) body[sk] = sk === 'n_sectors' ? Math.round(v) : v;
    } catch { /* an unreadable key is not sent */ }
  }
  if (!Object.keys(body).length) return;
  try {
    await fetch(`${API}/api/mesh/config`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch { /* the server block is a convenience for the next boot; the panel is right already */ }
}

export async function applyDutyLocal(die: string, cfg: string, duty: string,
                                     p: DutyPayload,
                                     prev: OutgoingRef | null,
                                     persistMesh = false): Promise<LocalApplyResult> {
  // From this line on, the panel is editing THIS duty: every operating-point
  // field it writes is filed under this key (and never under the duty we just
  // left, which is why the marker moves BEFORE the first sim.* write rather
  // than after the last one).  The catalog claims it earlier still, before its
  // server writes; both calls are idempotent.
  const opKey = dutyKey(die, cfg, duty);
  try { setActiveDuty(die, cfg, duty); } catch { /* quota */ }
  // The MATERIALS are per-duty too, and their default is "the machine's own" —
  // an ABSENT key, not a value.  Cleared here, before this duty's snapshot and
  // its overlay get to state their own: without the clear, a duty that never
  // picked any would keep solving with the magnet and the steel the PREVIOUS
  // duty chose.  lib/dutySettings.ts.
  try { clearDutyMaterialsKeys(); } catch { /* nothing to clear */ }
  // The mark the follower compares against, written FIRST: a poll landing
  // mid-apply must see this browser as already up to date, and an apply that
  // fails half way must not look like a browser that never followed at all
  // (the next ▶ or the next context change moves it again).
  try { rememberAppliedContext(die, cfg, duty); } catch { /* quota */ }

  const sim: DutySimBlock = p.sim ?? {};
  const changed = machineChanged(prev, die, cfg);
  // panel-owned persisted values + live nudge for the mounted panel
  const set = (k: string, v: unknown) => {
    if (v === undefined) return;   // a payload that says nothing writes nothing
    try { localStorage.setItem('sim.' + k, JSON.stringify(v)); } catch { /* quota */ }
  };
  // The coupled loop's two OUTPUT fields, before anything that may overrule
  // them for this duty (its settings block, then its local overlay).
  if (changed) {
    const t = coupledTempsForNewMachine(p.duty);
    set('coilTemp', t.coilTemp);
    set('magnetTempC', t.magnetTempC);
  }
  set('current', sim.current_a); set('gamma', sim.gamma_deg);
  set('rpm', sim.rpm); set('frequency', sim.frequency);
  set('opMode', sim.mode);
  if (sim.connection) set('connection', sim.connection);
  // Terminal connection — a winding property like the label above, so it is
  // restored with the machine and never inherited from the previous one.
  // The DUTY's own saved setting wins over the configuration's default: a
  // configuration written before `star_delta` existed says nothing, and
  // "nothing" used to become star — the Ø200 peak point ran in star twice
  // on 2026-09-13 with the Δ toggle silently flipped by this line.
  const _savedSD = (p.duty as { star_delta?: string } | undefined)?.star_delta
    ?? (p.duty?.mesh as Record<string, unknown> | undefined)?.['sim.starDelta'];
  const _sd = String(_savedSD ?? sim.star_delta ?? 'star');
  set('starDelta', _sd.toLowerCase().startsWith('d') ? 'delta' : 'star');
  // The d-axis pin is a property of the DIE's topology: a die without one must
  // CLEAR the field (empty = measure) — leaving the previous machine's pin in
  // place put a 24s/28p 60° onto a 12s/10p machine (its own d-axis is 120°) and
  // every γ sat 60° off the q-axis (user 2026-09-03).
  set('daxisDeg', sim.daxis_deg != null ? String(sim.daxis_deg) : '');
  window.dispatchEvent(new CustomEvent('sim-operating-point', {
    detail: { current: sim.current_a, gamma: sim.gamma_deg, rpm: sim.rpm,
              mode: sim.mode, connection: sim.connection,
              star_delta: sim.star_delta ?? 'star' } }));
  window.dispatchEvent(new CustomEvent('sim-design-applied'));
  window.dispatchEvent(new CustomEvent('family-changed'));
  const dd: DutyEntry = p.duty || {};
  // ── Restore EVERY saved panel setting (user: "сохранять всё что можно"):
  //    the duty carries the full mesh.*/sim.* state it was solved with — write
  //    it back verbatim, then tell the mounted panels to re-read their fields.
  //    The op-point set() calls above stay AFTER-authoritative (same values on
  //    a consistent save).
  if (dd.mesh && typeof dd.mesh === 'object') {
    // One implementation with the panel's self-heal (lib/dutySnapshot): never
    // the run trigger / result caches / the machine's DC link, retired per-part
    // mesh keys dropped.
    applyDutySettingsBlock(dd.mesh);
    // …but the MESH fidelity keys belong to the MACHINE, not to the duty: a
    // duty snapshot can carry another die's mesh (the panel travelled — the
    // 40 mm's 1.0 mm / ½ landed inside a 200 mm duty and reloaded itself
    // forever).  The die's own remembered mesh wins over it.
    try { restoreDieMeshSettings(die); } catch { /* keep duty's */ }
  } else {
    // A duty saved before settings capture existed: the die's remembered block
    // is the best statement of how this machine is normally run.
    try {
      restoreDieSettings(die);
      // …except the die block also carries a full OPERATING POINT, and that
      // point belongs to whichever duty of this machine was on the panel when
      // the block was taken — not to the one being loaded.  Writing it back is
      // how "S2 peak" used to reappear under "S1 cont".  The duty's own stored
      // point goes back on top; the fields the duty has no record of (coil
      // temp, drive) keep the die's value, which is at least this machine's
      // habit rather than the previous machine's — so a field the payload has
      // nothing to say about (null) is LEFT as the die restored it.
      const put = (k: string, v: unknown) => { if (v != null) set(k, v); };
      put('current', sim.current_a); put('gamma', sim.gamma_deg);
      put('rpm', sim.rpm); put('frequency', sim.frequency);
      put('opMode', sim.mode);
    } catch { /* keep panel as-is */ }
  }
  // The mesh block is settled now (duty snapshot, then the die's memory):
  // the OWNER's browser writes it to the server for the next boot.
  if (persistMesh) await persistMeshBlock();
  // ── THIS duty's own MATERIALS, as the yaml has them ─────────────────────────
  // The saved duty carries a `materials:` dict of its own ({part: name}, null =
  // "the machine's"; routes/family.py DutySpec).  It is written AFTER the
  // settings block on purpose: an old duty from before the generalization keeps
  // its magnet-only pick inside that block under the legacy `mat.magnet` key,
  // and this newer, complete statement must outrank it.  Both are then
  // outranked by the local overlay below.
  if (dd.materials && typeof dd.materials === 'object') {
    try { localStorage.setItem(ASSIGN_KEY, JSON.stringify(dd.materials)); }
    catch { /* quota — the machine's materials it is */ }
  }
  // ── THIS duty's own DUTY CYCLE, as the yaml has it ──────────────────────────
  // (2026-09-14)  The SNAPSHOT layer, written before the overlay below for the
  // same reason the materials are: the catalog states what this duty's cycle
  // IS, and the user's un-saved edit in the cycle editor is newer and outranks
  // it.  A duty with no block writes none — a continuous point, which is what
  // every duty saved before the cycle existed was assumed to be.
  applyDutyCycleBlock(die, cfg, duty, dd.duty_cycle);
  // ── THIS duty's own operating point, last word ──────────────────────────────
  // Everything above states the point as the CATALOG has it: the payload's
  // I/rpm/γ/mode, then the snapshot the duty was solved with.  On top of that
  // goes the user's own un-saved work on THIS duty — the local overlay —
  // because it is newer than the snapshot and belongs to exactly this
  // die/config/duty.  A duty with no overlay lands on its snapshot, which is
  // why S1 stops inheriting S2's 200 °C.
  //
  // The overlay is LOCAL: nothing here writes to the stored duty.  It is
  // written per keystroke by the Simulation panel and cleared by an explicit
  // Save to duty (lib/dutySettings.ts).
  try { restoreDutyOp(opKey); } catch { /* keep the snapshot's point */ }
  // Memory is written on LEAVING a die and on SAVING a duty — the two moments
  // when the panel state was demonstrably in use on that die.  Writing it here
  // too would cement a polluted duty snapshot as the die's memory before the
  // user ever corrected it.
  try { localStorage.setItem('family.lastDie', die); } catch { /* quota */ }
  // ALWAYS tell the mounted panels to re-read their persisted fields — not only
  // when the duty carried saved settings.  Without this, the panel's own state
  // (from the PREVIOUS motor) wrote itself back over the just-loaded values,
  // and the first Run went out with the old winding connection: T×½, R×¼ on the
  // 40 mm (user 2026-08-25, "она уже не раз повторяется" — this was the
  // recurring first-run bug).
  window.dispatchEvent(new CustomEvent('sim-settings-restored'));
  // A DIFFERENT machine is on the panel now.  Said out loud, after the fields
  // have been re-read, so a tab still holding the previous machine's answer can
  // drop it (the Electromagnetic tab re-checks the coupled loop's last result
  // and adopts it only when the server says it is not stale).
  if (changed) {
    try {
      window.dispatchEvent(new CustomEvent(MACHINE_CHANGED_EVENT,
        { detail: { die, config: cfg, duty } }));
    } catch { /* SSR */ }
  }
  // ── EVERY stored run of this duty, in ONE call ──────────────────────────────
  // A duty may have been solved on several excitations (sine, PWM, BLDC); the
  // catalog row shows the sine numbers, and the Simulation panel's run selector
  // switches between them with no solve at all.  ▶ is the only load action, so
  // it brings them all — the fetch is best-effort: a duty whose runs cannot be
  // read must still load exactly as it did before.
  let stored: StoredRun[] = [];
  let primaryRun: StoredRun | null = null;
  try {
    const got = await fetchDutyRuns(die, cfg, duty);
    stored = got.runs;
    setDutyRuns(opKey, stored);
    primaryRun = stored.find(r => r.drive === got.primary_drive)
      ?? stored.find(r => r.primary) ?? null;
    // Loading a duty always shows its PRIMARY result — the sine run the catalog
    // row is about.  The selector's own memory takes over from the next click;
    // defaulting to "whatever was picked last time" would reopen a 12-minute
    // PWM view for a user who asked for the motor.
    if (primaryRun) setPickedRun(opKey, String(primaryRun.drive));
  } catch { stored = []; clearDutyRuns(); }
  // ── The COMPLETE saved summary (all constants, live 3D/R/KV buttons) goes on
  //    the dashboard as-is; the legacy mini-summary from the recorded result
  //    numbers remains the fallback for old duties.
  const rr = dd.result || {};
  const others = stored.filter(r => r.drive !== primaryRun?.drive).length;
  const alsoTxt = others
    ? ` · ${others} other run${others === 1 ? '' : 's'} ready in Simulation` : '';
  if (primaryRun?.payload) {
    // The primary run's WAVEFORMS, so the tab opens on the run this duty is
    // about instead of on whatever transient the previous machine left behind.
    // Its SETTINGS are deliberately not re-applied: the precedence chain above
    // (duty snapshot → die mesh memory → the user's local overlay) already
    // spoke, and this is the same block.  Its SUMMARY comes from the payload
    // route when that has one, because that route heals a snapshot whose end3d
    // froze before a Stage-A passport existed — the stored run is a verbatim
    // copy and cannot.
    applyStoredRun({
      ...primaryRun,
      summary: (dd.summary && typeof dd.summary === 'object')
        ? dd.summary : primaryRun.summary,
    }, { settings: false });
    return { machineChanged: changed,
             message: `${driveLabel(String(primaryRun.drive))} run restored `
               + `(charts + summary, nothing recomputed)${alsoTxt}` };
  }
  if (dd.summary && typeof dd.summary === 'object') {
    window.dispatchEvent(new CustomEvent('sim-apply-summary',
      { detail: { summary: dd.summary } }));
    return { machineChanged: changed,
             message: `saved run state restored; Run to recompute${alsoTxt}` };
  }
  if (rr.efficiency_pct != null || dd.torque_nm != null) {
    const rpm = Number(sim.rpm) || 0;
    const omega = 2 * Math.PI * rpm / 60;
    const T = Number(dd.torque_nm) || 0;
    const Pmech = dd.power_kw != null ? Number(dd.power_kw) * 1000 : T * omega;
    const mass = Number(rr.mass_kg) || 0;
    const Vlpk = Number(rr.v_ll_peak_v) || 0;
    const ploss = Number(rr.loss_w) || 0;
    const summary: Record<string, unknown> = {
      rpm, I_phase_rms_A: Number(sim.current_a) || 0,
      gamma_deg: Number(sim.gamma_deg) || 0,
      connection: sim.connection, op_mode: sim.mode,
      star_delta: sim.star_delta ?? 'star',
      T_em_avg_Nm: T, T_ripple_pct: Number(rr.ripple_pct) || 0,
      P_mech_W: Pmech,
      // v_ll_peak_v was RECORDED from a real run, so it IS the line peak.
      // KV = rpm / V_line_PEAK — the max/max convention of the Simulation tile
      // and the user's Ansys table (2026-08-04); dividing by rms read ~√2
      // (+41 %) high.  The /√2 rms values below are sinusoid approximations —
      // the duty record keeps peaks only.
      V_line_peak_V: Vlpk, V_line_rms_V: Vlpk / Math.SQRT2,
      KV_rpm_per_V_line: Vlpk > 0 ? rpm / Vlpk : 0,
      P_loss_total_W: ploss,
      P_core_W: rr.p_core_w ?? undefined,
      P_stranded_W: rr.p_stranded_w ?? undefined,
      P_solid_W: rr.p_solid_w ?? undefined,
      V_phase_peak_V: rr.v_phase_peak_v ?? undefined,
      V_phase_rms_V: rr.v_phase_peak_v != null
        ? Number(rr.v_phase_peak_v) / Math.SQRT2 : undefined,   // sinusoid approx
      J_coil_A_per_mm2: rr.j_coil_a_mm2 ?? undefined,
      efficiency: rr.efficiency_pct != null ? Number(rr.efficiency_pct) / 100 : 0,
      mass_total_kg: mass, mass_components: [],
      torque_per_mass_Nm_kg: mass > 0 && T > 0 ? T / mass : 0,
      power_per_mass_W_kg: mass > 0 ? Pmech / mass : 0,
      loss_density_W_kg: mass > 0 ? ploss / mass : 0,
    };
    window.dispatchEvent(new CustomEvent('sim-apply-summary', { detail: { summary } }));
    return { machineChanged: changed,
             message: 'saved results are on the dashboard; Run to recompute' };
  }
  return { machineChanged: changed,
           message: 'no saved results yet; press Run in Simulation' };
}

/**
 * FOLLOW a duty another browser loaded: the local half, and nothing else.
 *
 * No activate, no geometry PUT, no winding / materials / simulation PATCH — the
 * browser that pressed ▶ made every one of those, and repeating them from here
 * would be a second writer for one shared machine.  This browser only has to
 * catch up with its own panel.
 */
export async function followActiveDuty(die: string, cfg: string,
                                       duty: string): Promise<LocalApplyResult> {
  beginDutyApply();
  try {
    const prev = leaveForDuty(die, cfg, duty);
    const p = await fetchDutyPayload(die, cfg, duty);
    // The follower runs only for a writer (adoptionDecision requires
    // can_write), so the server mesh block is theirs to update.
    return await applyDutyLocal(die, cfg, duty, p, prev, true);
  } finally {
    endDutyApply();
  }
}
