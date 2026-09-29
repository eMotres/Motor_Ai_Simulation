/**
 * The ELECTROMAGNETIC RUN's request body — one builder, every caller.
 *
 * It lived inside `TransientCharts.run()` as a 130-line object literal, which
 * was fine while exactly one component ever pressed Run.  It stopped being fine
 * on 2026-09-08, when the Thermal tab was given the right to make the missing
 * Electromagnetic run for itself (through the orchestrator, `POST
 * /api/coupled/run` — never by solving anything of its own): two places building
 * "the same" payload is two places for the run the thermal solve MAKES to stop
 * being the run the thermal solve LOOKS FOR, and the failure mode is silent —
 * a second refusal with the same words, after six minutes of solving.
 *
 * So the body is built HERE and nowhere else.  The Electromagnetic panel passes
 * the fields it holds in React state; the Thermal tab reads the same fields from
 * where that panel persists them (`emRunInputsFromSettings`) and passes the
 * operating point it is asking about on top.  Everything that was already read
 * from `localStorage` inside the literal — the whole mesh block, the coil
 * temperature, the magnet temperature, γ's d-axis reference, the winding
 * connection, the machine and its materials — is still read here, exactly as it
 * was, so a payload built from the same browser is byte-identical to the one
 * this app has always sent.
 */
import { currentGeoJson, currentMatJson } from './apiAuth';
import { EDDY_DEFAULT_STEPS } from './eddySteps';

/** The excitation sources the transient accepts. */
export type DriveKind =
  'current' | 'voltage' | 'pwm_voltage' | 'custom_current' | 'bldc_current';

const DRIVES: readonly DriveKind[] = [
  'current', 'voltage', 'pwm_voltage', 'custom_current', 'bldc_current'];

/** The pack on the DC link, as the backend takes it.  Structurally the
 *  Electromagnetic panel's `BatteryPack`; declared here so this module owns no
 *  import into a component. */
export interface EmRunBattery {
  v_oc?: number | null; v_nom?: number | null;
  v_min?: number | null; v_max?: number | null;
  cells?: number | null; n_parallel?: number | null;
  r_int_mohm?: number | null; capacity_ah?: number | null;
  i_charge_max_a?: number | null; chemistry?: string | null;
}

/** Everything the CALLER holds.  What is not here is read from the panel
 *  settings by the builder itself — because it is the same for every caller by
 *  definition (the mesh the Mesh tab is showing, the temperatures and the
 *  winding the Electromagnetic tab is showing, the machine that is loaded). */
export interface EmRunInputs {
  /** true = hand back the LAST saved transient instead of solving.  Only the
   *  mount path ever asks for it; a Run never does. */
  restore: boolean;
  /** steps per electrical period (`n_steps_per_period`) */
  steps: number;
  gamma_deg: number;
  I_phase_rms: number;
  drive: DriveKind;
  /** voltage drives: fundamental phase-voltage amplitude [V peak] and its angle */
  vPeak: number;
  vDelta: number;
  // (no vBus / fSwitch: a pwm_voltage run takes its DC link and carrier
  //  from the CONTROLLER, resolved by the backend — 2026-09-24)
  /** bldc_current: flat-top block amplitude [A] */
  iBlock: number;
  /** custom_current: JSON [[θe_deg, i_A], …] over one period */
  waveform: string;
  /** the machine's pack — sent ONLY on an imposed-voltage run that has one */
  battery: EmRunBattery | null;
  busCouple: boolean;
  chargeMax: boolean;
  /** field-based magnet / shaft eddy losses (`rotor_eddy`) */
  fieldLosses: boolean;
  /** the coupled σ·∂A/∂t solve (`eddy`) */
  eddyCoupled: boolean;
  /** per-element irreversible demagnetisation */
  demag: boolean;
  /** band-limit T(t) to the physical 6·k orders */
  torqueFilter: boolean;
  /** discard the backend's cached frames before recomputing */
  fresh: boolean;
  /** the id Stop cancels by */
  run_id: string;
}

