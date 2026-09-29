/**
 * DutyCycleEditor — FIND the regime this machine can hold, and show how hot it
 * gets holding it.
 *
 * THE REFRAME (user 2026-09-15): «с помощью Duty cycle мы можем подобрать такой
 * режим работы мотора, чтобы он смог уложиться в температурные лимиты — то есть
 * мы сами находим это время / S3 ED, при котором всё нормально».  This editor
 * used to take a duty ratio and grade it.  It now ANSWERS with one: leave the
 * ED blank and the backend finds the allowable ratio, integrates the cycle AT
 * that ratio and hands back the temperatures there, the same answer over a span
 * of cycle lengths, and how long a single pull lasts from cold, from the rated
 * state and out of the settled cycle.  The typed ratio survives as the optional
 * "check ED %" field, which is the only thing that turns a pass/fail chip on.
 *
 * A steady map answers "how hot is this machine if it runs this point forever".
 * A robot joint never does: it spends two seconds at its peak and a minute at
 * nothing, and the steady answer to the peak is a temperature the machine never
 * reaches.  So the CYCLE is part of the duty's definition — it lives in the yaml
 * with it (`routes/family.DutySpec.duty_cycle`) — and the solve behind this
 * editor fits a four-node lumped network to ONE steady map (the calibration
 * duty's) and integrates the profile on it: `POST /api/thermal/duty_cycle`.
 *
 * NOTHING SOLVES ON MOUNT.  The editor reads three things — the active family
 * context, the configuration's duty list, and whatever cycle this backend last
 * answered — and every one of them is a lookup.  Run is a deliberate press, and
 * it is the only thing here that costs seconds.
 *
 * The block is LOCAL until the duty is saved: typing in this editor writes the
 * per-duty overlay (`lib/dutySettings.noteDutyCycleEdit`), exactly the way the
 * Simulation panel's operating point does, and the duty save sends it (see
 * `ActiveFamilyStrip`).  Nothing here touches the stored duty — the standing
 * no-silent-state rule.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Box, Button, CircularProgress, MenuItem, Paper, Select,
  TextField, Tooltip, Typography,
} from '@mui/material';
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid,
  ReferenceLine, Tooltip as RcTooltip, Legend,
} from 'recharts';

import {
  dutyCycleChip, dutyCycleEdited, dutyCycleFromForm, dutyKey, noteDutyCycleEdit,
  readDutyCycle, setDutyCycleSnapshot, type DutyCycleForm,
} from '../../lib/dutySettings';
import { thermalPanelBlock, useThermalStore } from '../../stores/thermalStore';
import {
  fetchDutyCycle, fetchLastDutyCycle, fmt, fmtSecs, isNoEmRun, meshParams,
  simOperatingPoint,
} from './api';
import type {
  DutyCycleKind, DutyCycleRequest, DutyCycleResult, DutyCycleSpec,
} from './api';
/* The escape hatch out of the ONE refusal this editor can answer — see
   `dutyCycleOffer` for why the point may not come from the Electromagnetic
   tab, and why the run is offered rather than taken. */
import {
  OFFER_IDLE, fetchCalibrationPoint, offerReduce, pointTooltipWords, pointWords,
} from './dutyCycleOffer';
import type {
  CalibrationPoint, OfferAction, OfferState,
} from './dutyCycleOffer';
/* What the shaft is DELIVERING while it heats up — the duties' own torques,
   laid over the same cycle the temperatures are drawn on. */
import {
  missingTorques, torqueByDuty, torqueProfile,
} from './dutyCycleTorque';
/* THE REGIME the tool found — the headline, the ED-vs-cycle-length rows, the
   optional pass/fail chip and the fitted-at guard, all pure (tested by
   `__tests__/dutyCycleRegime.test.mjs`). */
import {
  OFFERED_KINDS, calibrationIssue, checkVerdict, edCycleRows, regimeLine,
  retiredKindNote, runModeLine,
} from './dutyCycleRegime';
/* …and the regime the COUPLED loop found for the same duty (2026-09-16): the
   client is the Electromagnetic tab's, because the answer is that loop's. */
import { fetchCoupledLast, type CoupledRegime }
  from '../simulation/coupledApi';
/* THE MAGNET LIMIT'S DEFAULT (2026-09-16): the card's own maximum working
   temperature, resolved exactly the way the solve resolves the magnet — the
   machine's assignment with this duty's pick laid over it — so the number in
   the placeholder belongs to the magnet that will actually be in the machine. */
import { useMotorAssignments } from '../materials/useMotorAssignments';
import { useMaterialsLibrary } from '../materials/useMaterialsLibrary';
import { activeDutyMaterials } from '../../lib/dutySettings';
import { effectiveAssignment } from '../../lib/dutyMaterials';
/* NO TOOLTIP WRAPS A CONTROL HERE.  A tooltip's popper is drawn above the menu
   a Select opens and takes the pointer, so the kind picker could not be opened
   at all (user 2026-09-15: «всплывающее меню всё закрывает»).  Every hint hangs
   on a ⓘ beside its control, one short line each; the tooltips that remain wrap
   READOUTS and carry `TIP_PROPS`, which keeps them under the menu layer. */
import HelpTip, { CTRL_ROW, TIP_PROPS } from './HelpTip';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

const lbl = { fontSize: 11, color: 'var(--text-3)', whiteSpace: 'nowrap' } as const;
const warn = { ...lbl, color: '#fbbf24', cursor: 'help',
               borderBottom: '1px dotted #fbbf24' } as const;

const AXIS = { fontSize: 10, fill: 'var(--text-2)' };
const GRID = { stroke: 'var(--panel)', strokeDasharray: '2 4' };
const TOOLTIP = {
  contentStyle: { background: 'var(--app-bg)', border: '1px solid var(--line-soft)',
    fontSize: 11, color: 'var(--text-1)' },
  labelFormatter: (v: unknown) => `t = ${Number(v).toFixed(2)} s`,
  formatter: (v: unknown) => `${Number(v).toFixed(1)} °C`,
};
const TQ_TOOLTIP = { ...TOOLTIP,
  formatter: (v: unknown) => `${Number(v).toFixed(2)} N·m` };

/** Both charts share it, so the two y-axes start at the same x. */
const Y_AXIS_W = 58;
/** …and the same hover: one crosshair over temperature and torque. */
const SYNC = 'duty-cycle';

/** The four nodes, in the order an engineer reads them. */
const NODE_INK: [string, string, string][] = [
  ['winding_hot', 'winding hot-spot', '#f87171'],
  ['winding', 'winding mean', '#fb923c'],
  ['stator', 'stator core', '#60a5fa'],
  ['magnet', 'magnets', '#a78bfa'],
  ['rotor', 'rotor + shaft', '#34d399'],
];

/** The kind picker's words.  Only `S1` and `S3` are OFFERED (see
 *  `dutyCycleRegime.OFFERED_KINDS`); the other two stay here because a duty
 *  saved before 2026-09-16 may carry one and it has to be readable. */
const KIND_LABEL: Record<DutyCycleKind, string> = {
  S1: 'S1 — continuous',
  S2: 'S2 — one pull (retired)',
  S3: 'S3 — intermittent (ED % of a cycle)',
  segments: 'Segments — explicit list (retired)',
};

/** The ED-vs-cycle-length chart's own ink — not a node colour, because it is
 *  not a temperature: it is the ANSWER, plotted against the period. */
const ED_INK = '#34d399';
const ED_TOOLTIP = {
  contentStyle: TOOLTIP.contentStyle,
  labelFormatter: (v: unknown) => `cycle ${Number(v).toFixed(0)} s`,
  formatter: (v: unknown) => `${Number(v).toFixed(1)} %`,
};

interface CatalogDuty {
  name: string;
  duty_cycle?: Record<string, unknown> | null;
  /** what this point is FOR, N·m — the torque panel's data (the tree carries
   *  it on every duty entry; `summary` is the fallback a payload may hold) */
  torque_nm?: number | null;
  summary?: Record<string, unknown> | null;
}
interface Ctx { die: string; config: string; duty: string }

/** The stored block back into the editor's text fields.  A number the block
 *  does not state stays BLANK — a blank is "not stated", and pre-filling it
 *  would turn a default into something the user appears to have chosen. */
