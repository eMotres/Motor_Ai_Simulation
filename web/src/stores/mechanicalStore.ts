/**
 * mechanicalStore — everything the Mechanical tab is showing, kept OUTSIDE the
 * panel's component tree.
 *
 * User 2026-09-06: "когда я захожу и выхожу в Mechanical, графики пропадают.
 * Нужно, чтобы по умолчанию: если нет расчётов — рисуется просто геометрия;
 * если есть — подгружается последний расчёт; если были изменения текущей
 * геометрии — нужно подсвечивать неактуальность текущего расчёта."
 *
 * The Mechanical tab is NOT `keepMounted` in App.tsx (it owns its own picture,
 * so it does not take the AppBar's viewer cluster), which means leaving the tab
 * unmounts the panel and `useState` results went with it — a 30-second contact
 * solve thrown away by clicking "Electromagnetic".  So the results and the toolbar
 * choices live here instead, and the panel becomes a view of this store.
 *
 * Three layers of survival, deliberately:
 *
 *   1. TAB SWITCH   — this store (in memory, module scope).  Exact restoration,
 *                     no round trip, including a solve still in flight.
 *   2. PAGE RELOAD / API RESTART — the backend's `/api/mechanical/last`, which
 *                     persists the last answers beside the config the same way
 *                     the Electromagnetic tab's last transient is persisted.  Fetched
 *                     once, on the first mount that finds this store empty.
 *   3. THE SMALL CHOICES — localStorage under `mech.*`, as the panel already
 *                     did for the modal inputs.  A result is too big and too
 *                     machine-specific for localStorage; a checkbox is not.
 *
 * NOTHING here solves on its own.  `hydrate()` reads, the three `solve*`
 * actions run only when a button is pressed — the tab's standing rule since it
 * was written (the operating point comes from the Electromagnetic tab, and a solve is
 * a deliberate press).
 */
import { create } from 'zustand';

import { liveGeoSig } from '../components/common/geoSig';
import { normalizeLocalRows } from '../components/compare/resultRows';
import type { LocalRow } from '../components/compare/resultRows';
import { adoptSettings, loadPanelSettings, savePanelSettings } from '../lib/panelSettings';
import { mechTempParams, partTempsFromComponents } from '../lib/mechThermalTemps';
import type { MechThermalTemps } from '../lib/mechThermalTemps';
import { fetchLastThermalLight } from '../components/thermal/api';
import type { SymmetryMode } from '../components/mechanical/api';
import {
  DEFAULT_BEAM, DEFAULT_CONTACTS, REF_TEMP_C, coupledPoint, fetchCriticalSpeeds,
  fetchLastMechanical, fetchMechMesh, fetchModes, fetchRotorStress,
  readLastSecs, readMech, readSimSetting, readSimTorqueNm, writeLastSecs,
  writeMech,
} from '../components/mechanical/api';
import type {
  BeamInputs, CaseName, CasesMode, ContactPair, ContactSpec, LoadsMode,
  MechKind, MechMeshPayload, ModalBody, ModalResult, ModalSupport, RotorStress,
  RotordynamicsResult,
} from '../components/mechanical/api';
import type { MechView } from '../components/mechanical/fieldAdapters';

const msg = (e: unknown): string => (e instanceof Error ? e.message : String(e));

/** The "Shaft & bearings" pickers, as the PANEL keeps them.
 *
 *  This is the working copy, not the machine's: the pair only becomes the
 *  machine's when "Save to machine" is pressed (PATCH
 *  /api/family/config/{die}/{cfg}/bearings, the route the battery uses).  Kept
 *  here so it persists like every other field on this tab — server-side, so it
 *  survives a reload and follows the user between browsers. */
export interface BearingPanel {
  /** Library card names (config/bearings_library.yaml); '' = not picked. */
  A: string;
  /** '' = symmetric shaft line, i.e. the same card as A. */
  B: string;
  lubrication: 'grease' | 'oil_air';
  preloadN: string;
  tempSource: 'manual' | 'thermal';
  tempC: string;
}

const DEFAULT_BRG: BearingPanel = {
  A: '', B: '', lubrication: 'grease', preloadN: '0',
  tempSource: 'manual', tempC: '70',
};

