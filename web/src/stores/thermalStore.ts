/**
 * thermalStore — everything the Thermal tab is showing, kept OUTSIDE the panel's
 * component tree.
 *
 * A clone of `mechanicalStore`, for the same reason and with the same three
 * layers of survival: the Thermal tab is not `keepMounted` in App.tsx (it draws
 * its own picture, so it does not take the AppBar's viewer cluster), which means
 * leaving the tab unmounts the panel and `useState` results would go with it — a
 * minute-long conduction solve thrown away by clicking "Electromagnetic".
 *
 *   1. TAB SWITCH   — this store (module scope, in memory).  Exact restoration,
 *                     no round trip, including a solve still in flight.
 *   2. PAGE RELOAD / API RESTART — the backend's `/api/thermal/last`, fetched
 *                     once, on the first mount that finds this store empty.
 *   3. THE SMALL CHOICES — localStorage under `therm.*`.  A field payload is far
 *                     too big and too machine-specific for localStorage; a
 *                     cooling mode is not.
 *
 * NOTHING here solves on its own.  `hydrate()` reads, `solveField()` and
 * `solveCoupled()` run only when a button is pressed — and neither of them owns
 * an operating point: the current, the angle, the speed, the coil temperature
 * and the step count are ALWAYS read from the Electromagnetic tab (standing project
 * rule: every physics setting of a run is read from where the user set it).
 */
import { create } from 'zustand';

import { liveGeoSig } from '../components/common/geoSig';
import { normalizeLocalRows } from '../components/compare/resultRows';
import type { LocalRow } from '../components/compare/resultRows';
import { adoptSettings, loadPanelSettings, savePanelSettings } from '../lib/panelSettings';
// The ORCHESTRATOR — the only thing in this app allowed to chain an
// electromagnetic run to a thermal solve.  This tab calls it when its own solve
// is refused for want of a run; it never solves electromagnetics itself, and
// neither does the route it calls.
import { cancelCoupled, registerThermalPanelBlock, runCoupled } from '../components/simulation/coupledApi';
import { buildEmRunPayload, emRunInputsFromSettings } from '../lib/emRunPayload';
// The duty-cycle editor's point lives in the CATALOGUE, not on the
// Electromagnetic tab — `emRunBodyAt` is what pins it onto the run.
import { emRunBodyAt } from '../components/thermal/dutyCycleOffer';
import type { EmRunAt } from '../components/thermal/dutyCycleOffer';
import {
  fetchCoupled, fetchLastThermal, fetchThermalField, fetchThermalMesh,
  fetchMeshParams, isNoEmRun, readLastSecs, readTherm, simOperatingPoint,
  writeLastSecs, writeTherm,
} from '../components/thermal/api';
import type {
  BoreMode, CoolMode, CoupledResult, EndFaceMode, FrameMode, ThermKind,
  ThermView, ThermalField, ThermalMeshPayload, ThermalMeshRequest,
  ThermalRequest,
} from '../components/thermal/api';
import { tileFullRing } from '../components/thermal/types';
import { readMeshSettings } from '../components/common/motorSettings';

/* The staleness test is IMPORTED, not re-implemented: "the result on screen is
   of a different machine" must mean exactly one thing across the app, and this
   is where it was first written (2026-09-06). */
export { isStale } from './mechanicalStore';

const msg = (e: unknown): string => (e instanceof Error ? e.message : String(e));

/** A text field's number, or the default — never `Number('') === 0`, which
 *  would silently solve a 0 °C ambient the user never typed. */
const num = (v: string, def: number): number => {
  const t = (v ?? '').trim();
  const n = Number(t);
  return t !== '' && Number.isFinite(n) ? n : def;
};

/* ── reading what a previous version of this tab persisted ─────────────────
   The cooling inputs were re-shaped on 2026-09-07 (two surfaces instead of one,
   the outlet temperature gone, the flow now required).  Every key that survived
   is still read under its old name, so nobody's settings reset; the two readers
   below are what makes a leftover value that is no longer meaningful — a blank
   `flowLpm` from the days when blank meant "derive it", a `coolMode` from a
   future branch — fall back to the default instead of being sent. */

/** A persisted text field, with blank treated as absent. */
const readStr = (key: string, def: string): string => {
  const v = readTherm<unknown>(key, def);
  const s = typeof v === 'number' ? String(v) : (typeof v === 'string' ? v : '');
  return s.trim() === '' ? def : s;
};

/** A persisted enum, with anything unrecognised falling back to the default. */
const readMode = <T extends string>(key: string, allowed: readonly T[], def: T): T => {
  const v = readTherm<unknown>(key, def);
  return (typeof v === 'string' && (allowed as readonly string[]).includes(v))
    ? (v as T) : def;
};

const COOL_MODES: readonly CoolMode[] = ['air', 'liquid', 'manual', 'none',
                                         'robotics'];
const BORE_MODES: readonly BoreMode[] = ['none', 'air', 'liquid', 'still'];
const END_FACE_MODES: readonly EndFaceMode[] = ['still', 'none'];
const FRAME_MODES: readonly FrameMode[] = ['housed', 'open'];

interface Slice<T> {
  data: T | null;
  busy: boolean;
  err: string | null;
  /** geometry signature at the moment it was solved / adopted — see `isStale` */
  geoSig: string | null;
  /** the backend's own staleness verdict; null = unknown */
  backendStale: boolean | null;
  /** ISO stamp from `/last` for a restored result; null for a fresh solve */
  restoredAt: string | null;
  /** `Date.now()` when this solve was started, for the ticking timer.  Client
   *  side ON PURPOSE: the clock has to move while the request is in flight and
   *  the backend has said nothing yet.  What ends up on screen afterwards is
   *  the backend's own `elapsed_s`. */
  startedAt: number | null;
}

const EMPTY: Slice<never> = {
  data: null, busy: false, err: null, geoSig: null,
  backendStale: null, restoredAt: null, startedAt: null,
};

/** The Electromagnetic run THIS TAB is making for itself, non-null exactly
 *  while it is in flight.  See `emRunThroughLoop`. */