function formFromBlock(b: Record<string, unknown> | null,
                       fallbackDuty: string): DutyCycleForm {
  const s = (v: unknown): string =>
    (v === null || v === undefined || v === '' ? '' : String(v));
  const kind = String(b?.kind ?? 'S1');
  return {
    kind: (['S1', 'S2', 'S3', 'segments'].includes(kind) ? kind : 'S1'),
    duty: s(b?.duty) || fallbackDuty,
    tOn: s(b?.t_on_s),
    edPct: s(b?.ed_pct),
    cycleS: s(b?.cycle_s),
    restDuty: b && 'rest_duty' in b ? s(b.rest_duty) : '',
    tStartC: s(b?.t_start_c),
    nCyclesMax: s(b?.n_cycles_max),
    calibrationDuty: s(b?.calibration_duty),
    segments: Array.isArray(b?.segments)
      ? (b.segments as Record<string, unknown>[]).map((e) => ({
          duty: s(e?.duty), t_s: s(e?.t_s) }))
      : [{ duty: fallbackDuty, t_s: '2' }, { duty: '', t_s: '8' }],
  };
}

/** WHICH duty the network is calibrated on when the block does not say — the
 *  same rule the backend applies (`routes/thermal._dc_rated_duty`), restated
 *  here only so the picker can SHOW what will happen rather than leaving it
 *  implicit.  The conductances all come from that one point. */
function ratedDuty(duties: CatalogDuty[], fallback: string): string {
  const names = duties.map((d) => d.name).filter(Boolean);
  for (const n of names) if (n.trim().toLowerCase().startsWith('rated')) return n;
  if (fallback && names.includes(fallback)) return fallback;
  return names[0] ?? '';
}