/**
 * Was `result` produced by a different machine than the one loaded now?
 *
 * TWO independent witnesses, the same pair `TransientCharts` uses for the last
 * transient, because the restore path has two mouths:
 *
 *   • `geoSig`  — this client's own stamp, taken at solve time from the store
 *                 geometry.  Catches a geometry edit made AFTER the result was
 *                 fetched, with no round trip.
 *   • `backend` — `/last`'s own verdict, from the server's `_geometry_
 *                 fingerprint` of the machine it solved vs the live one.
 *                 Survives a reload, where this client has no stamp of its own.
 *
 * Either one saying "different motor" is enough; neither knowing anything is
 * UNKNOWN, reported as not-stale — a check that cannot prove a mismatch must
 * never claim one (the same rule `geoSig.geoStaleAgainstLive` is written to).
 */
export function isStale(stamp: string | null, backend: boolean | null): boolean {
  if (backend === true) return true;
  const live = liveGeoSig();
  if (!stamp || !live) return false;
  return stamp !== live;
}

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
  /** `Date.now()` when this solve was started, for the ticking timer.
   *  Client-side ON PURPOSE: the point of the clock is that it moves while the
   *  request is still in flight and the backend has said nothing yet.  The
   *  number that ends up on screen afterwards is the backend's `elapsed_s`
   *  (user 2026-09-06: "нужно добавить ещё индикатор времени расчёта"). */
  startedAt: number | null;
}

const EMPTY: Slice<never> = {
  data: null, busy: false, err: null, geoSig: null,
  backendStale: null, restoredAt: null, startedAt: null,
};

export interface MechanicalState {
  /** `/last` has been consulted once — a second mount must not re-ask, or a
   *  restored-then-cleared result would come back on every tab switch. */
  hydrated: boolean;

  stress: Slice<RotorStress>;
  modal: Slice<ModalResult>;
  rotordyn: Slice<RotordynamicsResult>;

  /** the mesh itself: the bare cross-section drawn when nothing has been
   *  solved, and what the Build mesh button (2026-09-06) produces */
  geom: MechMeshPayload | null;
  geomBusy: boolean;
  geomErr: string | null;
  geomStartedAt: number | null;

  /** last measured seconds per kind of solve — the estimate the timer shows
   *  while the next one runs.  Mirrors localStorage `mech.lastSecs.<kind>`. */
  est: Record<MechKind, number | null>;

  // ── rotor-stress inputs (strings: they are text fields) ────────────────────
  /** how many load cases a Solve computes.
   *
   *  User 2026-09-06: "давай будем рассчитывать только на 23 000 оборотов — всё,
   *  что ниже, всяко выдержит, и проще будет считать только одну величину".  So
   *  the tab defaults to ONE case; the three-case table is still one click away
   *  (the API default stays `three`, this is the panel's choice). */
  cases: CasesMode;
  /** which forces a Solve applies.
   *
   *  User 2026-09-07: "добавь ещё и момент на ротор, пусть действуют все силы;
   *  сделай меню, чтобы можно было выбрать центробежную, момент и обе." */
  loads: LoadsMode;
  /** electromagnetic torque, N·m, as text (it is a field).
   *
   *  Seeded from the last transient the Electromagnetic tab ran — the operating
   *  point comes from there, never from a number typed into this panel — and
   *  editable, because "what if it were 250" is the whole point of the field.
   *  EMPTY means "let the backend take the last run's mean torque", which is
   *  what a fresh browser with no `sim.lastTransient` does. */
  torque: string;
  rpm: string;
  /** the single-speed proof rpm.  Its DEFAULT is the Electromagnetic tab's rpm × 1.15
   *  — the operating point still comes from there (standing rule); the ×1.15 is
   *  a mechanical proof margin, and it is editable like the overspeed factor
   *  beside it. */
  rpm1: string;
  osf: string;
  interf: string;
  /** rotor core / magnet / shaft temperature, °C, as text (it is a field).
   *
   *  User 2026-09-07: "нужно универсально добавить температуру ротора, чтобы
   *  можно было задавать; для моторов без бандажа этот эффект вообще
   *  минимальный".  20 is the REFERENCE — the machine as drawn, no thermal
   *  load — and it is the default because a temperature is a duty point the
   *  user chooses, not something this panel may invent (the Electromagnetic tab
   *  carries a COIL temperature, which is the winding, not the rotor). */
  rotorTempC: string;
  /** sleeve temperature, °C. Its own field because the band sits in the gap
   *  draught while the iron carries the loss, and the DIFFERENCE between the
   *  two is what moves the fit. */
  sleeveTempC: string;
  /** Where the temperatures come from (user 2026-09-07: "если есть уже
   *  термоанализ, можно брать температуру из него, а можно по умолчанию"):
   *  'manual' = the two fields above; 'thermal' = the LAST Thermal-tab result,
   *  ONE TEMPERATURE PER PART, when that result is for the machine on screen.
   *  With no fresh thermal result the fields are used and the panel says so. */
  tempSource: 'manual' | 'thermal';
  /** What the Thermal tab last solved, per part — read on mount and on demand.
   *
   *  User 2026-09-08: "в механический расчёт тоже нужно делать каплинг, чтобы
   *  температуры везде были одинаковы".  It used to be the rotor and sleeve
   *  MAXIMA only, with the rotor's number stretched over the core, the magnets
   *  AND the shaft — two numbers standing in for four parts that are not the
   *  same on a real machine.  Now every solid carries its own mean (what a
   *  Solve sends) and its own peak (what the tooltip shows); see
   *  `lib/mechThermalTemps` for why the mean is the one that goes in. */
  thermalTemps: MechThermalTemps | null;
  refreshThermalTemps: () => Promise<void>;
  /** ONE mesh size for the whole tab: the stress solve, the modal solve and the
   *  Build mesh button all use it.  User 2026-09-06 asked for the mesh to be a
   *  thing you control — two boxes called "mesh mm" that quietly disagreed were
   *  the opposite of that, and a shared size is also what lets the backend's
   *  mesh memo serve the solve with what Build mesh already built. */
  meshMm: string;
  /** `full` rotor or one periodic pole `sector` (2026-09-09, user: "используй
   *  периодичность, как я во Fusion") — persisted with the panel, so the
   *  coupled loop's mechanical step solves the same way the button does. */
  symmetry: SymmetryMode;
  contacts: Record<ContactPair, ContactSpec>;