export interface EmFallback {
  /** the run-id the loop was launched with — what Stop cancels */
  runId: string;
  /** the refusal's own sentence: which operating point / temperature / frame
   *  count the stored runs did not cover.  The strip's tooltip, so the user can
   *  see WHY a Solve turned into a transient. */
  why: string;
  /** Stop was pressed and the loop is winding down (it stops between phases,
   *  and mid-transient at the next frame). */
  stopping: boolean;
  /** WHOSE run this is, when it is not this tab's own Solve: the duty-cycle
   *  editor's calibration point reads differently from "the point on the
   *  Electromagnetic tab", and the strip must not claim the wrong one.  `null`
   *  = the field Solve's own fallback. */
  label?: string | null;
}

export interface ThermalState {
  /** `/last` has been consulted once — a second mount must not re-ask, or a
   *  restored-then-cleared result would come back on every tab switch. */
  hydrated: boolean;

  field: Slice<ThermalField>;
  coupled: Slice<CoupledResult>;

  /** non-null while this tab is making the Electromagnetic run its own solve
   *  could not find */
  emFallback: EmFallback | null;

  /** the mesh itself: the bare cross-section drawn when nothing has been
   *  solved, and what the Build mesh button produces */
  geom: ThermalMeshPayload | null;
  geomBusy: boolean;
  geomErr: string | null;
  geomStartedAt: number | null;

  /** last measured seconds per kind of solve — the estimate the timer shows
   *  while the next one runs.  Mirrors localStorage `therm.lastSecs.<kind>`. */
  est: Record<ThermKind, number | null>;

  // ── cooling inputs (strings: they are text fields) ────────────────────────
  /* TWO surfaces since 2026-09-07: the outer stator surface and the inner rotor
     bore, each with its own mode and its own numbers.  A machine cooled through
     the hollow shaft is a real machine, and it was not expressible before. */

  /** the OUTER stator / housing surface */
  coolMode: CoolMode;
  /** ambient air / enclosure temperature, °C — ONE field, because it is the
   *  same air on both surfaces */
  ambientT: string;
  /** blow speed over the housing, m/s (outer air mode) */
  airSpeed: string;
  fluid: string;
  /** coolant INLET, °C (outer liquid mode).  There is no outlet field any more:
   *  how hot the coolant comes back is what the machine does to it, so it is a
   *  result of the flow below rather than a second number to guess. */
  tIn: string;
  /** film coefficient typed by hand, W/m²K (manual mode) */
  hConv: string;
  /** litres per minute — REQUIRED and > 0 now that the outlet is a result: the
   *  pump is what the engineer actually chooses. */
  flowLpm: string;

  /** the INNER rotor bore (the hollow shaft's inner diameter), off by default */
  boreMode: BoreMode;
  /** blow speed through the bore, m/s — against `ambientT`, the same air */
  boreAirSpeed: string;
  boreFluid: string;
  /** bore coolant inlet, °C, and its flow in L/min */
  boreTIn: string;
  boreFlowLpm: string;

  /** How much SHAFT sticks out of the housing on each side, mm — '0' = off.
   *  The rotor's third heat path (user 2026-09-07: "торцы и лобовые части —
   *  только для вала, всё остальное вращается внутри мотора"): the end faces
   *  and the end windings turn inside the closed housing and have nowhere else
   *  to send their heat, but the shaft stubs sit in the room's air. */
  shaftExtMm: string;
  /** how many ends come out: '2' = through shaft, '1' = one end capped */
  shaftExtSides: string;

  /** How the machine is BUILT — 'housed' (the default, and every machine this
   *  tab has ever solved) or 'open' (user 2026-09-09, the 40 mm CIANO14: no
   *  housing at all, the tooth blocks with their coils between two end plates
   *  on standoff pins, the end turns and the axial channels between
   *  neighbouring coils in the propeller wash). */
  frame: FrameMode;
  /** frame=open: air over the end turns and through the slot channels, m/s.
   *  '0' means "whatever is blowing on the housing" — it is the same wash. */
  openAirSpeed: string;

  // ── the ROBOTICS mode's own inputs (2026-09-14) ───────────────────────────
  /* A robot joint is not a machine with a fan: it stands in a room and is
     BOLTED to an arm.  These four are the difference, and they belong to the
     one mode that reads them — a half-configured still-air machine would
     otherwise look exactly like a converged answer. */

  /** total hemispherical emissivity of the housing, 0…1.  On an 85 mm machine
   *  in still air radiation carries MORE than convection does, so this decides
   *  over half of what the housing loses. */
  emissivity: string;
  /** the bolted flange's contact conductance to the arm, W/K — '0' = bolted to
   *  nothing.  Assumed until somebody measures the joint: the panel says so. */
  mountG: string;
  /** the temperature the mount is HELD at, °C — BLANK means the ambient, and a
   *  blank field must stay blank rather than being filled with it, or a default
   *  would read as a number somebody chose. */
  mountT: string;
  /** are the machine's AXIAL faces exposed?  'still' = the end turns and the
   *  core / magnet end faces are in the room's air. */
  endFaces: EndFaceMode;
  /** how many ends are exposed: '2', or '1' when one end is against a gearbox */
  endFaceSides: string;

  /** iteration cap of the coupled loop */
  maxIter: string;

  /** The mesh block the next request will carry — the server's `mesh:` block,
   *  re-read before every build and solve (see api.fetchMeshParams). */
  meshCfg: ThermalMeshRequest | null;

  /** The tab's OWN comparison stack — the Configure tab's "Saved
   *  configurations", for cooling designs (user 2026-09-07: "сделай локальное
   *  сравнение по параметрам тепловой симуляции, только как в Configure").
   *  A PERSISTED field like any other, so the stack survives a reload and is
   *  the same in every browser; the permanent library is still the Compare tab. */
  compareRows: LocalRow[];

  // ── view choices ─────────────────────────────────────────────────────────
  view: ThermView;
  /** rank-equalised temperature colours (default) vs a true linear °C axis */
  eqTemp: boolean;
  showFlux: boolean;