const DutyCycleEditor: React.FC = () => {
  const [ctx, setCtx] = useState<Ctx | null>(null);
  const [duties, setDuties] = useState<CatalogDuty[]>([]);
  const [form, setForm] = useState<DutyCycleForm>({ kind: 'S1' });
  const [edited, setEdited] = useState(false);
  const [magnetLimit, setMagnetLimit] = useState('');
  const [res, setRes] = useState<DutyCycleResult | null>(null);
  const [restoredAt, setRestoredAt] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  /** the regime the COUPLED loop found for this duty, when it has run one */
  const [coupled, setCoupled] = useState<CoupledRegime | null>(null);

  const key = ctx ? dutyKey(ctx.die, ctx.config, ctx.duty) : null;

  /* ── who is loaded, what its duties are, and what cycle it carries ────────
     Three READS.  The tree is the same one the catalog fetches on every render
     and it carries each duty's `duty_cycle` block, so the rest-duty picker and
     the editor's own starting block come from one request. */
  const load = useCallback(async () => {
    let c: Ctx | null = null;
    try {
      const j = await fetch(`${API}/api/family/context`, { cache: 'no-store' })
        .then((r) => r.json());
      if (j?.active && j.die && j.config && j.duty) {
        c = { die: String(j.die), config: String(j.config), duty: String(j.duty) };
      }
    } catch { /* offline — the editor says "no duty loaded" */ }
    setCtx(c);
    if (!c) { setDuties([]); return; }
    let list: CatalogDuty[] = [];
    try {
      const t = await fetch(`${API}/api/family/tree`, { cache: 'no-store' })
        .then((r) => r.json());
      const die = (t?.dies ?? []).find((d: { name?: string }) => d?.name === c!.die);
      const cfg = (die?.configs ?? []).find((x: { name?: string }) => x?.name === c!.config);
      list = (cfg?.duties ?? []) as CatalogDuty[];
    } catch { /* offline — the pickers fall back to this duty alone */ }
    setDuties(list);
    // The yaml's own block becomes this duty's SNAPSHOT layer.  ▶ does this
    // too (lib/dutyLocalApply); doing it here as well is what makes the editor
    // correct after a plain page reload, when no ▶ has been pressed in this
    // browser since the duty was saved elsewhere.
    const stored = (list.find((d) => d.name === c!.duty)?.duty_cycle) ?? null;
    const k = dutyKey(c.die, c.config, c.duty);
    try { setDutyCycleSnapshot(k, stored); } catch { /* quota */ }
    setForm(formFromBlock(readDutyCycle(k), c.duty));
    setEdited(dutyCycleEdited(k));
  }, []);

  useEffect(() => {
    void load();
    // A ▶ elsewhere puts a different duty on the panel; the editor follows it
    // rather than keeping the previous machine's cycle on screen.
    const on = () => { void load(); };
    window.addEventListener('family-changed', on);
    window.addEventListener('sim-settings-restored', on);
    return () => {
      window.removeEventListener('family-changed', on);
      window.removeEventListener('sim-settings-restored', on);
    };
  }, [load]);

  /* ── and what the COUPLED loop found for this duty (2026-09-16) ──────────
     The loop solves an S2/S3 duty for its REGIME now: the electromagnetic run
     and the thermal solve iterate, and inside every pass the allowable duty
     ratio is searched at the temperatures that pass reached.  That answer is
     about the same duty this editor is open on, and it is the one the report
     prints — so it belongs here, on one line, rather than only on the
     Electromagnetic tab's summary card. */
  useEffect(() => {
    if (!ctx) return;
    let dead = false;
    void (async () => {
      const last = await fetchCoupledLast();
      if (dead) return;
      const r = last?.coupling?.duty_cycle ?? null;
      setCoupled(r && (!r.duty || r.duty === ctx.duty) ? r : null);
    })();
    return () => { dead = true; };
  }, [ctx]);

  /* ── whatever this backend last answered, if it is about THIS duty ──────── */
  useEffect(() => {
    if (!ctx) return;
    let dead = false;
    void (async () => {
      try {
        const last = await fetchLastDutyCycle();
        const e = last?.duty_cycle;
        if (dead || !e?.result) return;
        const r = e.result;
        if (r.die !== ctx.die || r.configuration !== ctx.config
            || r.duty !== ctx.duty) return;
        setRes(r);
        setRestoredAt(e.computed_at);
      } catch { /* a tab that has never solved a cycle is the normal state */ }
    })();
    return () => { dead = true; };
  }, [ctx]);

  /** One field changed: the form, and this duty's local overlay with it. */
  const patch = useCallback((p: Partial<DutyCycleForm>) => {
    setForm((f) => {
      const next = { ...f, ...p };
      try {
        noteDutyCycleEdit(key, dutyCycleFromForm(next));
        setEdited(true);
      } catch { /* the overlay is a convenience, never a blocker */ }
      return next;
    });
  }, [key]);

  // The block the catalog stores and the block the solve takes are the same
  // object seen from two sides: `lib/dutySettings` keeps it deliberately loose
  // (the CATALOG owns the schema and validates it by name), while the thermal
  // API types the fields it reads.  The cast is that identity, stated once.
  const blockRaw = useMemo(() => dutyCycleFromForm(form), [form]);
  const block = blockRaw as unknown as DutyCycleSpec;
  const names = useMemo(() => duties.map((d) => d.name).filter(Boolean), [duties]);
  const rated = useMemo(() => ratedDuty(duties, ctx?.duty ?? ''), [duties, ctx]);

  /* ── THE MAGNET'S OWN LIMIT, as the default (2026-09-16) ───────────────────
     The field used to start blank, and a blank draws no line and judges
     nothing: the tool reported an allowable ED that the WINDING could hold
     while the magnets in it went wherever they went.  Every NdFeB card in the
     library states `max_working_temp_c` (it is the number the report judges a
     magnet by, `report._magnet_limit`), so that is the default — as a
     PLACEHOLDER, not as text in the box, because the user did not type it and
     a value that looks typed reads as a choice somebody made.  Typing over it
     wins; clearing the box goes back to the card. */
  const { assignments: liveAssign } = useMotorAssignments();
  const { library: matLib } = useMaterialsLibrary();
  const [dutyMats, setDutyMats] =
    useState<Record<string, string | null>>(() => activeDutyMaterials());
  useEffect(() => {
    // The same events the Electromagnetic tab's magnet badge follows — the
    // duty's pick is localStorage, written by the catalog and the Materials
    // tab, and this field must not lag the magnet the solve will use.
    const EV = ['duty-materials-changed', 'mat-assign-changed',
                'mat-assign-local-changed', 'sim-settings-restored',
                'sim-design-applied', 'family-changed'];
    const sync = () => setDutyMats(activeDutyMaterials());
    for (const ev of EV) window.addEventListener(ev, sync);
    return () => { for (const ev of EV) window.removeEventListener(ev, sync); };
  }, []);
  /** the magnet the solve will use, and its card's maximum working temperature */
  const magnetCardLimit = useMemo((): number | null => {
    const machine = { magnet: liveAssign?.magnet || '' };
    const name = effectiveAssignment(
      machine, dutyMats,
      matLib as unknown as Record<string, Record<string, unknown>>).magnet;
    if (!name) return null;
    const card = (matLib?.magnet as Record<string, Record<string, unknown>>
                  | undefined)?.[name];
    const v = Number(card?.max_working_temp_c);
    return Number.isFinite(v) && v > 0 ? v : null;
  }, [liveAssign, dutyMats, matLib]);

  /** WHICH duty the network is fitted at — what the picker is showing, and the
   *  backend's own default when it shows nothing. */
  const calibDuty = (form.calibrationDuty ?? '').trim() || rated;
  /** …and whether that is a map worth fitting to.  An impulse point's steady
   *  map is a temperature the machine never reaches, so it is refused HERE
   *  rather than sent and explained afterwards. */
  const calibIssue = useMemo(
    () => calibrationIssue(form.calibrationDuty, rated), [form.calibrationDuty, rated]);

  /** What this editor will NOT send, said before the backend has to refuse it.
   *
   *  A BLANK ED is no longer one of them: it is the whole point — the request
   *  to find the allowable ratio.  Only a ratio that was TYPED and is not one
   *  is refused. */
  const issue = useMemo((): string | null => {
    if (!ctx) return 'no duty is loaded — press ▶ on one in the catalogue';
    // SHORT here: the whole sentence hangs in the ⚠ beside the picker, and the
    // standing rule is one line plus a tooltip (user: no text walls).
    if (calibIssue.level === 'refuse')
      return 'the calibration duty is an impulse point — fit at the rated duty';
    if (form.kind === 'S2' && !(Number(form.tOn) > 0))
      return 'S2 needs a run time: how long the pull lasts, in seconds';
    if (form.kind === 'S3') {
      const typed = (form.edPct ?? '').trim();
      const ed = Number(typed);
      if (typed !== '' && !(ed > 0 && ed <= 100))
        return 'the ED to check is the powered share of a cycle, 0 < ED ≤ 100 % — or leave it blank and it is found';
      if (!(Number(form.cycleS) > 0)) return 'S3 needs a cycle time: one ON + OFF period, in seconds';
    }
    if (form.kind === 'segments'
        && !(form.segments ?? []).some((s) => Number(s.t_s) > 0))
      return 'a segment list needs at least one segment with a duration';
    return null;
  }, [ctx, form, calibIssue]);

  /** THE CYCLE REQUEST, built in one place.
   *
   *  One builder, because the retry after an Electromagnetic run must re-issue
   *  exactly the question that was refused — a second literal here would be a
   *  second way for the run that gets MADE to stop being the run the retry
   *  LOOKS FOR, and that failure is silent: the same refusal, minutes later.
   *
   *  WHICH electromagnetic run the calibration map is built from, and on what
   *  mesh, are sent explicitly, because the route's own defaults (12 steps, a
   *  3 mm mesh) are NOT this app's — the loss map is looked up by the step
   *  count and the mesh, so a request that stayed silent asked for a run nobody
   *  made and was refused by name.  Same two sources the Thermal tab's own
   *  Solve reads: the Electromagnetic tab for the frame count, the Mesh tab for
   *  the mesh (standing rule — every physics value comes from where it was
   *  set). */
  const cycleRequest = useCallback((): DutyCycleRequest | null => {
    if (!ctx) return null;
    // Blank means the CARD's limit, not "no limit": the magnets are judged by
    // default (user 2026-09-16), and the placeholder beside the box says which
    // number that is.  A typed value wins, including a typed 0 — which is the
    // one way left to ask for a cycle judged on the winding alone.
    const ml = magnetLimit.trim() === '' ? magnetCardLimit : Number(magnetLimit);
    const op = simOperatingPoint();
    const mesh = meshParams();
    return {
      die: ctx.die, config: ctx.config, duty: ctx.duty,
      duty_cycle: block,
      n_steps_per_period: op.n_steps_per_period,
      n_periods: 1,
      mesh_size_mm: mesh.mesh_size_mm,
      min_size_mm: mesh.min_size_mm,
      outer_air_factor: mesh.outer_air_factor,
      n_sectors: mesh.n_sectors,
      component_mesh: mesh.component_mesh,
      // The cooling the THERMAL PANEL is showing — not what some earlier
      // session persisted server-side.  The network is fitted to a steady map
      // solved under exactly these boundary conditions.
      thermal_settings: thermalPanelBlock() ?? undefined,
      magnet_limit_c: ml != null && Number.isFinite(ml) ? ml : undefined,
    };
  }, [ctx, block, magnetLimit, magnetCardLimit]);

  /* ── the ONE refusal this editor can answer, and never silently ───────────
     The calibration map is solved at the CALIBRATION DUTY's own stored point,
     which is not the point on the Electromagnetic tab and may not even be this
     duty's.  So a `no_electromagnetic_run` becomes an OFFER: one line, the
     point spelled out, and two buttons.  The run itself goes through the
     orchestrator — `thermalStore.emRunAtPoint`, the same chaining path the
     Thermal tab's own Solve uses — and writes nothing into either tab.

     The state is mirrored into a ref so the async handlers below can read the
     CURRENT phase (a `useState` value captured by a closure is the phase the
     press started in), and `dispatch` returns the next state for the same
     reason. */
  const [offer, setOfferState] = useState<OfferState>(OFFER_IDLE);
  /** WHICH duty the standing offer is about.  A ▶ that puts another duty on the
   *  panel makes it stale, and a stale offer must not be actionable: the point
   *  in it belongs to the previous machine.  Compared rather than reset in an
   *  effect, so nothing here re-renders on its own. */
  const [offerKey, setOfferKey] = useState<string | null>(null);
  const offerRef = useRef<OfferState>(OFFER_IDLE);
  const dispatch = useCallback((a: OfferAction): OfferState => {
    const next = offerReduce(offerRef.current, a);
    offerRef.current = next;
    setOfferState(next);
    return next;
  }, []);

  /** Send the cycle.  `fresh` = a Run press (which clears any standing offer);
   *  the retry after an Electromagnetic run passes false, so that run counts as
   *  the one attempt this press gets. */
  const ask = useCallback(async (fresh: boolean) => {
    const req = cycleRequest();
    if (!req || !ctx) return;
    if (fresh) dispatch({ type: 'run' });
    setBusy(true); setErr(null);
    try {
      const r = await fetchDutyCycle(req);
      setRes(r); setRestoredAt(null);
      dispatch({ type: 'settled' });
    } catch (e) {
      // The backend refuses a cycle it cannot answer BY NAME, and the sentence
      // beside the code is written for an engineer to act on — shown verbatim.
      const why = e instanceof Error ? e.message : String(e);
      if (!isNoEmRun(e)) { setErr(why); return; }
      let point: CalibrationPoint;
      try {
        point = await fetchCalibrationPoint(API, ctx.die, ctx.config, calibDuty);
      } catch (pe) {
        // Without the point there is nothing to offer to run AT, so the
        // refusal stands exactly as it would have before.
        setErr(`${why} (the calibration duty could not be read, so this tab `
          + `cannot offer to make the run: `
          + `${pe instanceof Error ? pe.message : String(pe)})`);
        return;
      }
      // Already answered once for this press — a second refusal is the answer,
      // not a second offer of a second run.
      setOfferKey(key);
      if (dispatch({ type: 'refused', why, point }).phase !== 'offered') {
        setErr(why);
      }
    } finally {
      setBusy(false);
    }
  }, [cycleRequest, ctx, key, calibDuty, dispatch]);

  const run = useCallback(() => {
    if (!ctx || issue) return;
    void ask(true);
  }, [ctx, issue, ask]);

  /** [Make the run] — one electromagnetic run at the CALIBRATION duty's point,
   *  through the orchestrator, then the same cycle request again. */
  const makeEmRun = useCallback(() => {
    const s = offerRef.current;
    if (s.phase !== 'offered' || !s.point) return;
    const point = s.point;
    const why = s.why ?? '';
    dispatch({ type: 'accept' });
    // The frame count and the mesh the CYCLE REQUEST sends — the loss map is
    // looked up by both, so the run has to be made on them and not on whatever
    // the Electromagnetic tab's own Run would have used.
    const op = simOperatingPoint();
    const mesh = meshParams();
    void (async () => {
      const failed = await useThermalStore.getState().emRunAtPoint({
        point, n_steps_per_period: op.n_steps_per_period, mesh,
        label: `Electromagnetic run for the calibration duty “${point.duty}” `
          + `(${pointWords(point)}) — the duty cycle is waiting on it…`,
      }, why);
      dispatch({ type: 'settled' });
      if (failed) { setErr(failed); return; }
      await ask(false);
    })();
  }, [ask, dispatch]);

  /** [Cancel] — the offer is declined and the refusal stands, as before. */
  const declineEmRun = useCallback(() => {
    const s = offerRef.current;
    if (s.phase !== 'offered') return;
    dispatch({ type: 'cancel' });
    setErr(s.why);
  }, [dispatch]);

  /** [Stop] — cancels by run-id, exactly as the Thermal tab's own Stop does. */
  const stopEmRun = useCallback(() => {
    if (dispatch({ type: 'stop' }).stopping) {
      useThermalStore.getState().stopEmFallback();
    }
  }, [dispatch]);

  /* ── the chart's rows ─────────────────────────────────────────────────── */
  const rows = useMemo(() => {
    const c = res?.cycle;
    if (!c?.t_s?.length) return [];
    return c.t_s.map((t, i) => ({
      t,
      winding_hot: c.winding_hot_c?.[i],
      winding: c.T_c?.winding?.[i],
      stator: c.T_c?.stator?.[i],
      rotor: c.T_c?.rotor?.[i],
      magnet: c.T_c?.magnet?.[i],
    }));
  }, [res]);

  /* ── what the shaft delivers while it heats up ─────────────────────────────
     The cycle result carries losses and speeds, never a torque, so the torques
     come from the CONFIGURATION's duty entries and the SHAPE comes from the
     solved spec — the segments that were actually integrated, on the axis the
     temperature chart is drawn on. */
  const torques = useMemo(() => torqueByDuty(duties), [duties]);
  const tqSpan = useMemo(() => {
    const t = res?.cycle?.t_s;
    return t?.length ? t[t.length - 1] - t[0] : null;
  }, [res]);
  const tq = useMemo(
    () => torqueProfile(res?.spec?.segments, torques, tqSpan, res?.cycle?.t_s),
    [res, torques, tqSpan]);
  /** duties this cycle runs that state no torque — said, never drawn as zero */
  const tqMissing = useMemo(
    () => missingTorques(res?.spec?.segments, torques), [res, torques]);

  const lim = res?.limits;
  const cyc = res?.cycle;
  const split = res?.split;
  /** over the limit is not a warning, it is the answer */
  const over = !!(lim && cyc && cyc.winding_hot_peak_c > lim.winding_limit_c);

  /* ── THE REGIME, as the panel says it ─────────────────────────────────────
     The headline first and the charts under it: the answer is the regime, and
     T(t) is the evidence for it rather than the other way round. */
  const regime = useMemo(() => regimeLine(lim), [lim]);
  const edRows = useMemo(() => edCycleRows(lim), [lim]);
  /** the pass/fail chip — present ONLY when "check ED %" was filled */
  const verdict = useMemo(() => checkVerdict(lim), [lim]);
  /** was this ED FOUND (the cycle below is the allowable one) or asked for? */
  const foundEd = !!(lim?.ed_found && lim?.ed_allowable_pct != null);

  /** [Save the found ED to this duty] — the found ratio into this duty's LOCAL
   *  overlay, flagged `found: true` so the report can say "allowable, found by
   *  the tool" rather than printing it as something a person chose.  Local, like
   *  every other edit here: the yaml changes on the duty save and nowhere else
   *  (standing no-silent-state rule). */
  const saveFoundEd = useCallback(() => {
    const ed = lim?.ed_allowable_pct;
    if (!key || ed == null) return;
    try {
      noteDutyCycleEdit(key, { ...dutyCycleFromForm(form), ed_pct: ed,
                               found: true });
      setEdited(true);
    } catch { /* the overlay is a convenience, never a blocker */ }
  }, [key, form, lim]);

  const chip = dutyCycleChip(blockRaw);
  /** what the coupled Run will do in the chosen kind — one line + a ⓘ */
  const runMode = useMemo(() => runModeLine(form.kind), [form.kind]);
  /** a stored kind this editor no longer offers (S2, an explicit segment list) */
  const retired = useMemo(() => retiredKindNote(form.kind), [form.kind]);
  /** an offer or a run left over from ANOTHER duty — shown as nothing at all */
  const offerStale = offer.phase !== 'idle' && offerKey !== key;
  /** the offer line is on screen */
  const offering = offer.phase === 'offered' && !!offer.point && !offerStale;
  /** the orchestrator run this editor asked for is in flight */
  const emRunning = offer.phase === 'running' && !offerStale;

  return (
    <Paper sx={{ p: 1.25, mt: 1.5, bgcolor: 'var(--panel)' }}>
      {/* ── one short header line ─────────────────────────────────────────── */}
      <Box sx={{ display: 'flex', gap: 1.25, alignItems: 'center', flexWrap: 'wrap' }}>
        <Tooltip {...TIP_PROPS} title="What the machine DOES with this duty, over TIME. The steady map above is the temperature after this point has run long enough to stop changing — for a robot joint that is a temperature it never reaches, because it spends two seconds at its peak and a minute at nothing. This solves the cycle: a four-node lumped network (winding, stator core, rotor + shaft, magnets) whose conductances are FITTED to one steady map — the calibration duty's, solved under the cooling set above — and integrated through the profile until the cycle repeats itself. Nothing here runs on its own.">
          <Typography sx={{ ...lbl, fontWeight: 700, cursor: 'help',
            borderBottom: '1px dotted var(--text-4)' }}>
            Duty cycle
          </Typography>
        </Tooltip>
        {ctx ? (
          <Typography sx={{ ...lbl, fontFamily: 'monospace' }}>
            {ctx.duty}{chip ? ` · ${chip}` : ''}
          </Typography>
        ) : (
          <Typography sx={lbl}>no duty loaded — press ▶ on one in the catalogue</Typography>
        )}
        {/* ── what the COUPLED loop found, one line ─────────────────────────
            Never a second answer to the same question: this one is labelled
            with where it came from, and it is the ratio the electromagnetic
            run beside it was actually solved at. */}
        {/* …and NOT on an S1: a continuous duty has no ratio to find, so a
            cycle line there would be the panel answering a question the chosen
            mode does not ask (user 2026-09-16). */}
        {coupled && form.kind !== 'S1' && (
          <Tooltip {...TIP_PROPS} title={`The coupled EM ↔ thermal loop found this regime for ${coupled.duty ?? 'this duty'}: on an impulse duty each pass searches the duty ratio the limits allow and feeds back the temperatures AT it, so the electromagnetic run this answer belongs to was solved at the winding and magnet temperatures of THAT cycle. ${regimeLine(coupled)}. ${coupled.note ?? ''}`}>
            <Typography sx={{ ...lbl, cursor: 'help', fontFamily: 'monospace',
              color: coupled.feasible === false || coupled.fits_requested === false
                ? '#f87171' : '#34d399' }}>
              coupled: {coupled.kind === 'S2'
                ? `pull ${fmt(coupled.t_on_allowable_s, 0)} s`
                : `ED ${fmt(coupled.ed_allowable_pct, 1)} %`}
              {coupled.fits_requested === false ? ' — asked for more' : ''}
              {coupled.feasible === false ? ' — no ratio holds' : ''}
            </Typography>
          </Tooltip>
        )}
        {edited && (
          <Tooltip {...TIP_PROPS} title="This cycle lives only in this browser so far. It is sent to the yaml by the duty save (the ✓ in the strip at the top), the same way a material pick is — until then the catalogue, the report and every other browser still see the previous block.">
            <Typography sx={warn}>⚠ un-saved — press Save to duty</Typography>
          </Tooltip>
        )}
      </Box>

      {/* ALWAYS OPEN, since 2026-09-16.  It used to hide behind a Hide/Show
          button near the bottom of this tab, which made the duty cycle look
          optional: the user asked for the opposite («Меню Duty cycle должно
          быть всегда открыто и находиться вверху, после frame»), because
          choosing S1 or S3 is the first decision of the run, not the last.
          There is no collapsed state left to persist. */}
      <>
          {/* ── the profile, one line ───────────────────────────────────── */}
          <Box sx={{ display: 'flex', gap: 1.25, alignItems: 'center',
            flexWrap: 'wrap', mt: 1 }}>
            <Box sx={CTRL_ROW}>
              {/* TWO KINDS are offered (user 2026-09-16: «S2, я думаю, нужно
                  выбросить, не знаю ему пока применения»).  A duty that still
                  STORES an S2 or a segment list renders it — disabled, so it
                  can be read and left but never chosen again — because the
                  yaml, the backend and the report all still understand one. */}
              <Select size="small" value={form.kind}
                onChange={(e) => patch({ kind: String(e.target.value) })}
                sx={{ fontSize: 11, height: 30, minWidth: 220 }}>
                {[...OFFERED_KINDS].map((k) => (
                  <MenuItem key={k} value={k} sx={{ fontSize: 11 }}>
                    {KIND_LABEL[k as DutyCycleKind]}
                  </MenuItem>
                ))}
                {retired && (
                  <MenuItem key={form.kind} value={form.kind} disabled
                    sx={{ fontSize: 11 }}>
                    {KIND_LABEL[form.kind as DutyCycleKind] ?? form.kind}
                  </MenuItem>
                )}
              </Select>
              <HelpTip title="S1 runs this point for ever; S3 runs it for ED % of a repeated cycle and rests the remainder." />
            </Box>

            {/* ONE LINE: what the coupled Run will do in the chosen mode.  The
                flow has been in the backend since the coupled loop learned to
                solve an S3 duty for its regime, and it was nowhere on screen —
                the picker sat in one panel and the Run button in another. */}
            {runMode && (
              <Box sx={CTRL_ROW}>
                <Typography sx={{ ...lbl, fontFamily: 'monospace',
                  color: 'var(--text-2)' }}>
                  {runMode.line}
                </Typography>
                <HelpTip title={runMode.tip} />
              </Box>
            )}

            {/* …and a kind that is stored but no longer offered says so, once. */}
            {retired && (
              <Tooltip {...TIP_PROPS} title={`This duty's stored cycle block is a ${form.kind}, and this editor no longer offers that kind — it is shown so the duty can be read and left, not edited into another one. Pick S1 for a point the machine sits at, or S3 for a ratio of a repeated cycle; the block is rewritten on the duty save, and until then nothing in the yaml has changed.`}>
                <Typography sx={{ ...warn, fontWeight: 700 }}>⚠ {retired}</Typography>
              </Tooltip>
            )}

            {form.kind === 'S2' && (
              <Box sx={CTRL_ROW}>
                <TextField label="t_on s" size="small" value={form.tOn ?? ''}
                  disabled
                  sx={{ width: 96 }} inputProps={{ style: { fontSize: 12 } }}
                  InputLabelProps={{ style: { fontSize: 12 } }} />
                <HelpTip title="How long the single pull lasts, in seconds — read-only: S2 is no longer offered." />
              </Box>
            )}

            {form.kind === 'S3' && (
              <>
                <Box sx={CTRL_ROW}>
                  <TextField label="cycle s" size="small" value={form.cycleS ?? ''}
                    onChange={(e) => patch({ cycleS: e.target.value })}
                    sx={{ width: 96 }} inputProps={{ style: { fontSize: 12 } }}
                    InputLabelProps={{ style: { fontSize: 12 } }} />
                  <HelpTip title="One ON + OFF period, in seconds — the ED that comes back is the ratio of THIS period." />
                </Box>
                {/* THE OPTIONAL CHECK, not the input.  Blank = find the ED. */}
                <Box sx={CTRL_ROW}>
                  <TextField label="check ED %" size="small" placeholder="found"
                    value={form.edPct ?? ''}
                    onChange={(e) => patch({ edPct: e.target.value })}
                    sx={{ width: 108 }} inputProps={{ style: { fontSize: 12 } }}
                    InputLabelProps={{ style: { fontSize: 12 }, shrink: true }} />
                  <HelpTip title="Leave it blank and the allowable ED is FOUND; fill it to check one ratio and get a pass/fail." />
                </Box>
                <Box sx={CTRL_ROW}>
                  <Select size="small" displayEmpty value={form.restDuty ?? ''}
                    onChange={(e) => patch({ restDuty: String(e.target.value) })}
                    sx={{ fontSize: 11, height: 30, minWidth: 190 }}>
                    <MenuItem value="" sx={{ fontSize: 11 }}>rest: unpowered</MenuItem>
                    {names.map((n) => (
                      <MenuItem key={n} value={n} sx={{ fontSize: 11 }}>rest: {n}</MenuItem>
                    ))}
                  </Select>
                  <HelpTip title="What the machine does while it rests — another duty, or unpowered." />
                </Box>
              </>
            )}

            {/* READ-ONLY, like S2: a stored segment list is shown as it stands
                so the duty can be understood before it is moved to S1 or S3. */}
            {form.kind === 'segments' && (
              <>
                {(form.segments ?? []).map((sg, i) => (
                  <Box key={i} sx={{ display: 'flex', gap: 0.5, alignItems: 'center' }}>
                    <Select size="small" displayEmpty value={sg.duty} disabled
                      sx={{ fontSize: 11, height: 30, minWidth: 150 }}>
                      <MenuItem value="" sx={{ fontSize: 11 }}>unpowered</MenuItem>
                      {names.map((n) => (
                        <MenuItem key={n} value={n} sx={{ fontSize: 11 }}>{n}</MenuItem>
                      ))}
                    </Select>
                    <TextField label="s" size="small" value={sg.t_s} disabled
                      sx={{ width: 72 }} inputProps={{ style: { fontSize: 12 } }}
                      InputLabelProps={{ style: { fontSize: 12 } }} />
                  </Box>
                ))}
              </>
            )}
          </Box>

          {/* ── what it is fitted at, and where it starts ────────────────── */}
          <Box sx={{ display: 'flex', gap: 1.25, alignItems: 'center',
            flexWrap: 'wrap', mt: 1 }}>
            <Box sx={CTRL_ROW}>
              <Select size="small" displayEmpty value={form.calibrationDuty ?? ''}
                onChange={(e) => patch({ calibrationDuty: String(e.target.value) })}
                sx={{ fontSize: 11, height: 30, minWidth: 230 }}>
                <MenuItem value="" sx={{ fontSize: 11 }}>
                  fitted at: {rated || 'the rated duty'} (default)
                </MenuItem>
                {names.map((n) => (
                  <MenuItem key={n} value={n} sx={{ fontSize: 11 }}>fitted at: {n}</MenuItem>
                ))}
              </Select>
              <HelpTip title="Which duty's steady map the lumped network's conductances are fitted to — the rated duty, never an impulse point." />
            </Box>
            {calibIssue.level !== 'ok' && (
              <Tooltip {...TIP_PROPS} title={`${calibIssue.text} Every conductance in the four-node network is divided out of ONE converged steady map — the calibration duty's — so that choice decides every temperature below it. The steady map of a PEAK point is the machine after the peak has run for ever, which is a machine that would have burned: fitting there fits the conductances at 600 °C.`}>
                <Typography sx={calibIssue.level === 'refuse'
                  ? { ...warn, color: '#f87171',
                      borderBottom: '1px dotted #f87171', fontWeight: 700 }
                  : warn}>
                  ⚠ {calibIssue.level === 'refuse'
                    ? 'impulse duty as calibration' : 'not the rated duty'}
                </Typography>
              </Tooltip>
            )}
            <Box sx={CTRL_ROW}>
              <TextField label="start °C" size="small" value={form.tStartC ?? ''}
                onChange={(e) => patch({ tStartC: e.target.value })}
                sx={{ width: 96 }} inputProps={{ style: { fontSize: 12 } }}
                InputLabelProps={{ style: { fontSize: 12 } }} />
              <HelpTip title="Where the machine starts, °C — blank is the ambient set above." />
            </Box>
            <Box sx={CTRL_ROW}>
              <TextField label="magnet limit °C" size="small" value={magnetLimit}
                onChange={(e) => setMagnetLimit(e.target.value)}
                placeholder={magnetCardLimit != null
                  ? String(magnetCardLimit) : 'none on the card'}
                sx={{ width: 148 }} inputProps={{ style: { fontSize: 12 } }}
                InputLabelProps={{ style: { fontSize: 12 }, shrink: true }} />
              <HelpTip title={magnetCardLimit != null
                ? `A magnet temperature to judge the cycle against, °C. Blank uses the assigned magnet card's own maximum working temperature, ${magnetCardLimit} °C — so the magnets are judged by default and the line is drawn.`
                : 'A magnet temperature to judge the cycle against, °C. The assigned magnet card states no maximum working temperature, so a blank draws no line and judges nothing.'} />
            </Box>

            <Box sx={CTRL_ROW}>
              <Button variant="contained" size="small" onClick={run}
                disabled={busy || emRunning || !!issue}
                startIcon={busy ? <CircularProgress size={13} color="inherit" /> : undefined}>
                {busy ? 'Running' : 'Run cycle'}
              </Button>
              <HelpTip title="One calibration thermal map, then the cycle integrated on it — seconds, not minutes." />
            </Box>
            {issue && !busy && (
              <Tooltip {...TIP_PROPS} title="The cycle as written does not describe something that can be integrated, so Run is disabled until it does — this tab validates its input rather than sending it and translating the solver's refusal back.">
                <Typography sx={{ ...warn, color: '#f87171',
                  borderBottom: '1px dotted #f87171', fontWeight: 700 }}>
                  ⚠ {issue}
                </Typography>
              </Tooltip>
            )}
            {res && !busy && (
              <Tooltip {...TIP_PROPS} title={`One calibration thermal map${res.cached ? ' (reused — a matching map was already solved at this point and these boundary conditions)' : ' (solved for this)'} plus the cycle integration.${restoredAt ? ` Answered ${String(restoredAt).replace('T', ' ').replace('+00:00', ' UTC')}.` : ''}`}>
                <Typography sx={{ ...lbl, ml: 'auto', cursor: 'help' }}>
                  {res.cached ? 'map reused · ' : ''}
                  {fmtSecs(res.elapsed_s ?? res.solve_time_s)}
                </Typography>
              </Tooltip>
            )}
          </Box>

          {/* ── the missing Electromagnetic run, OFFERED ─────────────────────
              One line under Run.  Never taken silently: it costs minutes, and
              it is made at the CALIBRATION duty's point — not the one the
              Electromagnetic tab is showing. */}
          {offering && offer.point && (
            <Box sx={{ display: 'flex', gap: 1, alignItems: 'center',
              flexWrap: 'wrap', mt: 1 }}>
              <Tooltip {...TIP_PROPS} title={`The network is fitted to ONE steady map, and that map is solved at the calibration duty “${offer.point.duty}”’s own saved point (${pointTooltipWords(offer.point)}) — not at the point on the Electromagnetic tab. There is no electromagnetic run of this machine at it, so there is no loss field to heat it with. Making it here runs the EM ↔ thermal orchestrator once at THAT point, with the cooling this panel is showing; nothing is written into the Electromagnetic tab or into this panel, and the cycle is re-sent by itself when it finishes. The backend refused with: ${offer.why ?? ''}`}>
                <Typography sx={{ ...warn, fontWeight: 700, whiteSpace: 'normal' }}>
                  ⚠ No electromagnetic run at the calibration point
                  {' '}({pointWords(offer.point)}). Make it now (~2 min)?
                </Typography>
              </Tooltip>
              <Button variant="contained" size="small" onClick={makeEmRun}>
                Make the run
              </Button>
              <Button variant="text" size="small" onClick={declineEmRun}>
                Cancel
              </Button>
            </Box>
          )}

          {/* ── …and while it runs.  The progress bar is the orchestrator's,
              at the top of this tab's scroller (the thermal tracker is silent
              through the transient, which is most of the wait). */}
          {emRunning && (
            <Box sx={{ display: 'flex', gap: 1, alignItems: 'center',
              flexWrap: 'wrap', mt: 1 }}>
              <CircularProgress size={13} />
              <Tooltip {...TIP_PROPS} title="One electromagnetic run at the calibration duty’s point, through the EM ↔ thermal orchestrator — the progress bar at the top of this tab is its own. When it finishes, the cycle above is sent again by itself.">
                <Typography sx={{ ...lbl, cursor: 'help' }}>
                  making the electromagnetic run at the calibration point…
                </Typography>
              </Tooltip>
              <Tooltip {...TIP_PROPS} title="Cancel the electromagnetic run. It stops between phases and, inside a transient, at the next frame — so it can take a few seconds, and it leaves no result.">
                <span>
                  <Button variant="outlined" size="small" color="warning"
                    onClick={stopEmRun} disabled={offer.stopping}>
                    {offer.stopping ? 'Stopping' : 'Stop'}
                  </Button>
                </span>
              </Tooltip>
            </Box>
          )}

          {err && <Alert severity="error" sx={{ mt: 1, fontSize: 12 }}>{err}</Alert>}

          {/* ── the answer, one short line ──────────────────────────────── */}
          {res && cyc && lim && (
            <>
              {/* ── THE ANSWER, FIRST ────────────────────────────────────────
                  The regime the machine can hold, in one line, above every
                  chart: the charts are the EVIDENCE for it, not the answer. */}
              {regime && (
                <Box sx={{ display: 'flex', gap: 1, alignItems: 'center',
                  flexWrap: 'wrap', mt: 1.25 }}>
                  <Tooltip {...TIP_PROPS} title={`${lim.ed_found_note ?? ''} ${lim.ed_note ?? ''} The allowable ratio is found by bisecting the duty ratio against the PERIODIC peak hot spot, so it is the ED at which this cycle sits ON the limit — the limiting part is ${String(lim.ed_limiting_part ?? '—')}. ${lim.s2_note ?? ''} ${lim.s2_from_rated_note ?? ''} ${lim.s2_from_cycle_mean_note ?? ''} At the allowable point the winding hot spot peaks at ${fmt(lim.at_allowable?.winding_hot_peak_c, 1)} °C, the stator core at ${fmt(lim.at_allowable?.peak_c?.stator, 1)} °C and the magnets at ${fmt(lim.at_allowable?.magnet_peak_c, 1)} °C.`}>
                    <Typography sx={{ ...lbl, cursor: 'help', fontWeight: 700,
                      fontFamily: 'monospace', whiteSpace: 'normal',
                      color: 'var(--text-0)' }}>
                      {regime}
                    </Typography>
                  </Tooltip>
                  {verdict && (
                    <Tooltip {...TIP_PROPS} title={`You asked this tool to CHECK ${fmt(verdict.askedPct, 1)} %, so it also integrated that cycle — the charts below are the one you asked about, not the allowable one. ${verdict.ok ? 'It fits under every limit.' : 'It does not: at that ratio the cycle goes over.'}`}>
                      <Typography sx={{ ...lbl, cursor: 'help', fontWeight: 700,
                        fontFamily: 'monospace',
                        border: '1px solid var(--line-soft)', borderRadius: 1,
                        px: 0.75, py: 0.125,
                        color: verdict.ok ? '#34d399' : '#f87171' }}>
                        {verdict.text}
                      </Typography>
                    </Tooltip>
                  )}
                  {foundEd && (
                    <Tooltip {...TIP_PROPS} title="Write the FOUND duty ratio into this duty's cycle block, flagged as found by the tool rather than chosen by hand — so the report can say “allowable, found by the tool”. Local to this browser until the duty save (the ✓ in the strip at the top), like every other edit here.">
                      <Button size="small" variant="outlined"
                        sx={{ fontSize: 11, py: 0 }} onClick={saveFoundEd}>
                        Save the found ED
                      </Button>
                    </Tooltip>
                  )}
                </Box>
              )}

              <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'center',
                flexWrap: 'wrap', mt: 1.25 }}>
                <Tooltip {...TIP_PROPS} title={`The winding HOT SPOT over the cycle: peak ${fmt(cyc.winding_hot_peak_c, 1)} °C, mean ${fmt(cyc.winding_hot_mean_c, 1)} °C, against the project's ${fmt(lim.winding_limit_c, 0)} °C insulation class. ${lim.winding_limit_note ?? ''} The mean of the winding NODE is ${fmt(cyc.mean_c?.winding, 1)} °C — the hot spot is that plus the calibration map's own max − mean offset of ${fmt(cyc.hot_spot_offset_K, 1)} K, held constant, which is a stated approximation and not a solved gradient.`}>
                  <Typography sx={{ ...lbl, cursor: 'help', fontFamily: 'monospace',
                    fontWeight: 700, color: over ? '#f87171' : 'var(--text-0)' }}>
                    winding hot {fmt(cyc.winding_hot_peak_c, 0)} °C peak / {fmt(cyc.winding_hot_mean_c, 0)} °C mean
                    {over ? ` — over the ${fmt(lim.winding_limit_c, 0)} °C class` : ''}
                  </Typography>
                </Tooltip>
                <Tooltip {...TIP_PROPS} title={`The magnets over the cycle: peak ${fmt(cyc.peak_c?.magnet, 1)} °C, mean ${fmt(cyc.mean_c?.magnet, 1)} °C, minimum ${fmt(cyc.min_c?.magnet, 1)} °C. ${lim.magnet_limit_c != null ? `Judged against ${fmt(lim.magnet_limit_c, 0)} °C.` : (lim.magnet_limit_note ?? '')} The rotor node sits at ${fmt(cyc.peak_c?.rotor, 1)} °C peak and the stator core at ${fmt(cyc.peak_c?.stator, 1)} °C.`}>
                  <Typography sx={{ ...lbl, cursor: 'help', fontFamily: 'monospace' }}>
                    magnets {fmt(cyc.peak_c?.magnet, 0)} °C
                  </Typography>
                </Tooltip>
                {lim.s2_time_to_limit_s != null ? (
                  <Tooltip {...TIP_PROPS} title={`${lim.s2_note ?? ''} Integrated from the start temperature with no rest at all, so it is the length of ONE pull and nothing else.`}>
                    <Typography sx={{ ...lbl, cursor: 'help', fontFamily: 'monospace' }}>
                      S2 {fmt(lim.s2_time_to_limit_s, 0)} s to {String(lim.s2_limiting_part ?? '')}
                    </Typography>
                  </Tooltip>
                ) : (
                  <Tooltip {...TIP_PROPS} title={lim.s2_note ?? ''}>
                    <Typography sx={{ ...lbl, cursor: 'help', fontFamily: 'monospace' }}>
                      S2 — settles below every limit
                    </Typography>
                  </Tooltip>
                )}
                {/* the asked-for ratio beside the allowable one — only in the
                    CHECK mode; the headline above already carries the found
                    one, and repeating it would be two answers to one question */}
                {lim.ed_allowable_pct != null && lim.ed_requested_pct != null && (
                  <Tooltip {...TIP_PROPS} title={`${lim.ed_note ?? ''} Found by bisecting the duty ratio against the periodic peak hot spot, so it is the ED at which this cycle SITS on the limit — the limiting part is ${String(lim.ed_limiting_part ?? '—')}. The requested ${fmt(lim.ed_requested_pct, 1)} % is what you typed.`}>
                    <Typography sx={{ ...lbl, cursor: 'help', fontFamily: 'monospace',
                      fontWeight: 700,
                      color: (lim.ed_requested_pct ?? 0) > lim.ed_allowable_pct
                        ? '#f87171' : '#34d399' }}>
                      ED {fmt(lim.ed_requested_pct, 0)} % asked / {fmt(lim.ed_allowable_pct, 0)} % allowed
                    </Typography>
                  </Tooltip>
                )}
                {split && (
                  <Tooltip {...TIP_PROPS} title={`Time-averaged over one cycle: the STATOR side loses ${fmt(split.stator_side_W, 1)} W (housing ${fmt(split.housing_W, 1)} W, mount ${fmt(split.mount_W, 1)} W, end turns ${fmt(split.winding_end_faces_W, 1)} W, stator end faces ${fmt(split.stator_end_faces_W, 1)} W) and the ROTOR side ${fmt(split.rotor_side_W, 1)} W (bore ${fmt(split.bore_W, 1)} W, shaft ends ${fmt(split.shaft_ends_W, 1)} W, rotor end faces ${fmt(split.rotor_end_faces_W, 1)} W, magnet end faces ${fmt(split.magnet_end_faces_W, 2)} W). ${fmt(split.gap_W, 1)} W crosses the air gap from the rotor to the stator. Generation ${fmt(split.generated_total_W, 1)} W; the balance closes to ${fmt(split.closure_pct, 3)} %.`}>
                    <Typography sx={{ ...lbl, cursor: 'help', fontFamily: 'monospace' }}>
                      stator side {fmt(split.stator_pct, 0)} % / rotor side {fmt(split.rotor_pct, 0)} %
                    </Typography>
                  </Tooltip>
                )}
                <Tooltip {...TIP_PROPS} title={`${cyc.note ?? ''} ${cyc.converged === false ? 'The cycle map did NOT reach a fixed point within the cap — the answer below is the last iterate and the machine is still climbing.' : ''} Residual ${fmt(cyc.residual_K, 3)} K after ${cyc.n_cycles} cycle(s); the energy balance over the shown cycle closes to ${fmt(cyc.closure_pct, 3)} % (${fmt(cyc.energy_in_J, 0)} J in, ${fmt(cyc.energy_out_J, 0)} J out, ${fmt(cyc.stored_J, 0)} J stored). ${cyc.n_solved} points were integrated and ${cyc.n_samples} kept for the chart. The network was fitted at "${String(res.network?.calibration_duty ?? '—')}" and the machine's total heat capacity is ${fmt(res.network?.C_total_J_per_K, 0)} J/K.`}>
                  <Typography sx={{ ...lbl, cursor: 'help', fontFamily: 'monospace',
                    color: cyc.converged === false ? '#fbbf24' : 'var(--text-3)' }}>
                    {cyc.converged === false ? '⚠ not periodic' : 'periodic'} · {cyc.n_cycles} cycle{cyc.n_cycles === 1 ? '' : 's'}
                  </Typography>
                </Tooltip>
              </Box>

              {/* ── the two pictures, side by side ───────────────────────────
                  LEFT: the answer as a function of the period — the allowable
                  ED at 10, 30, 60, 120 and 300 s, which is what makes the one
                  number above mean something.  RIGHT: the cycle itself, at the
                  ALLOWABLE ratio (or at the checked one), and the torque it
                  delivers on the same axis and the same hover. */}
              <Box sx={{ display: 'flex', gap: 2, mt: 1, flexWrap: 'wrap',
                alignItems: 'flex-start' }}>
                {edRows.length > 1 && (
                  <Box sx={{ flex: '1 1 300px', minWidth: 280, height: 260 }}>
                    <Box sx={{ display: 'flex', gap: 1, alignItems: 'center',
                      flexWrap: 'wrap' }}>
                      <Typography sx={{ fontSize: 11, fontWeight: 700,
                        color: 'var(--text-2)' }}>
                        Allowable ED vs cycle length
                      </Typography>
                      <Tooltip {...TIP_PROPS} title={`The same search at ${edRows.map((r) => `${fmt(r.cycleS, 0)} s → ${fmt(r.edPct, 1)} % (${fmt(r.tOnS, 1)} s on, magnets ${fmt(r.magnetC, 0)} °C)`).join(', ')}. A short period is ridden out on the machine's own heat capacity, so a high ratio is allowed; a long one has to be in thermal balance and the allowable ratio falls towards the continuous answer. Every point sits ON the limit — this is one answer at five periods, not five judgements. Solved on the network already fitted: no extra field solve was paid for it.`}>
                        <Typography sx={{ ...lbl, cursor: 'help',
                          fontFamily: 'monospace' }}>
                          on-time {fmt(edRows[0].tOnS, 1)} → {fmt(edRows[edRows.length - 1].tOnS, 1)} s
                        </Typography>
                      </Tooltip>
                    </Box>
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={edRows}
                        margin={{ top: 8, right: 14, left: 0, bottom: 16 }}>
                        <CartesianGrid {...GRID} />
                        <XAxis dataKey="cycleS" type="number" scale="log"
                          domain={['dataMin', 'dataMax']} tick={AXIS}
                          ticks={edRows.map((r) => r.cycleS)}
                          tickFormatter={(v: number) => String(Math.round(v))}
                          label={{ value: 'cycle length [s]',
                            position: 'insideBottom', offset: -4,
                            style: { fontSize: 10, fill: 'var(--text-4)' } }} />
                        <YAxis tick={AXIS} width={Y_AXIS_W} domain={[0, 'auto']}
                          label={{ value: 'ED [%]', angle: -90,
                            position: 'insideLeft', offset: 12,
                            style: { fontSize: 10, fill: 'var(--text-4)' } }} />
                        <RcTooltip {...ED_TOOLTIP} />
                        {lim.ed_cycle_s != null && (
                          <ReferenceLine x={lim.ed_cycle_s} stroke="#fbbf24"
                            strokeDasharray="4 4" ifOverflow="extendDomain"
                            label={{ value: `${fmt(lim.ed_cycle_s, 0)} s`,
                              position: 'top',
                              style: { fontSize: 9, fill: '#fbbf24' } }} />
                        )}
                        <Line type="monotone" dataKey="edPct"
                          name="allowable ED" stroke={ED_INK} strokeWidth={1.8}
                          dot={{ r: 2.5, fill: ED_INK }}
                          isAnimationActive={false} />
                      </LineChart>
                    </ResponsiveContainer>
                  </Box>
                )}

                <Box sx={{ flex: '2 1 420px', minWidth: 320 }}>
              {/* ── T(t), with the lines it must not cross ───────────────── */}
              {rows.length > 1 && (
                <Box sx={{ height: 260 }}>
                  <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-2)' }}>
                    T(t) over one cycle
                    <span style={{ color: 'var(--text-4)', fontWeight: 400 }}>
                      {'  ·  '}{fmt(res.spec?.cycle_s, 2)} s
                      {res.spec?.kind === 'S2' ? ' (one pull)'
                        : (res.spec?.ed_given === false
                          ? ` at the allowable ${fmt(res.spec?.ed_pct, 1)} %`
                          : ' (the periodic state)')}
                    </span>
                  </Typography>
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={rows} syncId={SYNC} syncMethod="value"
                      margin={{ top: 8, right: 10, left: 0, bottom: 16 }}>
                      <CartesianGrid {...GRID} />
                      <XAxis dataKey="t" type="number" tick={AXIS}
                        domain={['dataMin', 'dataMax']}
                        tickFormatter={(v: number) => Number(v).toFixed(1)}
                        label={{ value: 't [s]', position: 'insideBottom', offset: -4,
                          style: { fontSize: 10, fill: 'var(--text-4)' } }} />
                      <YAxis tick={AXIS} width={Y_AXIS_W}
                        label={{ value: 'T [°C]', angle: -90, position: 'insideLeft',
                          offset: 12, style: { fontSize: 10, fill: 'var(--text-4)' } }} />
                      <RcTooltip {...TOOLTIP} />
                      <Legend wrapperStyle={{ fontSize: 10 }} />
                      {/* the class, drawn rather than described */}
                      <ReferenceLine y={lim.winding_limit_c} stroke="#f87171"
                        strokeDasharray="4 4" ifOverflow="extendDomain"
                        label={{ value: `class ${fmt(lim.winding_limit_c, 0)} °C`,
                          position: 'right', style: { fontSize: 9, fill: '#f87171' } }} />
                      {lim.magnet_limit_c != null && (
                        <ReferenceLine y={lim.magnet_limit_c} stroke="#a78bfa"
                          strokeDasharray="4 4" ifOverflow="extendDomain"
                          label={{ value: `magnets ${fmt(lim.magnet_limit_c, 0)} °C`,
                            position: 'right', style: { fontSize: 9, fill: '#a78bfa' } }} />
                      )}
                      {NODE_INK.map(([k, name, ink]) => (
                        <Line key={k} type="monotone" dataKey={k} name={name}
                          stroke={ink} strokeWidth={k === 'winding_hot' ? 1.6 : 1.1}
                          dot={false} isAnimationActive={false} />
                      ))}
                    </LineChart>
                  </ResponsiveContainer>
                </Box>
              )}

              {/* ── …and what the shaft is DELIVERING while it does that ─────
                  Same axis, same hover (syncId), one step per segment: the
                  torque of the duty that runs in it, 0 while unpowered. */}
              {tq && tq.rows.length > 1 && (
                <Box sx={{ height: 170, mt: 2.5 }}>
                  <Box sx={{ display: 'flex', gap: 1, alignItems: 'center',
                    flexWrap: 'wrap' }}>
                    <Typography sx={{ fontSize: 11, fontWeight: 700,
                      color: 'var(--text-2)' }}>
                      Shaft torque over the same cycle
                    </Typography>
                    <Tooltip {...TIP_PROPS} title={`The torque of whichever duty runs in each segment — ${tq.steps.map((s) => `${s.duty} ${fmt(s.nm, 2)} N·m for ${fmt(s.t1 - s.t0, 1)} s`).join(', ')} — from the configuration's own duty entries, not from the thermal solve. The mean is time-weighted over the ${fmt(tq.spanS, 1)} s cycle, which is the torque a gearbox behind this joint actually sees; the peak is ${fmt(tq.peakNm, 2)} N·m.`}>
                      <Typography sx={{ ...lbl, cursor: 'help',
                        fontFamily: 'monospace', fontWeight: 700,
                        border: '1px solid var(--line-soft)', borderRadius: 1,
                        px: 0.75, py: 0.125, color: 'var(--text-0)' }}>
                        mean {fmt(tq.meanNm, 1)} N·m
                      </Typography>
                    </Tooltip>
                  </Box>
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={tq.rows} syncId={SYNC} syncMethod="value"
                      margin={{ top: 8, right: 10, left: 0, bottom: 16 }}>
                      <CartesianGrid {...GRID} />
                      <XAxis dataKey="t" type="number" tick={AXIS}
                        domain={['dataMin', 'dataMax']}
                        tickFormatter={(v: number) => Number(v).toFixed(1)}
                        label={{ value: 't [s]', position: 'insideBottom', offset: -4,
                          style: { fontSize: 10, fill: 'var(--text-4)' } }} />
                      <YAxis tick={AXIS} width={Y_AXIS_W}
                        label={{ value: 'N·m', angle: -90, position: 'insideLeft',
                          offset: 12, style: { fontSize: 10, fill: 'var(--text-4)' } }} />
                      <RcTooltip {...TQ_TOOLTIP} />
                      <ReferenceLine y={tq.meanNm} stroke="#34d399"
                        strokeDasharray="4 4" ifOverflow="extendDomain"
                        label={{ value: `mean ${fmt(tq.meanNm, 1)} N·m`,
                          position: 'right',
                          style: { fontSize: 9, fill: '#34d399' } }} />
                      <Line type="stepAfter" dataKey="nm" name="shaft torque"
                        stroke="#38bdf8" strokeWidth={1.6} dot={false}
                        isAnimationActive={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </Box>
              )}
              {!tq && tqMissing.length > 0 && (
                <Tooltip {...TIP_PROPS} title="A segment's torque is the torque of the duty that runs in it, and these duties carry none — the panel refuses to draw a step at zero for a point that pulls. Save the duty with its torque (the ✓ in the strip at the top, with the torque field filled) and the profile appears.">
                  <Typography sx={{ ...warn, mt: 1, display: 'block' }}>
                    ⚠ no torque stored for {tqMissing.join(', ')} — torque profile not drawn
                  </Typography>
                </Tooltip>
              )}
                </Box>
              </Box>
            </>
          )}
      </>
    </Paper>
  );
};

export default DutyCycleEditor;