  /** The tab's OWN comparison stack — the Configure tab's "Saved
   *  configurations", for rotor variants (user 2026-09-07: "сделай локальное
   *  сравнение … так же сделай в механике").  A PERSISTED field like any
   *  other, so the stack survives a reload and is the same in every browser;
   *  the permanent library is still the Compare tab. */
  compareRows: LocalRow[];

  // ── rotor-stress view choices ─────────────────────────────────────────────
  viewCase: CaseName;
  view: MechView;
  exagg: string;
  showContacts: boolean;
  sfLow: string;
  sfHigh: string;

  // ── modal inputs ──────────────────────────────────────────────────────────
  body: ModalBody;
  support: ModalSupport;
  nModes: string;
  modalExagg: string;
  modalSel: number;

  // ── shaft line ────────────────────────────────────────────────────────────
  beam: Record<string, string>;

  // ── shaft & bearings ──────────────────────────────────────────────────────
  /** The bearing pickers' working copy — see `BearingPanel`. */
  brg: BearingPanel;
  /** The stiffness this panel last auto-filled into `beam.bearing_k_n_per_m`
   *  from the picked card.  A value WE wrote is ours to replace when the card
   *  changes; a number the user typed is theirs and survives (the same bargain
   *  the battery's V_bus prefill keeps — see `busIsPrefill` in
   *  components/simulation). null = the panel has never filled it. */
  brgKSeed: string | null;

  set: <K extends keyof MechanicalState>(k: K, v: MechanicalState[K]) => void;
  setContact: (pair: ContactPair, patch: Partial<ContactSpec>) => void;
  setBeam: (key: string, v: string) => void;