  set: <K extends keyof ThermalState>(k: K, v: ThermalState[K]) => void;

  hydrate: () => Promise<void>;
  /** Adopt the backend's last result when it is NEWER than what is on screen.
   *  `hydrate` runs once per SPA session, so a map solved after that first
   *  visit — the coupled loop's own, at its converged temperatures — never
   *  reached this tab (2026-09-10).  After a coupled run every tab has to show
   *  the same answer; that is what the loop is for. */
  refreshLast: () => Promise<void>;
  /** Build (or re-fetch) the solid sub-mesh at the Mesh tab's settings.  NEVER
   *  called on a field edit — each build is seconds of the mesher, so it is a
   *  button. */
  loadGeometry: () => Promise<void>;
  solveField: () => Promise<void>;
  solveCoupled: () => Promise<void>;
  /** Make ONE Electromagnetic run at a point that is NOT this tab's — the
   *  duty-cycle editor's calibration duty (2026-09-14).  Through the same
   *  orchestrator the field Solve's own fallback uses, so there is one chaining
   *  path in this app and not two, and with the same progress strip and Stop.
   *
   *  It CHANGES NOTHING here: the map it produces is not adopted as this tab's
   *  field result and no setting of any panel is written — the caller asked for
   *  a run at another duty's point, and a picture of that point replacing the
   *  one on screen would be the silent state mutation this project forbids.
   *  Resolves to `null` on success, or the refusal's sentence. */
  emRunAtPoint: (at: EmRunAt, why: string) => Promise<string | null>;
  /** Cancel the Electromagnetic run this tab started for itself.  Does nothing
   *  when there is none — the Stop button only exists while there is. */
  stopEmFallback: () => void;
}

/** The small choices persist under `therm.*`.  The operating point is NOT among
 *  them — it always comes from the Electromagnetic tab. */
const PERSISTED: (keyof ThermalState)[] = [
  'coolMode', 'ambientT', 'airSpeed', 'fluid', 'tIn', 'hConv', 'flowLpm',
  'boreMode', 'boreAirSpeed', 'boreFluid', 'boreTIn', 'boreFlowLpm',
  'shaftExtMm', 'shaftExtSides',
  'frame', 'openAirSpeed',
  'emissivity', 'mountG', 'mountT', 'endFaces', 'endFaceSides',
  'maxIter', 'view', 'eqTemp', 'showFlux',
  // The local comparison stack rides the SAME path as every other remembered
  // field (localStorage + the server), so there is one way this tab remembers
  // things rather than two.
  'compareRows',
];

/** Remember how long this kind of solve took, so the NEXT one can say "~40 s".
 *
 *  Written both to the store (the timer re-renders) and to localStorage (the
 *  estimate is there after a reload, before anything has been solved this
 *  session).  Only a REAL solve teaches it — a cache hit took no time, and an
 *  estimate taught by cache hits would promise an instant answer forever. */
function noteSecs(set: (p: Partial<ThermalState>) => void,
                  get: () => ThermalState,
                  kind: ThermKind, secs: number | null | undefined): void {
  if (secs === null || secs === undefined || !Number.isFinite(secs) || secs <= 0) return;
  writeLastSecs(kind, secs);
  set({ est: { ...get().est, [kind]: Math.round(secs * 10) / 10 } });
}

/* ═══════════════════════════════════════════════════════════════════════════
 * The cooling half of the request
 *
 * PURE and exported so it can be pinned by a test without a browser: its body is
 * copied verbatim into `components/thermal/__tests__/coolingPayload.test.mjs`,
 * which fixes WHICH fields each mode sends.  That matters twice over — an
 * `air_speed_mps` shipped with a liquid jacket is a parameter the solver keys
 * its cache on and never uses, and a `fluid_temp_out_c` shipped at all is the
 * boundary condition this tab stopped pretending to know on 2026-09-07 (the
 * outlet temperature is a RESULT of the flow).
 * ═══════════════════════════════════════════════════════════════════════════ */

/** The two surfaces exactly as the panel holds them — text fields, because
 *  that is what the user is typing into. */
export interface CoolingInputs {
  coolMode: CoolMode; ambientT: string; airSpeed: string; fluid: string;
  tIn: string; flowLpm: string; hConv: string;
  boreMode: BoreMode; boreAirSpeed: string; boreFluid: string;
  boreTIn: string; boreFlowLpm: string;
  shaftExtMm: string; shaftExtSides: string;
  frame: FrameMode; openAirSpeed: string;
  emissivity: string; mountG: string; mountT: string;
  endFaces: EndFaceMode; endFaceSides: string;
}

type CoolingRequest = Pick<ThermalRequest,
  'cooling_mode' | 'ambient_temp' | 'h_conv' | 'air_speed_mps' | 'fluid'
  | 'fluid_temp_in_c' | 'flow_lpm' | 'bore_mode' | 'bore_air_speed_mps'
  | 'bore_fluid' | 'bore_fluid_temp_in_c' | 'bore_flow_lpm'
  | 'shaft_ext_length_mm' | 'shaft_ext_sides' | 'frame' | 'open_air_speed_mps'
  | 'emissivity' | 'end_faces' | 'end_face_sides' | 'mount_g_w_per_k'
  | 'mount_temp_c'>;