function readMeshSetting<T>(key: string, def: T): T {
  try {
    const raw = localStorage.getItem(`mesh.${key}`);
    return raw == null ? def : (JSON.parse(raw) as T);
  } catch { return def; }
}

function readSimSetting<T>(key: string, def: T): T {
  try {
    const raw = localStorage.getItem(`sim.${key}`);
    return raw == null ? def : (JSON.parse(raw) as T);
  } catch { return def; }
}

const numOr = (v: unknown, def: number): number => {
  const n = Number(v);
  return Number.isFinite(n) ? n : def;
};

/**
 * The EXCITATION half of the body.
 *
 * Its own function because it is the only part with branching rules worth
 * pinning (`__tests__/emRunPayload.test.mjs`): each source's own parameters are
 * sent ONLY with that source, because the backend refuses a `v_bus` sent with a
 * current drive rather than ignore it — a parameter that is quietly dropped is
 * how a run answers a different question than the one on screen.
 */
export function driveFields(
  inp: Pick<EmRunInputs, 'drive' | 'vPeak' | 'vDelta'
  | 'iBlock' | 'waveform' | 'battery' | 'busCouple' | 'chargeMax'>,
): Record<string, unknown> {
  const { drive, battery } = inp;
  const imposedV = drive === 'voltage' || drive === 'pwm_voltage';
  return {
    // Drive mode: "voltage" imposes sinusoidal phase voltages — the currents
    // become the machine's own response (incl. back-EMF-harmonic parasitics)
    // and the backend also runs a matched-fundamental current-drive reference
    // (harm_ref) so ΔP_harm = the watt cost of those harmonic currents.
    drive,
    ...(imposedV
      ? { v_phase_peak: inp.vPeak, v_delta_deg: inp.vDelta, harm_ref: true }
      : { v_phase_peak: 0, v_delta_deg: 0, harm_ref: false }),
    // pwm_voltage sends NO v_bus / f_switch (2026-09-24): the PWM drive is
    // the Controller's, and the route resolves both from it.
    ...(drive === 'bldc_current' ? { i_block: inp.iBlock } : {}),
    ...(drive === 'custom_current' ? { waveform: inp.waveform } : {}),
    // ── THE PACK ON THE DC LINK ────────────────────────────────────
    // Only ever sent on an imposed-voltage run of a machine that HAS a
    // battery: on any other run there is no bridge for it to be behind, and
    // the request stays exactly the one this app has always built.  The two
    // loop flags ride with it; without a battery the backend refuses them, so
    // they are gated on the same condition rather than separately.
    ...((imposedV && battery)
      ? { battery: JSON.stringify(battery),
          ...(inp.busCouple ? { bus_couple: true } : {}),
          ...(inp.chargeMax ? { charge_max: true } : {}) }
      : {}),
  };
}

/**
 * The body itself.
 *
 * Typed inputs — ONE source for both transports.  The direct GET goes through
 * FastAPI (which coerces query strings to the typed signature); the kernel POST
 * calls `get_fem_transient` DIRECTLY, so the JSON types must be REAL here (a
 * string "false" would read truthy → e.g. a spurious restore).
 */