  hydrate: () => Promise<void>;
  /** Adopt the backend's last result when it is NEWER than what is on screen.
   *
   *  `hydrate` runs once per store lifetime, which is once per SPA session, so
   *  a result solved by somebody else after that first visit never reached this
   *  tab: the coupled loop solves the rotor stress at its converged
   *  temperatures and files it as the last mechanical result, and the user came
   *  back to the tab to find the run before it (2026-09-10: "в механике был
   *  предыдущий режим 22900, когда я туда зашёл", and with it the torque and
   *  the rotor temperature of that older run).  Cheap — one GET — and it never
   *  touches a slice that is mid-solve or newer than the server's. */
  refreshLast: () => Promise<void>;
  /** Build (or re-fetch) the rotor mesh at the current size.  NEVER called on a
   *  field edit — each build is seconds of gmsh, so it is a button. */
  loadGeometry: () => Promise<void>;
  /** `hasSleeve` comes from the panel, which is the one that reads the live
   *  geometry: a machine with `sleeve_thickness = 0` has no fit to specify and
   *  the backend rejects a non-zero interference on it with a 422.  The field is
   *  disabled in the UI, but its remembered value survives a switch to a
   *  sleeveless machine — sending it would turn that switch into an error. */
  solveStress: (hasSleeve: boolean) => Promise<void>;
  solveModes: (rpm: number) => Promise<void>;
  solveCriticals: (rpm: number) => Promise<void>;
}

/** The small choices persist under `mech.*` — the panel already did this for
 *  the modal inputs, and the operating point is NOT one of them (it always
 *  comes from the Electromagnetic tab). */
const PERSISTED: (keyof MechanicalState)[] = [
  'cases', 'loads', 'torque', 'rpm',
  'rpm1', 'osf', 'interf', 'rotorTempC', 'sleeveTempC', 'tempSource',
  'meshMm', 'symmetry', 'contacts', 'viewCase', 'view', 'exagg',
  'showContacts', 'sfLow', 'sfHigh', 'body', 'support', 'nModes',
  'modalExagg', 'beam',
  // The bearing pickers ride the same path: one way this tab remembers things.
  'brg', 'brgKSeed',
  // The local comparison stack rides the SAME path as every other remembered
  // field (localStorage + the server), so there is one way this tab remembers
  // things rather than two.
  'compareRows',
];

/** Remember how long this kind of solve took, so the NEXT one can say "~50 s".
 *
 *  Written both to the store (the timer re-renders) and to localStorage (the
 *  estimate is there after a reload, before anything has been solved this
 *  session).  Only a real solve teaches it — a cache hit took no time, and an
 *  estimate taught by cache hits would promise an instant answer forever. */
function noteSecs(set: (p: Partial<MechanicalState>) => void,
                  get: () => MechanicalState,
                  kind: MechKind, secs: number | null | undefined): void {
  if (secs === null || secs === undefined || !Number.isFinite(secs) || secs <= 0) return;
  writeLastSecs(kind, secs);
  set({ est: { ...get().est, [kind]: Math.round(secs * 10) / 10 } });
}

function initialBeam(): Record<string, string> {
  const saved = readMech<Partial<Record<string, string>>>('beam', {});
  const out: Record<string, string> = {};
  (Object.keys(DEFAULT_BEAM) as (keyof BeamInputs)[]).forEach((k) => {
    out[k] = saved[k] ?? String(DEFAULT_BEAM[k]);
  });
  return out;
}

/** The speed a single-case Solve runs at: the box when a number was typed
 *  (a deliberate proof speed), else the Electromagnetic tab's rpm — the
 *  operating point always comes from there (2026-09-09).  0 when neither
 *  exists, which the backend refuses by name. */
export function effectiveProofRpm(rpm1: string): number {
  const typed = Number(String(rpm1 ?? '').trim());
  if (String(rpm1 ?? '').trim() !== '' && Number.isFinite(typed) && typed > 0) return typed;
  const sim = Number(readSimSetting('rpm', 0));
  return Number.isFinite(sim) && sim > 0 ? sim : 0;
}