export function coolingFields(s: CoolingInputs): CoolingRequest {
  const ambient = num(s.ambientT, 40);
  const liquid = s.coolMode === 'liquid';
  const boreLiquid = s.boreMode === 'liquid';
  // ── THE ROBOTICS MODE's own four (2026-09-14) ───────────────────────────
  // Same rule as everything else here: a parameter the chosen mode does not use
  // is not sent.  `emissivity` and the two end-face flags ride with the mode;
  // `end_face_sides` does not ride alone, because a side count beside
  // `end_faces: none` is exactly the unused cache-splitting parameter this
  // function exists to keep off the wire.  The MOUNT is gated on being > 0 —
  // the same gate the exposed shaft length has — and the mount TEMPERATURE only
  // when it was typed: blank means the ambient, and sending the ambient in its
  // place would make a default look like a number somebody chose.
  //
  // ASYMMETRY, ON PURPOSE (mirrored from `thermal_settings.cooling_fields`):
  // the ROUTER reads `mount_g_w_per_k` in every cooling mode, but the PANEL only
  // offers the field with the robotics mode, so that is the only mode this
  // builder can send it from.
  const robot = s.coolMode === 'robotics';
  const endFaces: EndFaceMode = s.endFaces === 'none' ? 'none' : 'still';
  const mountG = robot ? Math.max(0, num(s.mountG, 0)) : 0;
  const mountTyped = (s.mountT ?? '').trim() !== '';
  // The exposed shaft is OFF at 0 mm, and off means the two shaft fields are
  // not sent at all — `shaft_ext_sides` beside a length of zero is a parameter
  // the solver keys its cache on and never uses, exactly like an `air_speed_mps`
  // beside a water jacket.  The diameter is never sent: it is derived from the
  // geometry, and a second place to type a shaft diameter is a second place for
  // it to disagree with the CAD.
  const shaftMm = Math.max(0, num(s.shaftExtMm, 0));
  return {
    cooling_mode: s.coolMode,
    // Always sent: it is the ambient the outer film works against AND the
    // temperature of the air blown through the bore — the same air.
    ambient_temp: ambient,
    h_conv: s.coolMode === 'manual' ? num(s.hConv, 50) : undefined,
    air_speed_mps: s.coolMode === 'air' ? num(s.airSpeed, 0) : undefined,
    fluid: liquid ? s.fluid : undefined,
    // The inlet defaults to the ambient rather than to a number of its own:
    // a coolant loop nobody has configured sits at room temperature.
    fluid_temp_in_c: liquid ? num(s.tIn, ambient) : undefined,
    // Required > 0 by the backend.  The fallback only ever ships if something
    // solves around `coolingIssue`, which blocks an empty field in the UI.
    flow_lpm: liquid ? num(s.flowLpm, 8) : undefined,
    bore_mode: s.boreMode,
    bore_air_speed_mps: s.boreMode === 'air' ? num(s.boreAirSpeed, 0) : undefined,
    bore_fluid: boreLiquid ? s.boreFluid : undefined,
    bore_fluid_temp_in_c: boreLiquid ? num(s.boreTIn, ambient) : undefined,
    bore_flow_lpm: boreLiquid ? num(s.boreFlowLpm, 4) : undefined,
    shaft_ext_length_mm: shaftMm > 0 ? shaftMm : undefined,
    shaft_ext_sides: shaftMm > 0
      ? (num(s.shaftExtSides, 2) === 1 ? 1 : 2) : undefined,
    // THE FRAME, sent only when the machine is OPEN — the same rule the shaft
    // follows.  A `frame: 'housed'` on the wire is a value the solver keys its
    // cache on and never reads, so it would split one machine's cache in two.
    // The SPEED rides with it even at 0: 0 is meaningful there (the backend
    // takes the housing's own air speed, because it is the same wash), which is
    // exactly why it is not gated on being > 0 the way the stub length is.
    frame: s.frame === 'open' ? 'open' : undefined,
    open_air_speed_mps: s.frame === 'open'
      ? Math.max(0, num(s.openAirSpeed, 0)) : undefined,
    emissivity: robot ? Math.min(Math.max(num(s.emissivity, 0.9), 0), 1)
                      : undefined,
    end_faces: robot ? endFaces : undefined,
    end_face_sides: (robot && endFaces !== 'none')
      ? (num(s.endFaceSides, 2) === 1 ? 1 : 2) : undefined,
    mount_g_w_per_k: mountG > 0 ? mountG : undefined,
    mount_temp_c: (mountG > 0 && mountTyped) ? num(s.mountT, ambient) : undefined,
  };
}

/** What is wrong with the cooling, as a sentence an engineer can act on — or
 *  `null`.  Solve is DISABLED while this returns something: a client-facing tab
 *  validates its input loudly instead of sending an impossible machine and
 *  translating the solver's 422 afterwards. */
export function coolingIssue(s: CoolingInputs): string | null {
  if (s.coolMode === 'liquid' && !(num(s.flowLpm, 0) > 0))
    return 'coolant flow must be greater than 0 L/min';
  if (s.coolMode === 'manual' && !(num(s.hConv, 0) > 0))
    return 'h must be greater than 0 W/m²K';
  if (s.boreMode === 'liquid' && !(num(s.boreFlowLpm, 0) > 0))
    return 'bore coolant flow must be greater than 0 L/min';
  if (s.boreMode === 'still' && s.coolMode !== 'robotics')
    return ('a still (unventilated) bore belongs to the robotics mode — it '
            + 'radiates out of the two ends at the machine’s emissivity, and '
            + 'that input only exists there');
  if (s.coolMode === 'robotics') {
    const eps = num(s.emissivity, 0.9);
    if (!(eps >= 0 && eps <= 1)) return 'emissivity must be between 0 and 1';
  }
  const mountG = s.coolMode === 'robotics' ? num(s.mountG, 0) : 0;
  if (mountG < 0) return 'the mount conductance cannot be negative';
  // WIDENED 2026-09-14 (mirrors `thermal_settings.cooling_issue`): a machine
  // bolted to a cold arm IS cooled, even with every film switched off — the
  // mount is a conductance to a held temperature and the steady problem has a
  // solution.  What has nowhere to send its heat is the machine with no door.
  if (s.coolMode === 'none' && s.boreMode === 'none' && !(mountG > 0))
    return 'no cooled surface and no mount conductance — the heat has nowhere to leave';
  return null;
}

/**
 * The request BOTH solves send.
 *
 * One builder, so the field solve and the coupled solve describe the same
 * machine under the same cooling: two copies of this would be two ways for the
 * coupled loop's converged temperature to belong to a different boundary
 * condition than the map beside it.
 */