export function buildEmRunPayload(inp: EmRunInputs): Record<string, unknown> {
  return {
    // restore=true → on open, return the LAST saved transient (stale-flagged if
    // params differ) instead of recomputing.  Only the Run button omits it.
    restore:            inp.restore,
    n_steps_per_period: inp.steps,
    n_periods:          1,
    gamma_deg:          inp.gamma_deg,
    I_phase_rms:        inp.I_phase_rms,
    ...driveFields(inp),
    mesh_size_mm:       readMeshSetting('meshSize',    4.0),
    min_size_mm:        readMeshSetting('minSize',     0.3),
    outer_air_factor:   readMeshSetting('outerAir',    1.3),
    motion_band:        readMeshSetting('motionBand',  true),
    band_thickness_mm:  readMeshSetting('bandThickness', 0.4),
    gap_layers:         readMeshSetting('gapLayers',   2),
    n_sectors:          readMeshSetting('nSectors',    1),
    stator_fillet_mm:   0,   // native geometry — extra smoothing removed
    // ALWAYS use the sliding band for the transient torque/back-EMF — meshes
    // ONCE and rotates the rotor through a moving band (clean, physical T(t)),
    // vs remesh-per-frame which injects huge numerical ripple.  Decoupled from
    // the Mesh-tab toggle (that now only controls mesh VISUALISATION).
    sliding_band:       true,
    // Field-based magnet/shaft eddy losses (σ·∂A/∂t solve) vs the slab estimate.
    rotor_eddy:         inp.fieldLosses,
    // Coupled σ·∂A/∂t eddy-current solve.  ON: the induced currents are solved
    // WITH the field (copper loss is the solved 2-D value, not a post-process)
    // and the run's field snapshot carries the real eddy J⟳, so the J⟳ / Loss
    // views render it instead of running a second transient.
    eddy:               inp.eddyCoupled,
    // Keep the last frame's field server-side for the J⟳ / Loss views.  Not
    // an extra solve — it is the frame this run just finished.  It is also the
    // ONLY thing that lets a thermal solve find this run's per-element losses.
    field_snapshot:     true,
    // Per-element irreversible demagnetisation: de-rates Br → torque/EMF + %-map.
    demag:              inp.demag,
    // Band-limit T(t) to the physical 6·k orders (UI toggle, default ON).
    torque_filter:      inp.torqueFilter,
    // Bit-identical pole/slot mesh (Mesh-tab "Periodic" toggle).
    pole_copy:          readMeshSetting('poleCopy', false),
    // ANSYS-style concentric-ring air-gap mesh (Mesh-tab "Air-gap mesh" toggle).
    // template halves need the belt → force structured gap when template on
    structured_gap:     readMeshSetting('structuredGap', false) || readMeshSetting('ironTemplate', true),
    // Harmonic gap coupling (Mesh-tab "Harmonic gap"): step-independent RAW ripple.
    airgap_macro:       readMeshSetting('harmonicGap', false),
    // P2 is the calculation basis (Mesh-tab toggle, default ON): quadratic
    // elements → B linear per element → smooth Arkkio torque, no P1 staircase,
    // and the forbidden-order noise floor converges to 0 with mesh refinement.
    // P1 is deleted, so this is a constant now — sending anything else raises
    // in the solver rather than silently downgrading.
    element_order:      2,
    // Deterministic template iron mesh (Mesh-tab "Template iron" toggle).
    iron_template:      readMeshSetting('ironTemplate', true),
    // Geometry-driven CDT mesh (Mesh-tab "Geometry-driven mesh" toggle, default ON).
    geo_mesh:           readMeshSetting('geoMesh', true),
    // SPEED — sent explicitly whenever the panel has one, so an ordinary
    // user's rpm applies to THEIR solve without touching the shared config
    // (omitted → the backend falls back to the shared simulation.rpm).
    ...(() => {
      const v = Number(readSimSetting('rpm', NaN));
      return Number.isFinite(v) && v > 0 ? { rpm: v } : {};
    })(),
    // Copper-loss physics: coil temperature → ρ_Cu(T); end-winding factor
    // (0 = auto-estimate from geometry) for the copper the 2-D field misses.
    coil_temp_c:        readSimSetting('coilTemp',   120.0),
    end_winding_factor: readSimSetting('endWinding',   0.0),
    // MAGNET temperature: sent only when the field holds a number.  Blank
    // means "the card as the library quotes it" — the behaviour of every run
    // before this field existed — and sending a 0 for blank would demagnetise
    // the machine instead, the same trap the d-axis field below avoids.
    ...(() => {
      const s = String(readSimSetting<string>('magnetTempC', '') ?? '').trim();
      const v = Number(s);
      return s === '' || !Number.isFinite(v) ? {} : { magnet_temp_c: v };
    })(),
    // Operating mode — generator shifts the drive 180 deg el server-side.
    mode: readSimSetting<string>('opMode', 'motor'),
    // D-AXIS: sent only when PINNED.  Blank means "measure it", and the
    // backend's own resolver decides that from the shared config — sending
    // a 0 for "blank" would pin the reference to zero degrees instead.
    ...(() => {
      const _d = String(readSimSetting<string>('daxisDeg', '') ?? '').trim();
      return _d === '' || !Number.isFinite(Number(_d))
        ? {} : { daxis_deg: Number(_d) };
    })(),
    // WINDING — send the SELECTED connection explicitly.  The selector
    // buttons write the shared config through a debounced sync, and the
    // auto-run raced it: the run computed with the OLD winding while the UI
    // labelled it with the new one (measured live: a 4S run and a 2S-2P run
    // both returned 32.11 Nm).  With the label in the request the backend
    // resolves n_parallel from exactly what the selector shows.
    ...(readSimSetting<string>('connection', '')
      ? { connection: readSimSetting<string>('connection', '') } : {}),
    // TERMINAL connection, sent explicitly for the same reason: the selector
    // writes the shared config through a debounced sync, and a Run pressed
    // inside that window would otherwise solve the previous connection while
    // the UI labels it with the new one.
    star_delta: (readSimSetting<string>('starDelta', 'star') || 'star'),
    // Per-part mesh size from the Mesh tab (same localStorage key).
    component_mesh:     JSON.stringify(readMeshSetting<Record<string, number>>('componentMesh', {})),
    // SAME include_frames/n_frames as the FemAnimationViewer so both panels hit
    // the exact same backend cache key (one solve, not two).
    include_frames:     true,
    n_frames:           inp.steps,
    run_id:             inp.run_id,
    fresh:              inp.fresh,
    // The kernel POST bypasses the fetch interceptor's ?geo=/?mat= — the
    // caller's own geometry and materials must ride the payload, or the
    // Run solves the SHARED config while the field views show the copy.
    ...(() => { const g = currentGeoJson(); return g ? { geo: g } : {}; })(),
    ...(() => { const m = currentMatJson(); return m ? { mat: m } : {}; })(),
  };
}