export const useMechanicalStore = create<MechanicalState>()((set, get) => ({
  hydrated: false,
  stress: { ...EMPTY } as Slice<RotorStress>,
  modal: { ...EMPTY } as Slice<ModalResult>,
  rotordyn: { ...EMPTY } as Slice<RotordynamicsResult>,

  geom: null,
  geomBusy: false,
  geomErr: null,
  geomStartedAt: null,

  est: {
    stress: readLastSecs('stress'), modal: readLastSecs('modal'),
    rotordyn: readLastSecs('rotordyn'), mesh: readLastSecs('mesh'),
  },

  cases: readMech('cases', 'single' as CasesMode),
  // "пусть действуют все силы" — both is the default, here as on the API.
  loads: readMech('loads', 'both' as LoadsMode),
  // Never a hard-coded torque: unset, it is the last Electromagnetic run's |T_avg|,
  // and empty when nothing has been run (the backend then resolves it).
  torque: readMech('torque', String(
    readSimTorqueNm() === null ? '' : Math.round(readSimTorqueNm()! * 10) / 10)),
  rpm: String(readSimSetting('rpm', 0) || ''),
  // BLANK means "the Electromagnetic tab's rpm" (user 2026-09-09: "надо писать
  // всегда расчётные rpm") — `effectiveProofRpm()` below resolves it at solve
  // time and the panel shows the resolved number in the box.  It used to seed
  // ×1.15 once and then keep whatever was typed, which left the Ø200's 23 000
  // on the 40 mm and an empty box on a fresh machine.  A typed value is a
  // deliberate proof speed and stays put.
  rpm1: readMech('rpm1', ''),
  osf: readMech('osf', '1.2'),
  interf: readMech('interf', '0'),
  // 20 °C = the reference: the machine exactly as it has always been solved.
  // Nothing is seeded from the Electromagnetic tab, because what that tab carries is
  // the COIL temperature — the winding, not the rotor — and putting a winding
  // number into a rotor field would be a made-up duty point.
  // Worst case by decision (user 2026-09-07: "давай вести расчёты ротора при
  // температуре 150 градусов, и магниты тоже на 150 — это будет худшим
  // вариантом"): rotor 150 °C, and the sleeve at 150 °C too, so a cold
  // browser never quietly reports a cold rotor.  20 °C (= no thermal load) is
  // one keystroke away in the field.
  rotorTempC: readMech('rotorTempC', '150'),
  sleeveTempC: readMech('sleeveTempC', '150'),
  tempSource: readMech('tempSource', 'manual' as 'manual' | 'thermal'),
  thermalTemps: null,

  refreshThermalTemps: async () => {
    try {
      const last = await fetchLastThermalLight();
      const e = last?.field;
      // The four rotor solids, mean AND peak.  The reading of that payload is
      // in `lib/mechThermalTemps` — pure, and pinned by a node test — so the
      // request builder below and the line the panel prints cannot disagree
      // about what was taken from where.
      set({
        thermalTemps: e
          ? partTempsFromComponents(
              (e.result?.components ?? {}) as Record<string, { max?: number; avg?: number } | null>,
              e.computed_at, e.stale_geometry)
          : null,
      });
    } catch { set({ thermalTemps: null }); }
  },
  // The old `mech.modal.mesh` is read as the fallback so a user who only ever
  // set the modal box keeps their size when the two fields became one.
  meshMm: readMech('meshMm', readMech('modal.mesh', '1.5')),
  symmetry: (readMech<string>('symmetry', 'full') === 'sector' ? 'sector' : 'full'),
  contacts: { ...DEFAULT_CONTACTS, ...readMech('contacts', {}) },

  // Whatever this browser stacked last time, re-validated: what is in
  // localStorage was written by a previous version of this app.
  compareRows: normalizeLocalRows(readMech<unknown>('compareRows', [])),

  viewCase: readMech('viewCase', 'rated' as CaseName),
  view: readMech('view', 'vm' as MechView),
  exagg: readMech('exagg', 'auto'),
  showContacts: readMech('showContacts', true),
  // EMPTY = auto (user 2026-09-09: "давай это делать автоматом") — the map
  // chooses the bands from the field, see components/mechanical/fieldAdapters.
  // The one-time migration below is for the pair nobody ever typed: '2'/'4'
  // was the default this app shipped, so a browser carrying exactly those two
  // was never asked; anything else is a real choice and is left alone.
  ...(() => {
    const lo = readMech<string>('sfLow', ''), hi = readMech<string>('sfHigh', '');
    const untouched = lo === '2' && hi === '4';
    if (untouched) { writeMech('sfLow', ''); writeMech('sfHigh', ''); }
    return { sfLow: untouched ? '' : lo, sfHigh: untouched ? '' : hi };
  })(),

  body: readMech('modal.body', 'rotor' as ModalBody),
  support: readMech('modal.support', 'free' as ModalSupport),
  nModes: readMech('modal.n', '12'),
  modalExagg: readMech('modal.exagg', '8'),
  modalSel: 0,

  beam: initialBeam(),

  // Spread over the default so a stored copy written before a field existed
  // still yields a complete panel rather than an undefined picker.
  brg: { ...DEFAULT_BRG, ...readMech<Partial<BearingPanel>>('brg', {}) },
  brgKSeed: readMech<string | null>('brgKSeed', null),

  set: (k, v) => {
    set({ [k]: v } as Pick<MechanicalState, typeof k>);
    // The modal keys keep the `mech.modal.*` names the panel wrote before this
    // store existed, so nobody's saved choices are lost by the move.
    if (PERSISTED.includes(k)) {
      const alias: Partial<Record<string, string>> = {
        body: 'modal.body', support: 'modal.support', nModes: 'modal.n',
        modalExagg: 'modal.exagg',
      };
      writeMech(alias[k as string] ?? (k as string), v);
      // …and to the SERVER, the memory every browser shares (user 2026-09-07:
      // "запоминай все последние настройки … одинаково для всех
      // моделирований").  Debounced in the helper; the last snapshot wins.
      const snap: Record<string, unknown> = {};
      const st = get();
      for (const pk of PERSISTED) snap[pk as string] = (st as unknown as Record<string, unknown>)[pk as string];
      snap[k as string] = v;
      savePanelSettings('mechanical', snap);
    }
  },

  setContact: (pair, patch) => {
    const next = { ...get().contacts, [pair]: { ...get().contacts[pair], ...patch } };
    set({ contacts: next });
    writeMech('contacts', next);
  },

  setBeam: (key, v) => {
    const next = { ...get().beam, [key]: v };
    set({ beam: next });
    writeMech('beam', next);
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
    // The FIELDS first, from the server: what the user last had on this tab in
    // ANY browser, solved or not.  Server first, the browser's copy only when
    // the server has never seen a field.  Written straight into state (not via
    // `set`) so the adoption itself does not echo back as a save.
    try {
      const srv = await loadPanelSettings('mechanical');
      const adopted = adoptSettings(get() as unknown as Record<string, unknown>, srv?.settings, PERSISTED as string[]);
      // The stack is a LIST of objects, not a text field: what the server holds
      // is JSON another browser wrote, so it is re-validated before it can
      // reach the table (`adoptSettings` only checks the shape is an array).
      if ('compareRows' in adopted) {
        adopted.compareRows = normalizeLocalRows(adopted.compareRows);
      }
      if (Object.keys(adopted).length) set(adopted as Partial<MechanicalState>);
    } catch { /* offline: localStorage already seeded the state */ }
    void get().refreshThermalTemps();
    try {
      const last = await fetchLastMechanical();
      const adopt = <T,>(cur: Slice<T>, e: { result: T; geometry_fingerprint: string | null;
                                             computed_at: string | null;
                                             stale_geometry: boolean | null } | null): Slice<T> =>
        (cur.data || !e) ? cur : {
          data: e.result, busy: false, err: null,
          // No stamp of our own: this result was not solved by this client, so
          // the backend's verdict is the only witness there is.
          geoSig: null, backendStale: e.stale_geometry,
          restoredAt: e.computed_at, startedAt: null,
        };
      set({
        stress: adopt(get().stress, last.rotor_stress),
        modal: adopt(get().modal, last.modes),
        rotordyn: adopt(get().rotordyn, last.critical_speeds),
      });
    } catch (e) {
      // A missing /last is not an error the user has to read: the tab simply
      // starts empty and draws the geometry.  Keep it for the console.
      console.warn('mechanical: could not read the last result', msg(e));
    }
    if (!get().stress.data) await get().loadGeometry();
  },

  refreshLast: async () => {
    // `Date.parse` on both, never a string compare: the stamps arrive in two
    // shapes ('…+00:00' from the solve routes, a bare local one from the
    // coupled step) and lexicographic order across the two is meaningless.
    const when = (v: unknown): number => {
      const t = Date.parse(String(v ?? ''));
      return Number.isFinite(t) ? t : NaN;
    };
    try {
      const last = await fetchLastMechanical();
      const adopt = <T,>(cur: Slice<T>, e: { result: T; geometry_fingerprint: string | null;
                                             computed_at: string | null;
                                             stale_geometry: boolean | null } | null): Slice<T> => {
        if (!e || cur.busy) return cur;
        const mine = when((cur.data as { computed_at?: string } | null)?.computed_at
                          ?? cur.restoredAt);
        const theirs = when(e.computed_at);
        // Adopt when the server is strictly newer, and when what is on screen
        // carries no readable stamp at all (nothing to compare = nothing to
        // defend).  Never when the two are the same answer.
        if (!Number.isNaN(mine) && !(theirs > mine)) return cur;
        return {
          data: e.result, busy: false, err: null,
          geoSig: null, backendStale: e.stale_geometry,
          restoredAt: e.computed_at, startedAt: null,
        };
      };
      const prevStress = get().stress;
      const nextStress = adopt(prevStress, last.rotor_stress);
      set({
        stress: nextStress,
        modal: adopt(get().modal, last.modes),
        rotordyn: adopt(get().rotordyn, last.critical_speeds),
      });
      // THE BOXES FOLLOW THE ANSWER.  After a coupled run everything is
      // supposed to agree — that is what the loop is for (user 2026-09-10:
      // "по-хорошему после каплинга всё должно совпадать") — and a panel whose
      // speed, torque and rotor temperature still describe the run BEFORE it
      // contradicts the very result printed under them.  Only when a newer
      // result was actually adopted, and only the fields that result names.
      const r = nextStress.data;
      if (r && nextStress !== prevStress) {
        const pt = r.thermal?.part_temps_c;
        const patch: Partial<MechanicalState> = {};
        if (Number.isFinite(r.rpm)) {
          patch.rpm = String(Math.round(r.rpm));
          patch.rpm1 = String(Math.round(r.rpm));
        }
        if (r.torque_nm != null && Number.isFinite(r.torque_nm)) {
          patch.torque = String(Number(r.torque_nm.toFixed(3)));
        }
        if (pt) {
          patch.rotorTempC = String(Number(pt.rotor_core.toFixed(1)));
          patch.sleeveTempC = String(Number(pt.sleeve.toFixed(1)));
        }
        if (Object.keys(patch).length) set(patch);
      }
    } catch (e) {
      console.warn('mechanical: could not refresh the last result', msg(e));
    }
  },

  /** The mesh: what an unsolved tab draws, and what Build mesh (re)builds.
   *
   *  Deliberately NOT called when the mesh-size field changes — user 2026-09-06
   *  wanted control over the mesh, and a mesher that re-runs on every keystroke
   *  of "1.25" is three gmsh passes nobody asked for.  The solve that follows
   *  reuses this mesh (the backend memoises it on the geometry), so pressing
   *  Build mesh costs the seconds once, not twice. */
  loadGeometry: async () => {
    if (get().geomBusy) return;
    const t0 = Date.now();
    set({ geomBusy: true, geomErr: null, geomStartedAt: t0 });
    try {
      const m = await fetchMechMesh(Number(get().meshMm) || 1.5);
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

  solveStress: async (hasSleeve) => {
    const s = get();
    set({ stress: { ...s.stress, busy: true, err: null, startedAt: Date.now() } });
    try {
      const single = s.cases === 'single';
      // An empty (or unparseable) torque field is NOT sent: the backend then
      // resolves the last run's mean torque itself, which is the honest default
      // — a 0 would be a torque the user never asked for.
      const tq = Number(s.torque);
      // Coupled thermal ON (2026-09-09): the point is the Electromagnetic run's
      // — its rpm and its torque — exactly what the loop's own mechanical
      // step solves at; the boxes are shown locked to it on the panel.
      const cp = coupledPoint();
      const out = await fetchRotorStress({
        cases: s.cases,
        loads: s.loads,
        torque_nm: cp.torque !== null ? cp.torque
          : (s.torque.trim() !== '' && Number.isFinite(tq) && tq !== 0)
            ? tq : undefined,
        // In single-speed mode the ONE speed solved is the proof rpm, so that is
        // what `rpm` means to the backend; the overspeed factor is not used
        // (the backend forces it to 1 so nothing quotes an unsolved speed).
        rpm: cp.rpm !== null ? cp.rpm
          : (single ? effectiveProofRpm(s.rpm1) : (Number(s.rpm) || effectiveProofRpm(''))),
        overspeed_factor: Number(s.osf) || 1.2,
        interference_mm: hasSleeve ? (Number(s.interf) || 0) : 0,
        // THE temperatures, one per solid when the Thermal tab has them and
        // that answer is FRESH for this machine; the two manual fields
        // otherwise, in which case not one new query parameter is sent and the
        // request is byte for byte the one this app has always made.  An EMPTY
        // or unparseable field falls back to the REFERENCE, never to NaN and
        // never to `Number('') === 0`: 20 °C is "no thermal load", which is the
        // honest reading of an empty box; 0 °C would be a −20 K load nobody
        // asked for.  The whole rule lives in `lib/mechThermalTemps`.
        // NO BAND → NO THERMAL LOAD (user 2026-09-09: "температура в роторе
        // важна только для бандажа"): the iron expands freely and the
        // magnet/iron mismatch is taken up by the epoxy bed, so a sleeveless
        // machine is solved at the reference whatever the fields say — the
        // same rule the coupled loop's mechanical step applies server-side.
        // …always the real ones (user 2026-09-09): what they DO is the
        // solver's rule — a temperature is a load on a retaining band's fit
        // and nowhere else — so a sleeveless rotor gets them too and the
        // answer reports them as "not a load" rather than never seeing them.
        ...mechTempParams(s.tempSource, s.thermalTemps,
                          { rotorTempC: s.rotorTempC, sleeveTempC: s.sleeveTempC },
                          REF_TEMP_C),
        mesh_size_mm: Number(s.meshMm) || 1.5,
        order: 2,
        // `full` is not sent: the request stays byte for byte the one this
        // app has always made unless the user picked the sector.
        ...(s.symmetry === 'sector' ? { symmetry: 'sector' as const } : {}),
        contacts: s.contacts,
      });
      set({ stress: { data: out, busy: false, err: null,
                      geoSig: liveGeoSig(), backendStale: false,
                      restoredAt: null, startedAt: null } });
      // The field was empty and the backend resolved a torque from the last
      // run: adopt it, so the number that was actually applied is on screen and
      // the next Solve reproduces this answer instead of re-resolving.
      if (s.torque.trim() === '' && out.torque_nm) {
        get().set('torque', String(Math.round(out.torque_nm * 10) / 10));
      }
      if (!out.cached) noteSecs(set, get, 'stress', out.elapsed_s ?? out.solve_time_s);
    } catch (e) {
      set({ stress: { ...EMPTY, err: msg(e) } as Slice<RotorStress> });
    }
  },

  solveModes: async (rpm) => {
    const s = get();
    set({ modal: { ...s.modal, busy: true, err: null, startedAt: Date.now() } });
    try {
      const out = await fetchModes({
        body: s.body, support: s.support, n: Number(s.nModes) || 12,
        // the tab's ONE mesh size (2026-09-06) — the modal solve used to carry
        // its own, so the same rotor was meshed twice at two sizes
        mesh_size_mm: Number(s.meshMm) || 1.5, order: 2,
        winding_mass: true, rpm,
      });
      set({ modal: { data: out, busy: false, err: null, geoSig: liveGeoSig(),
                     backendStale: false, restoredAt: null, startedAt: null },
            modalSel: 0 });
      if (!out.cached) noteSecs(set, get, 'modal', out.elapsed_s ?? out.solve_time_s);
    } catch (e) {
      set({ modal: { ...EMPTY, err: msg(e) } as Slice<ModalResult> });
    }
  },

  solveCriticals: async (rpm) => {
    const s = get();
    set({ rotordyn: { ...s.rotordyn, busy: true, err: null,
                      startedAt: Date.now() } });
    // An unparseable field falls back to its default rather than sending NaN:
    // the backend would reject it, but "bearing_span_mm=NaN" is a worse error
    // message than the number the panel is showing.
    const p: BeamInputs = { ...DEFAULT_BEAM };
    const px = p as unknown as Record<string, number>;
    Object.keys(DEFAULT_BEAM).forEach((k) => {
      const v = Number(s.beam[k]);
      if (Number.isFinite(v)) px[k] = v;
    });
    try {
      const out = await fetchCriticalSpeeds({ ...p, rpm, n_modes: 6, mesh_size_mm: 3.0 });
      set({ rotordyn: { data: out, busy: false, err: null, geoSig: liveGeoSig(),
                        backendStale: false, restoredAt: null,
                        startedAt: null } });
      if (!out.cached) noteSecs(set, get, 'rotordyn', out.elapsed_s ?? out.solve_time_s);
    } catch (e) {
      set({ rotordyn: { ...EMPTY, err: msg(e) } as Slice<RotordynamicsResult> });
    }
  },
}));