function buildRequest(s: ThermalState, mesh: ThermalMeshRequest): ThermalRequest {
  return {
    ...coolingFields(s),
    // …and the physics of the machine itself, from the Electromagnetic tab.
    ...simOperatingPoint(),
    n_periods: 1,
    ...mesh,
  };
}

/* ═══════════════════════════════════════════════════════════════════════════
 * When there is no Electromagnetic run to heat the machine with
 *
 * The thermal ROUTE never computes electromagnetic losses — it answers 422
 * `no_electromagnetic_run` naming the run that is missing, and that refusal is a
 * standing rule of this project (2026-09-07).  What was left for the user to do
 * by hand — go to the Electromagnetic tab, press Run, come back, press Solve —
 * this tab now does for them (user 2026-09-08: *"лучше, чтобы она сама считала
 * электромагнетику и понимала, когда это нужно делать, а когда не надо"*).
 *
 * NOT by solving anything here, and not by relaxing the route: through the
 * ORCHESTRATOR, `POST /api/coupled/run`, the one thing in this app allowed to
 * chain the two solvers.  With `max_iter: 1` that is exactly ONE electromagnetic
 * run at the temperatures the Electromagnetic tab is showing, followed by the
 * thermal solve with THIS tab's cooling — the two halves the user would have
 * done by hand, in that order, and nothing more.  When a matching run already
 * exists none of this happens: the plain solve answers and this file is not
 * reached.
 * ═══════════════════════════════════════════════════════════════════════════ */

/** The cooling THIS panel is showing, in the field names `thermal_settings`
 *  speaks.  Not `coupledApi.thermalSettings()`, which reads what was persisted:
 *  the tab that asked for the run is the tab whose boundary conditions it must
 *  be solved under.  `maxIter` is deliberately not among them — the fallback is
 *  ONE pass and says so with `max_iter`. */
function panelCooling(s: ThermalState): Record<string, string> {
  const KEYS = [
    'coolMode', 'ambientT', 'airSpeed', 'fluid', 'tIn', 'hConv', 'flowLpm',
    'boreMode', 'boreAirSpeed', 'boreFluid', 'boreTIn', 'boreFlowLpm',
    'shaftExtMm', 'shaftExtSides', 'frame', 'openAirSpeed',
    // …and the robotics block (2026-09-14).  `mountT` is included and may be
    // BLANK: the loop below drops empty strings, and an absent `mountT` is what
    // `cooling_fields` reads as "the ambient".
    'emissivity', 'mountG', 'mountT', 'endFaces', 'endFaceSides'] as const;
  const out: Record<string, string> = {};
  for (const k of KEYS) {
    const v = s[k];
    if (v === null || v === undefined || v === '') continue;
    out[k] = String(v);
  }
  return out;
}

/** The cooling THIS panel is showing, for anything that has to send
 *  `thermal_settings` — the duty-cycle solve, which fits its network to one
 *  steady map and must use the boundary conditions the user is LOOKING at, not
 *  the ones some earlier session persisted server-side.  `null` before the
 *  store has adopted the server's settings: until then the backend's copy is
 *  the whole truth and the request sends nothing rather than a half-hydrated
 *  panel (the same rule `registerThermalPanelBlock` below follows). */
export function thermalPanelBlock(): Record<string, string> | null {
  const s = useThermalStore.getState();
  return s.hydrated ? panelCooling(s) : null;
}

/** The Electromagnetic tab's coupled Run reads the cooling from HERE too
 *  (2026-09-09): the same block this tab would send, plus its `maxIter`, and
 *  only once the store has adopted the server's settings — before that the
 *  backend's copy of them is the whole truth and the run sends nothing.  The
 *  old per-key `therm.*` read shipped a `liquid` mode without its flow. */
registerThermalPanelBlock(() => {
  const s = useThermalStore.getState();
  if (!s.hydrated) return null;
  const out = panelCooling(s);
  const it = String(s.maxIter ?? '').trim();
  if (it !== '') out.maxIter = it;
  return out;
});

/**
 * Make the missing Electromagnetic run, and come back with the map.
 *
 * Blocks for the whole loop — a transient plus a conduction solve, minutes — and
 * says so meanwhile through `emFallback`: the panel swaps its progress strip to
 * `/api/coupled/progress` (the thermal bar is silent through the transient,
 * which is most of the wait) and shows a Stop that cancels by run-id.
 *
 * Anything the orchestrator refuses — a single frame, an imposed-voltage drive,
 * the conducting solve off, an rpm or a winding that differs from the shared
 * config, cooling that cannot be solved — comes back as its own sentence and is
 * shown verbatim.  Those are all things the user must fix; none of them is
 * something this tab may decide on its behalf.
 *
 * `at` is the SECOND caller's difference (2026-09-14, the duty-cycle editor).
 * The field Solve asks about the point the Electromagnetic tab is showing, so it
 * passes nothing and the payload is built from that tab's own fields.  A duty
 * cycle's calibration map is solved at ANOTHER DUTY's stored point, so that
 * caller hands it over explicitly and it overrides every field the builder would
 * have read from the Electromagnetic tab (`dutyCycleOffer.emRunBodyAt`).
 */