/**
 * The Electromagnetic panel's inputs, read from where that panel PERSISTS them.
 *
 * For a caller that is not the Electromagnetic panel — today the Thermal tab,
 * making the run its solve could not find.  Standing project rule: every physics
 * setting of a run is read from where the user set it, never from a default
 * invented in the panel that needs it.  The three constants (`fieldLosses`,
 * `eddyCoupled`, `torqueFilter`) are constants in that panel too, and
 * `chargeMax` / `fresh` are one-shot presses that only exist while a finger is
 * on a button — so a run made from settings is never a max-charge search and
 * never discards the cache.
 *
 * `over` is what the caller knows better: the Thermal tab pins the operating
 * point it is asking ABOUT onto the run it is asking FOR.
 */
export function emRunInputsFromSettings(
  over: Partial<EmRunInputs> = {},
): EmRunInputs {
  const drive = readSimSetting<string>('drive', 'current');
  return {
    restore: false,
    // No stored count = the panel's own default (eddy runs: 72, lib/eddySteps).
    steps: numOr(readSimSetting('stepsPP', EDDY_DEFAULT_STEPS), EDDY_DEFAULT_STEPS),
    gamma_deg: numOr(readSimSetting('gamma', 0), 0),
    I_phase_rms: Math.max(0, numOr(readSimSetting('current', 0), 0)),
    drive: (DRIVES as readonly string[]).includes(drive)
      ? (drive as DriveKind) : 'current',
    vPeak: numOr(readSimSetting('vPeak', 30.0), 30.0),
    vDelta: numOr(readSimSetting('vDelta', 0), 0),
    iBlock: numOr(readSimSetting('iBlock', 0), 0),
    waveform: String(readSimSetting<string>('waveform', '') ?? ''),
    // NOT read from storage: the pack is loaded from the family context by the
    // Electromagnetic panel itself, and it is only ever sent on an imposed-
    // voltage run — which the orchestrator refuses outright.  A guess here
    // would be a pack on the DC link that nobody put there.
    battery: null,
    busCouple: readSimSetting('busCouple', true) === true,
    chargeMax: false,
    fieldLosses: true,
    eddyCoupled: true,
    demag: readSimSetting('demag', true) === true,
    torqueFilter: false,
    fresh: false,
    run_id: '',
    ...over,
  };
}