async function emRunThroughLoop(
  set: (p: Partial<ThermalState>) => void,
  get: () => ThermalState,
  why: string,
  at?: EmRunAt,
): Promise<ThermalField> {
  const runId = `therm-${Date.now().toString(36)}`;
  set({ emFallback: { runId, why, stopping: false, label: at?.label ?? null } });
  try {
    // The operating point the REFUSED request named, pinned onto the run: the
    // loss map is looked up by the current, the angle, the speed, the coil
    // temperature and the frame count, so a run made at anything else is not the
    // run that was missing.  Everything else — the mesh, the temperatures, the
    // winding, the machine — is read where the Electromagnetic tab set it, by
    // the same builder that tab's own Run uses.
    const op = simOperatingPoint();
    const base = buildEmRunPayload(emRunInputsFromSettings({
      I_phase_rms: at ? at.point.I_phase_rms : op.I_phase_rms,
      gamma_deg: at ? at.point.gamma_deg : op.gamma_deg,
      steps: at ? at.n_steps_per_period : op.n_steps_per_period,
      run_id: runId,
    }));
    // `record: false` — THE RUN IS NOT THIS MACHINE'S ANSWER (2026-09-15).
    // Only on the `at` branch, and the difference is the whole flag: the field
    // Solve above runs the point this tab is showing, which IS the loaded
    // duty's, and must keep being filed under it.  A calibration run is made at
    // ANOTHER duty's point (14.7 A, 1 000 rpm, coil 30 °C) while the editor
    // holds this one, and the backend could not tell the two apart — measured
    // on 2026-09-15, the L13 peak duty's `em` and `thermal` field sidecars came
    // back as 14.7 A / 30 °C maps and `/api/thermal/last` as 59 °C instead of
    // 409 °C.  The flag suppresses the FILING only: the run, its transient
    // store and its field snapshot — the thing the duty-cycle route looks the
    // loss map up in — are written exactly as before, which is the whole point
    // of making it.  `for_duty` is the log line's, nothing reads it back.
    const payload = at
      ? { ...emRunBodyAt(base, at), record: false,
          ...(at.label ? { for_duty: at.label } : {}) }
      : base;
    const res = await runCoupled(payload, undefined, {
      // ONE electromagnetic run, then one thermal solve.  Iterating the two to
      // their fixed point is the Electromagnetic tab's coupled switch — not
      // something a Solve press here buys silently.
      maxIter: 1,
      // …and no rotor stress either: this Solve asked for a temperature map.
      mechanical: false,
      thermalSettings: panelCooling(get()),
    });
    // `CoupledRunResult.thermal` is deliberately loose (the orchestrator's
    // client does not own the thermal payload's shape) — this tab does, and the
    // `components` check below is what actually decides it is a map.
    const map = res.thermal as unknown as ThermalField | null;
    if (map && map.components) return map;
    // Not seen in practice: the loop remembers its map as this tab's last
    // `field` result, so an answer that came back without one can still be read
    // back from where it was stored.
    const last = (await fetchLastThermal()).field?.result;
    if (!last) throw new Error('the coupled loop returned no temperature map');
    return last;
  } catch (e) {
    // A cancel is a REQUEST, not a failure: the loop answers a cancelled run
    // with its own 499, and "coupled run stopped" is not what the user who
    // pressed Stop needs to read.
    if (get().emFallback?.stopping) throw new Error('Cancelled.');
    throw e;
  } finally {
    set({ emFallback: null });
  }
}

export const useThermalStore = create<ThermalState>()((set, get) => ({
  hydrated: false,
  field: { ...EMPTY } as Slice<ThermalField>,
  coupled: { ...EMPTY } as Slice<CoupledResult>,
  emFallback: null,

  geom: null,
  geomBusy: false,
  geomErr: null,
  geomStartedAt: null,

  est: {
    field: readLastSecs('field'), coupled: readLastSecs('coupled'),
    mesh: readLastSecs('mesh'),
  },

  coolMode: readMode('coolMode', COOL_MODES, 'air' as CoolMode),
  ambientT: readStr('ambientT', '40'),
  airSpeed: readStr('airSpeed', '10'),
  fluid: readStr('fluid', 'water'),
  tIn: readStr('tIn', '40'),
  hConv: readStr('hConv', '50'),
  // '' was the old default and meant "let the solver derive the flow from the
  // inlet↔outlet ΔT".  There is no outlet input any more, so a blank flow is
  // now simply a missing pump: `readStr` turns it back into the 8 L/min default.
  flowLpm: readStr('flowLpm', '8'),

  boreMode: readMode('boreMode', BORE_MODES, 'none' as BoreMode),
  boreAirSpeed: readStr('boreAirSpeed', '10'),
  boreFluid: readStr('boreFluid', 'water'),
  boreTIn: readStr('boreTIn', '40'),
  boreFlowLpm: readStr('boreFlowLpm', '4'),

  // OFF by default, and it has to be: a shaft length nobody typed would add a
  // heat path nobody designed, and every temperature on this tab would quietly
  // get better.
  shaftExtMm: readStr('shaftExtMm', '0'),
  shaftExtSides: readStr('shaftExtSides', '2'),

  // HOUSED by default, and it has to be: `open` puts the end windings and the
  // slot channels in the airflow, and a machine that was never described that
  // way would quietly get tens of kelvin better.
  frame: readMode('frame', FRAME_MODES, 'housed' as FrameMode),
  openAirSpeed: readStr('openAirSpeed', '0'),

  // ── the robotics block (2026-09-14) ──────────────────────────────────────
  // 0.9 is anodised / painted aluminium and most machine housings; bare
  // polished metal is 0.1 and would remove most of the housing's loss, which is
  // exactly why it is an input and not a constant.
  emissivity: readStr('emissivity', '0.9'),
  // 2 W/K is an ASSUMPTION — nobody has measured this flange yet (plan decision
  // 7) — and the panel says so out loud.  It is not zero, because a joint
  // bolted to nothing is not the machine anybody is designing.
  mountG: readStr('mountG', '2'),
  // BLANK on purpose: the mount sits at the ambient until somebody states
  // otherwise, and pre-filling it would turn that default into a claim.
  // `readStr` would swallow a blank into its default, so it is read raw.
  mountT: (() => {
    const v = readTherm<unknown>('mountT', '');
    return typeof v === 'number' ? String(v) : (typeof v === 'string' ? v : '');
  })(),
  endFaces: readMode('endFaces', END_FACE_MODES, 'still' as EndFaceMode),
  endFaceSides: readStr('endFaceSides', '2'),

  maxIter: readStr('maxIter', '6'),

  // Whatever this browser stacked last time, re-validated: what is in
  // localStorage was written by a previous version of this app.
  compareRows: normalizeLocalRows(readTherm<unknown>('compareRows', [])),

  meshCfg: null,

  view: readTherm('view', 'temp' as ThermView),
  eqTemp: readTherm('eqTemp', true),
  showFlux: readTherm('showFlux', true),

  set: (k, v) => {
    set({ [k]: v } as Pick<ThermalState, typeof k>);
    if (PERSISTED.includes(k)) {
      writeTherm(k as string, v);
      // …and to the SERVER, the memory every browser shares — the same bargain
      // the Electromagnetic tab has with the config (user 2026-09-07: "запоминай все
      // последние настройки … одинаково для всех моделирований").
      const snap: Record<string, unknown> = {};
      const st = get();
      for (const pk of PERSISTED) snap[pk as string] = (st as unknown as Record<string, unknown>)[pk as string];
      snap[k as string] = v;
      savePanelSettings('thermal', snap);
    }
  },

  /**
   * First mount after a reload: adopt whatever the backend still has.
   *
   * Only ever fills EMPTY slices — a result solved in this session always wins
   * over the one on disk, because the disk copy may be the same answer and may
   * be an older one, and silently replacing what is on screen with a file is
   * how the Electromagnetic tab's "backNewer" bug read.
   */
  hydrate: async () => {
    if (get().hydrated) return;
    set({ hydrated: true });
    // The mesh block is read from the server first, so the context line shows
    // what a Solve would actually mesh, not the browser's leftover keys.
    try { set({ meshCfg: await fetchMeshParams() }); } catch { /* shown as local */ }
    // The FIELDS, from the server: what the user last had on this tab in ANY
    // browser, solved or not.  Written straight into state (not via `set`) so
    // the adoption does not echo back as a save.
    try {
      const srv = await loadPanelSettings('thermal');
      const adopted = adoptSettings(get() as unknown as Record<string, unknown>, srv?.settings, PERSISTED as string[]);
      // The stack is a LIST of objects, not a text field: what the server holds
      // is JSON another browser wrote, so it is re-validated before it can
      // reach the table (`adoptSettings` only checks the shape is an array).
      if ('compareRows' in adopted) {
        adopted.compareRows = normalizeLocalRows(adopted.compareRows);
      }
      if (Object.keys(adopted).length) set(adopted as Partial<ThermalState>);
    } catch { /* offline: localStorage already seeded the state */ }
    try {
      const last = await fetchLastThermal();
      const adopt = <T,>(cur: Slice<T>,
                         e: { result: T; geometry_fingerprint: string | null;
                              computed_at: string | null;
                              stale_geometry: boolean | null } | null,
                         tile: (r: T) => T): Slice<T> =>
        (cur.data || !e?.result) ? cur : {
          data: tile(e.result), busy: false, err: null,
          // No stamp of our own: this result was not solved by this client, so
          // the backend's verdict is the only witness there is.
          geoSig: null, backendStale: e.stale_geometry,
          restoredAt: e.computed_at, startedAt: null,
        };
      // A field solved before 2026-09-09 17:09 carries no `n_sectors` (only
      // the mesh preview did), so the tiler would leave it a quarter of a
      // motor.  It was solved on the Mesh tab's own sector count, which is
      // what the tiler borrows here (user: "сделай тепловые поля на весь
      // мотор, а не только на 1/4").
      const tileRestored = <P extends { n_sectors?: number }>(p: P): P => {
        if (!p || (p.n_sectors ?? 0) > 0) return tileFullRing(p as never) as P;
        const mb = readMeshSettings();
        const n = Math.max(1, Math.round(Number(mb.n_sectors ?? mb.nSectors ?? 1) || 1));
        return tileFullRing({ ...p, n_sectors: n, symmetry_mult: n } as never) as P;
      };
      set({
        field: adopt(get().field, last.field, tileRestored),
        coupled: adopt(get().coupled, last.coupled,
                       (r) => ({ ...r, field: tileRestored(r.field) })),
      });
    } catch (e) {
      // A missing /last is not an error the user has to read: the tab simply
      // starts empty and draws the geometry.  Keep it for the console.
      console.warn('thermal: could not read the last result', msg(e));
    }
    if (!get().field.data) await get().loadGeometry();
  },

  refreshLast: async () => {
    // `Date.parse` on both — the stamps arrive in two shapes and a string
    // compare across them is meaningless (see the mechanical store).
    const when = (v: unknown): number => {
      const t = Date.parse(String(v ?? ''));
      return Number.isFinite(t) ? t : NaN;
    };
    try {
      const last = await fetchLastThermal();
      const tileRestored = <P extends { n_sectors?: number }>(p: P): P => {
        if (!p || (p.n_sectors ?? 0) > 0) return tileFullRing(p as never) as P;
        const mb = readMeshSettings();
        const n = Math.max(1, Math.round(Number(mb.n_sectors ?? mb.nSectors ?? 1) || 1));
        return tileFullRing({ ...p, n_sectors: n, symmetry_mult: n } as never) as P;
      };
      const adopt = <T,>(cur: Slice<T>,
                         e: { result: T; geometry_fingerprint: string | null;
                              computed_at: string | null;
                              stale_geometry: boolean | null } | null,
                         tile: (r: T) => T): Slice<T> => {
        if (!e?.result || cur.busy) return cur;
        const mine = when((cur.data as { computed_at?: string } | null)?.computed_at
                          ?? cur.restoredAt);
        const theirs = when(e.computed_at);
        if (!Number.isNaN(mine) && !(theirs > mine)) return cur;
        return {
          data: tile(e.result), busy: false, err: null,
          geoSig: null, backendStale: e.stale_geometry,
          restoredAt: e.computed_at, startedAt: null,
        };
      };
      set({
        field: adopt(get().field, last.field, tileRestored),
        coupled: adopt(get().coupled, last.coupled,
                       (r) => ({ ...r, field: tileRestored(r.field) })),
      });
    } catch (e) {
      console.warn('thermal: could not refresh the last result', msg(e));
    }
  },

  loadGeometry: async () => {
    // A build already running is not re-entered — unless its flag is STALE
    // (a request the API restart cut off never resolves, and a flag stuck
    // true would disable the button until a reload: 'на кнопку Build mesh
    // невозможно нажать', 2026-09-07).
    if (get().geomBusy && Date.now() - (get().geomStartedAt ?? 0) < 180_000) return;
    const t0 = Date.now();
    set({ geomBusy: true, geomErr: null, geomStartedAt: t0 });
    try {
      // Tiled here, once: a sector mesh drawn as it comes back is a quarter of
      // a motor on an otherwise empty tab.
      const mesh = await fetchMeshParams();
      set({ meshCfg: mesh });
      const m = tileFullRing(await fetchThermalMesh(mesh));
      set({ geom: m });
      noteSecs(set, get, 'mesh',
               // What it COST to build: a cache hit is not a 0.05 s mesh, and
               // an estimate taught by cache hits would say "~0 s" forever.
               (m.mesh_s ?? 0) > 0 ? m.mesh_s : ((Date.now() - t0) / 1000));
    } catch (e) {
      set({ geomErr: msg(e) });
    } finally {
      set({ geomBusy: false, geomStartedAt: null });
    }
  },

  solveField: async () => {
    const s = get();
    // Never send a boundary condition that cannot be solved (a liquid loop with
    // no pump, no cooled surface at all): the panel disables Solve on the same
    // verdict, and this is the second lock on the same door.
    const bad = coolingIssue(s);
    if (bad) { set({ field: { ...s.field, busy: false, err: bad } }); return; }
    set({ field: { ...s.field, busy: true, err: null, startedAt: Date.now() } });
    /** the refusal's sentence, when the answer was "no Electromagnetic run" */
    let why: string | null = null;
    try {
      const mesh = await fetchMeshParams();
      set({ meshCfg: mesh });
      const out = tileFullRing(await fetchThermalField(buildRequest(s, mesh)));
      set({ field: { data: out, busy: false, err: null, geoSig: liveGeoSig(),
                     backendStale: false, restoredAt: null, startedAt: null } });
      if (!out.cached) noteSecs(set, get, 'field', out.elapsed_s ?? out.solve_time_s);
      return;
    } catch (e) {
      // The ONE refusal this tab can answer by itself: there is no
      // Electromagnetic run of this machine at this point, so make it — through
      // the orchestrator.  Every other error is the user's to read.
      if (!isNoEmRun(e)) {
        set({ field: { ...EMPTY, err: msg(e) } as Slice<ThermalField> });
        return;
      }
      why = msg(e);
    }
    // The loop's own thermal half solved this tab's cooling at the run it just
    // made, so its map IS the answer this Solve asked for — shown exactly as a
    // plain Solve's, and stamped the same way, because it is just as fresh.
    try {
      const out = tileFullRing(await emRunThroughLoop(set, get, why));
      set({ field: { data: out, busy: false, err: null, geoSig: liveGeoSig(),
                     backendStale: false, restoredAt: null, startedAt: null } });
      if (!out.cached) noteSecs(set, get, 'field', out.elapsed_s ?? out.solve_time_s);
    } catch (e) {
      set({ field: { ...EMPTY, err: msg(e) } as Slice<ThermalField> });
    }
  },

  /** The EM↔thermal fixed point: losses heat the copper, hotter copper is more
   *  resistive, more resistive copper loses more.  Each iteration is a full EM
   *  solve plus a conduction solve, which is why the cap is an input and the
   *  run is a deliberate press. */
  solveCoupled: async () => {
    const s = get();
    const bad = coolingIssue(s);
    if (bad) { set({ coupled: { ...s.coupled, busy: false, err: bad } }); return; }
    set({ coupled: { ...s.coupled, busy: true, err: null, startedAt: Date.now() } });
    const ask = async (): Promise<CoupledResult> => {
      const mesh = await fetchMeshParams();
      set({ meshCfg: mesh });
      const raw = await fetchCoupled({
        ...buildRequest(s, mesh),
        // 1…12 on the backend; anything outside that is the field's typo, not a
        // request, so it falls back to the API's own default rather than 422.
        max_iter: Math.min(12, Math.max(1, Math.round(num(s.maxIter, 6)))),
      });
      return { ...raw, field: tileFullRing(raw.field) };
    };
    try {
      let out: CoupledResult;
      try {
        out = await ask();
      } catch (e) {
        if (!isNoEmRun(e)) throw e;
        // This loop reuses ONE loss map and rescales the copper between passes
        // (`em_solves: 0`), so it needs the same Electromagnetic run the field
        // Solve does and refuses in the same words.  Make it the same way, then
        // ask again — the button keeps its own physics, it just stops being
        // blocked on a run the user had to go and make by hand.
        const mapped = tileFullRing(await emRunThroughLoop(set, get, msg(e)));
        // The loop stored that map as this tab's last `field` result, so adopt
        // it here too: a panel whose picture disagrees with `/last` is a panel
        // that will disagree with itself after the next reload.
        set({ field: { data: mapped, busy: false, err: null, geoSig: liveGeoSig(),
                       backendStale: false, restoredAt: null, startedAt: null } });
        out = await ask();
      }
      set({ coupled: { data: out, busy: false, err: null, geoSig: liveGeoSig(),
                       backendStale: false, restoredAt: null, startedAt: null } });
      if (!out.cached) noteSecs(set, get, 'coupled', out.elapsed_s ?? out.solve_time_s);
    } catch (e) {
      set({ coupled: { ...EMPTY, err: msg(e) } as Slice<CoupledResult> });
    }
  },

  emRunAtPoint: async (at, why) => {
    // No `field` write, no `noteSecs`, no adopted temperatures: this run is not
    // an answer to anything on this tab.  The only state it touches is
    // `emFallback`, which is what the strip and the Stop button read.
    try {
      await emRunThroughLoop(set, get, why, at);
      return null;
    } catch (e) {
      return msg(e);
    }
  },

  stopEmFallback: () => {
    const f = get().emFallback;
    if (!f || f.stopping) return;
    // By run-id, exactly as the Electromagnetic tab's Stop does: the cancel sets
    // both the loop's registry and the transient's, so it is honoured by the
    // frame march instead of waiting six minutes for it to finish.
    cancelCoupled(f.runId);
    set({ emFallback: { ...f, stopping: true } });
  },
}));
