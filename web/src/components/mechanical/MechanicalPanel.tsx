/** Mechanical tab — rotor centrifugal stress and the retaining sleeve.
 *
 * Added 2026-09-05 for the user's request: "начнём с расчёта центробежных сил
 * ротора ... чтобы оценить какой бандаж нужен для удержания магнитов и ротора".
 *
 * Nothing solves on mount.  The operating point comes from the Electromagnetic tab
 * (standing project rule: every physics setting of a run is read from there,
 * never from a default in a panel), and Solve is a deliberate press.
 *
 * 2026-09-06 the panel stopped OWNING what it shows.  User: "когда я захожу и
 * выхожу в Mechanical, графики пропадают. Нужно, чтобы по умолчанию: если нет
 * расчётов — рисуется просто геометрия; если есть — подгружается последний
 * расчёт; если были изменения текущей геометрии — нужно подсвечивать
 * неактуальность текущего расчёта."  This tab is not `keepMounted` (it draws
 * its own picture, so it does not take the AppBar's viewer cluster), so leaving
 * it unmounted the component and every `useState` result went with it — a
 * 30-second contact solve thrown away by a click.  The results and the toolbar
 * choices now live in `stores/mechanicalStore`, which also fetches the
 * backend's `/api/mechanical/last` on the first mount after a reload.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, Box, Button, CircularProgress, MenuItem,
  Paper, Select, TextField, Tooltip, Typography,
} from '@mui/material';

import { effectiveProofRpm, isStale, useMechanicalStore } from '../../stores/mechanicalStore';
import {
  thermalTempsLine, thermalTempsTip, thermalTempsInUse,
} from '../../lib/mechThermalTemps';
import { historyNoticeFor } from '../../lib/historyNotice';
import { useMotorStore } from '../../stores/motorStore';
import AddResultToCompareButton from '../compare/AddResultToCompareButton';
import { MAX_LOCAL_ROWS, localMechanicalRow } from '../compare/resultRows';
import LocalCompareTable from '../common/LocalCompareTable';
import type { ColumnDef } from '../common/LocalCompareTable';
import SolveProgressStrip from '../common/SolveProgressStrip';
import ModalSection from './ModalSection';
import BearingsSection from './BearingsSection';
import { SolveTimer, solvedIn } from './SolveTimer';
import StressMap, { GeometryMap } from './StressMap';
import {
  CONTACT_LABEL, CONTACT_PAIRS, LOADS_LABEL, PART_LABEL, REF_TEMP_C, SEAT_SURFACE,
  caseKeys, coupledPoint, fmt, fmtSecs, pickCase, readSimSetting, sfAccent,
} from './api';
import type {
  CaseResult, CaseName, ContactType, LoadsMode, PartResult, RotorStress,
} from './api';
import type { MechView } from './fieldAdapters';

/** Tooltip on a Select: keep the hint UNDER the menu the Select opens (MUI
 *  draws tooltips at z 1500, menus at z 1300, so a hint wrapped round a Select
 *  used to cover its own options — 'падающее меню подсказки не даёт сменить
 *  воздух на жидкость', 2026-09-07). */
const SELECT_TIP = { popper: { sx: { zIndex: 1250 } } } as const;

const lbl = { fontSize: 11, color: 'var(--text-3)', whiteSpace: 'nowrap' } as const;
const warn = { ...lbl, color: '#fbbf24', cursor: 'help', borderBottom: '1px dotted #fbbf24' } as const;

const r0 = (v: number) => Math.round(v);

/** Were any temperatures GIVEN — whether or not they loaded anything.  Under
 *  the band-fit rule (2026-09-09) a sleeveless rotor's map is `active: false`
 *  and still worth showing: the user wants the coupling's real numbers in
 *  view, with the sentence saying they were not a load. */
const tempsGiven = (t: RotorStress['thermal']): boolean => {
  if (!t) return false;
  if (t.active) return true;
  const p = t.part_temps_c;
  const vals = p ? [p.magnet, p.rotor_core, p.shaft, p.sleeve]
                 : [t.rotor_temp_c, t.sleeve_temp_c];
  return vals.some((v) => Number.isFinite(v) && Math.abs(v - t.ref_temp_c) > 0.5);
};

/** The temperature badge on the context line: `150/150 °C` while ONE number
 *  covers the rotor side, `163/161/158/162 °C` (magnet / core / shaft / sleeve)
 *  once they differ — which is what a run coupled to the Thermal tab produces
 *  (2026-09-08).  The tooltip beside it names the order. */
const tempBadge = (t: RotorStress['thermal']): string => {
  if (!tempsGiven(t) || !t) return '';
  const p = t.part_temps_c;
  if (!p) return `${r0(t.rotor_temp_c)}/${r0(t.sleeve_temp_c)} °C`;
  const oneRotorNumber = [p.magnet, p.shaft]
    .every((v) => Math.abs(v - p.rotor_core) < 0.5);
  return oneRotorNumber
    ? `${r0(p.rotor_core)}/${r0(p.sleeve)} °C`
    : `${r0(p.magnet)}/${r0(p.rotor_core)}/${r0(p.shaft)}/${r0(p.sleeve)} °C`;
};

/** The same thing spelled out, for the tooltip — and what it DID.  User
 *  2026-09-09: "нам нужно учитывать температуру только как изменение давления
 *  на бандаж, если он есть": a temperature is a load on the band's fit and
 *  nowhere else, so the sentence says either how the fit moved or that nothing
 *  was loaded. */
const tempSentence = (t: RotorStress['thermal']): string => {
  if (!tempsGiven(t) || !t) return '';
  const p = t.part_temps_c;
  const which = p
    ? `Magnets ${r0(p.magnet)}, core ${r0(p.rotor_core)}, shaft ${r0(p.shaft)} and sleeve ${r0(p.sleeve)} °C`
    : `Rotor ${r0(t.rotor_temp_c)} °C and sleeve ${r0(t.sleeve_temp_c)} °C`;
  const named = Object.keys(t.part_temps_given ?? {});
  const did = t.active
    ? ` Applied as the band's fit change only (${t.applied_as ?? 'band fit'}); every part is solved as drawn.`
    : ` Not a load here${t.applied_as ? ` (${t.applied_as})` : ''}: a temperature only changes a retaining band's fit pressure, so the parts are solved as drawn at ${r0(t.ref_temp_c)} °C.`;
  return ` ${which} against a ${r0(t.ref_temp_c)} °C reference.${did}${named.length
      ? ` ${named.join(', ')} came from the Thermal tab; the rest inherited the panel's fields.` : ''}`;
};

/** One number the user is meant to read, with the sentence that explains it. */
const Cell: React.FC<{
  value: string; unit?: string; sub?: string; colour?: string; tooltip?: string;
}> = ({ value, unit, sub, colour = 'var(--text-0)', tooltip }) => {
  const cell = (
    <Box sx={{ px: 1, py: 0.5, minWidth: 0 }}>
      <Typography sx={{
        fontSize: 14, fontWeight: 700, color: colour,
        fontFamily: 'monospace', lineHeight: 1.15,
      }}>
        {value}
        {unit && (
          <Typography component="span" sx={{ fontSize: 9, color: 'var(--text-4)', ml: 0.4, fontWeight: 400 }}>
            {unit}
          </Typography>
        )}
      </Typography>
      {sub && (
        // 11, not the 9 every other small print uses: this line carries the
        // safety factor now (user 2026-09-10, "SF 1.66 · 2500 MPa побольше
        // шрифт сделай"), and a margin is not a footnote.
        <Typography sx={{ fontSize: 11, color: 'var(--text-3)',
                          fontFamily: 'monospace', lineHeight: 1.25 }}>
          {sub}
        </Typography>
      )}
    </Box>
  );
  return tooltip ? <Tooltip title={tooltip} placement="top">{cell}</Tooltip> : cell;
};

/* The per-map caption (MapHead) is gone with the three side-by-side canvases:
   the shared viewer prints the selected output's name, range and headline
   number on ONE header line (2026-09-06). */

type Row = {
  key: string;
  label: string;
  tip: string;
  cell: (c: CaseResult, r: RotorStress) => React.ReactNode;
};

/** The safety factor a part is judged by — strength over the governing
 *  AVERAGED stress, which is the stress printed on the tile.  The two divide:
 *  the band's 2500 MPa over its 1559 MPa of averaged hoop IS its 1.60. */
function sfOf(c: CaseResult, part: string, p: PartResult): number | null {
  const s = c.sf_min_per_part?.[part];
  const v = s?.averaged ?? p.safety_factor ?? s?.p05 ?? s?.min;
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/** The tile's second line.  User 2026-09-10: "добавь ещё SF для каждого
 *  материала вместо процентов 69 % of 2500" — a per cent of a strength is one
 *  division away from the safety factor everything else on the page is judged
 *  by, and two ways of saying the same margin is one way too many.  The
 *  strength stays: it is what the SF was divided by. */
/** What the number on the tile IS, and what the other convention would say.
 *  Both tools every user of ours also runs offer the same pair, and naming
 *  them is what makes our number reproducible in theirs. */
function unavgTip(p: PartResult, unavg?: number | null): string {
  const g = p.governing_stress_mpa;
  const base = 'Averaged: each element value area-averaged onto the nodes of its own part — the ANSYS and Fusion default, and the field the map draws, so this number is the map\'s maximum.';
  const un = (typeof unavg === 'number' && Number.isFinite(unavg))
    ? ` Unaveraged (the raw element peak, what those tools show with averaging off): ${fmt(unavg)} MPa.`
    : '';
  const sf = (typeof g === 'number' && Number.isFinite(g))
    ? ` The safety factor divides the strength by ${fmt(g)} MPa.`
    : '';
  return `${base}${un}${sf}`;
}

function sfSub(c: CaseResult, part: string, p: PartResult): string {
  const sf = sfOf(c, part, p);
  const str = p.strength_mpa ? `${p.strength_mpa.toFixed(0)} MPa` : '';
  if (sf === null) return str;
  return str ? `SF ${sf.toFixed(2)} · ${str}` : `SF ${sf.toFixed(2)}`;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * The tab's own comparison table
 *
 * User 2026-09-07: *"сделай локальное сравнение … так же сделай в механике"* —
 * the Configure tab's stack, for rotor variants: press the blue button and this
 * answer becomes a row, so an interference fit or a sleeve temperature can be
 * traded off against the safety factors without leaving the tab.
 *
 * These are the COLUMNS; the rows are built by
 * `compare/resultRows.localMechanicalRow`, so this table and the permanent
 * Compare library read exactly the same keys.
 * ═══════════════════════════════════════════════════════════════════════════ */

/** The four joints as a cell: `sleeve_magnet:separation µ0.2 · …` is a true
 *  sentence and a 90-character column, so it is abbreviated here and left
 *  whole in the cell's tooltip. */
const shortContacts = (v: unknown): string => {
  if (typeof v !== 'string' || v === '') return '—';
  return v.split(' · ').map((s) => s
    .replace(/^sleeve_magnet:/, 's/m:').replace(/^sleeve_rotor:/, 's/r:')
    .replace(/^magnet_rotor:/, 'm/r:').replace(/^shaft_rotor:/, 'sh/r:')
    .replace('separation', 'sep').replace('bonded', 'bond')
    .replace('sliding', 'slide')).join(' · ');
};

/** What was SET.  Anything identical in every row collapses into the table's
 *  "same for all" line, which is why there can be this many. */
const MECH_INPUT_COLS: ColumnDef[] = [
  { key: 'case', label: 'Case', kind: 'input' },
  { key: 'case_mode', label: 'Mode', kind: 'input' },
  { key: 'rpm', label: 'rpm', d: 0, kind: 'input' },
  { key: 'rpm_overspeed', label: 'Overspeed', unit: 'rpm', d: 0, kind: 'input' },
  { key: 'loads', label: 'Loads', kind: 'input' },
  { key: 'torque_nm', label: 'T', unit: 'N·m', d: 1, kind: 'input' },
  { key: 'interference_mm', label: 'Interference', unit: 'mm', d: 3, kind: 'input' },
  { key: 'interference_eff_mm', label: 'Fit at T', unit: 'mm', d: 3, kind: 'input' },
  // One column per solid (2026-09-08): the whole point of coupling to Thermal
  // is that these four are NOT the same number, so a row that showed one of
  // them could not say which variant ran hotter where.  `rotor_c` keeps its key
  // — it is the core, and rows stacked before today already carry it — and only
  // its label says so; the two new ones are the parts that used to be silently
  // stretched onto it.  Anything identical in every row collapses into the
  // table's "same for all" line, so four columns is not four columns of noise.
  { key: 'rotor_c', label: 'Core', unit: '°C', d: 0, kind: 'input' },
  { key: 'magnet_c', label: 'Magnet', unit: '°C', d: 0, kind: 'input' },
  { key: 'shaft_c', label: 'Shaft', unit: '°C', d: 0, kind: 'input' },
  { key: 'sleeve_c', label: 'Sleeve', unit: '°C', d: 0, kind: 'input' },
  { key: 'mesh_mm', label: 'Mesh', unit: 'mm', d: 2, kind: 'input' },
  { key: 'sleeve_mat', label: 'Sleeve mat', kind: 'input' },
  { key: 'contacts', label: 'Contacts', kind: 'input', fmt: shortContacts },
];

/** What came OUT.  The safety factors want to be high, every stress and every
 *  open fraction low; the two frequency answers want to be far above the
 *  running speed, which is "high" here. */
const MECH_RESULT_COLS: ColumnDef[] = [
  { key: 'sf_sleeve', label: 'SF sleeve', d: 2, better: 'hi', kind: 'result' },
  { key: 'sleeve_hoop_p995_mpa', label: 'Sleeve σθ', unit: 'MPa', d: 0,
    better: 'lo', kind: 'result' },
  { key: 'sf_iron', label: 'SF iron', d: 2, better: 'hi', kind: 'result' },
  { key: 'iron_vm_p995_mpa', label: 'Iron VM p99.5', unit: 'MPa', d: 0,
    better: 'lo', kind: 'result' },
  { key: 'magnet_s1_p995_mpa', label: 'Magnet σ1', unit: 'MPa', d: 0,
    better: 'lo', kind: 'result' },
  // Max first — it is the rub criterion — with the ring's mean beside it.
  { key: 'od_growth_um', label: 'OD growth max', unit: 'µm', d: 1, better: 'lo',
    kind: 'result' },
  { key: 'od_growth_mean_um', label: 'OD growth mean', unit: 'µm', d: 1,
    better: 'lo', kind: 'result' },
  { key: 'open_sleeve_magnet', label: 'Sleeve/magnet open', unit: '%', d: 1,
    better: 'lo', kind: 'result' },
  { key: 'open_sleeve_rotor', label: 'Sleeve/rotor open', unit: '%', d: 1,
    better: 'lo', kind: 'result' },
  { key: 'open_magnet_rotor', label: 'Magnet/iron open', unit: '%', d: 1,
    better: 'lo', kind: 'result' },
  // Three states, not two: "no verdict" is the solve that ran away, and it
  // must not print as "NOT held" (which would be a mechanical answer).
  { key: 'poles_held', label: 'Torque path', kind: 'result',
    fmt: (v) => (v === true ? 'held' : v === false ? 'NOT held' : '—') },
  { key: 'retention_verdict', label: 'Retention', kind: 'result',
    fmt: (v) => (typeof v === 'string' && v !== ''
      ? (v.length > 22 ? `${v.slice(0, 21)}…` : v) : '—') },
  { key: 'mode1_hz', label: 'Mode 1', unit: 'Hz', d: 0, better: 'hi', kind: 'result' },
  { key: 'mode2_hz', label: 'Mode 2', unit: 'Hz', d: 0, better: 'hi', kind: 'result' },
  { key: 'critical1_rpm', label: 'Critical 1', unit: 'rpm', d: 0, better: 'hi',
    kind: 'result' },
];

const MECH_COLS: ColumnDef[] = [...MECH_INPUT_COLS, ...MECH_RESULT_COLS];

const MechanicalPanel: React.FC = () => {
  const sleeveT = Number(useMotorStore((s) => (s.geometry as Record<string, unknown> | null)?.sleeve_thickness) ?? 0);
  const hasSleeveGeo = sleeveT > 0;
  // The store's own geometry is the live machine; reading it here is what makes
  // the staleness badge re-evaluate the moment the geometry changes, without a
  // round trip to the backend (the second witness is the backend's verdict on
  // the restored result — see `mechanicalStore.isStale`).
  const liveGeometry = useMotorStore((s) => s.geometry);

  const st = useMechanicalStore();
  const {
    cases, loads, torque, rpm, rpm1, osf, interf, rotorTempC, sleeveTempC,
    tempSource, thermalTemps,
    meshMm, contacts, viewCase,
    view, exagg, showContacts, sfLow, sfHigh, geom, geomBusy, geomErr,
  } = st;
  const single = cases === 'single';
  // The three-case mode is retired (2026-09-07): a store that still remembers
  // it is put back on the one speed the tab now has.
  const _setCases = st.set;
  useEffect(() => { if (cases !== 'single') _setCases('cases', 'single'); }, [cases, _setCases]);
  // Temperatures come from the Thermal tab only when asked AND fresh — and when
  // they do, it is ONE PER SOLID (2026-09-08), which is what the line below the
  // pickers prints.  The rule is `lib/mechThermalTemps`, shared with the store's
  // request builder so the line and the request cannot disagree.
  const fromThermal = thermalTempsInUse(tempSource, thermalTemps);
  const thermalLine = fromThermal ? thermalTempsLine(thermalTemps) : '';
  const thermalTip = thermalTempsTip(thermalTemps);
  const withTorque = loads !== 'centrifugal';
  /* The Electromagnetic tab's COIL temperature — named in the rotor field's tooltip
     as a starting point, never written into the field: it is the winding's
     number, not the rotor's, and the standing rule is that a physics value is
     read from where it was measured or left alone (2026-09-07). */
  const simCoilTemp = Number(readSimSetting('coilTemp', 0)) || 0;
  /* The Electromagnetic tab's "Coupled thermal" switch.  With it ON the loop
     solves the rotor stress at the temperatures of ITS thermal map, bypassing
     these fields — so the fields must show those temperatures and not a
     manual number typed hours ago (user 2026-09-08: "температуры должны быть
     согласованы, а у тебя здесь стоят старые значения").  Read on mount and
     on cross-tab storage changes, like the rpm above. */
  const [coupled, setCoupledView] = useState<boolean>(
    () => readSimSetting<unknown>('coupled', false) === true);
  useEffect(() => {
    const sync = () => setCoupledView(readSimSetting<unknown>('coupled', false) === true);
    window.addEventListener('storage', sync);
    window.addEventListener('focus', sync);
    return () => { window.removeEventListener('storage', sync); window.removeEventListener('focus', sync); };
  }, []);
  /* With the switch ON the speed and the torque are the Electromagnetic run's
     too (2026-09-09, user: "момент должен быть правильным, и электромагнитного,
     и обороты, и температуры"): the two boxes show that point, locked, and a
     Solve here sends it — the loop's own mechanical step solves the same one.
     Re-read on every render: the run changes on the other tab, and this panel
     re-mounts on the way back to it. */
  const cp = coupledPoint();
  const res = st.stress.data;
  const busy = st.stress.busy;
  const err = st.stress.err;
  // The action identities are stable for the store's lifetime (they are closures
  // created once by `create`), so they are safe effect dependencies — `st` as a
  // whole is not: it is a new object on every state change.
  const setPair = st.setContact;
  const setField = st.set;
  const hydrate = st.hydrate;
  // Coupled ON → the picker is "from Thermal" and the temperatures are re-read,
  // so what the fields show is what the loop's last rotor-stress step used.
  const refreshThermalTemps = st.refreshThermalTemps;
  // The rotor temperature comes from the Thermal solve whenever there is one
  // (user 2026-09-09: "температуру ротора ставить из Thermal, хотя ты её и не
  // используешь — но будешь использовать вместе с бандажом"): band or no
  // band, the picker goes to Thermal when the loop is on or a fresh Thermal
  // result appears; a manual pick survives until the next Thermal solve.
  const thermalFresh = !!thermalTemps && !thermalTemps.stale;
  useEffect(() => {
    if (!coupled && !thermalFresh) return;
    if (tempSource !== 'thermal') setField('tempSource', 'thermal');
    if (coupled) void refreshThermalTemps();
  }, [coupled, thermalFresh, tempSource, setField, refreshThermalTemps]);

  // Come back to whatever was on screen: the store first (a tab switch), then
  // the backend's persisted last result (a reload / API restart), then the bare
  // cross-section.  Nothing is SOLVED by this — `hydrate` is a read.
  // …and on EVERY mount, ask whether the backend has something newer — the
  // coupled loop files its own rotor-stress step as the last mechanical result,
  // and `hydrate` only ever runs once per session (2026-09-10).
  const refreshLast = st.refreshLast;
  useEffect(() => { void hydrate().then(() => refreshLast()); },
            [hydrate, refreshLast]);

  // The Electromagnetic tab may be edited while this tab is open; pick its rpm up
  // whenever this panel is (re)mounted rather than freezing the first read.
  useEffect(() => {
    const v = Number(readSimSetting('rpm', 0));
    if (v > 0 && !rpm) setField('rpm', String(v));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* ── is the shown result still this machine's? ──────────────────────────
     2026-09-06: "если были изменения текущей геометрии — нужно подсвечивать
     неактуальность текущего расчёта".  We do NOT re-solve: an expensive solve
     started by a geometry edit the user has not finished making is worse than a
     badge.  `liveGeometry` is in the dependency list so the badge appears the
     moment the machine changes. */
  const stale = useMemo(
    () => !!res && isStale(st.stress.geoSig, st.stress.backendStale),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [res, st.stress.geoSig, st.stress.backendStale, liveGeometry]);
  const staleNote = stale ? 'result is for a previous geometry — re-Solve' : null;
  const staleTip = st.stress.backendStale === true
    ? `The backend fingerprinted the machine this was solved on (${res?.geo_fingerprint ?? '—'}) and it is not the one loaded now. Everything below — stresses, lift-off speeds, the picture — describes the previous cross-section. Press Solve to recompute it for this machine.${st.stress.restoredAt ? ` Solved ${st.stress.restoredAt}.` : ''}`
    : 'The geometry changed after this result was solved, so the numbers and the picture below belong to the previous cross-section. Press Solve to recompute them for the machine currently loaded.';

  const solveStress = st.solveStress;
  const solve = useCallback(() => { void solveStress(hasSleeveGeo); },
                            [solveStress, hasSleeveGeo]);
  const solveLimitSpeed = st.solveLimitSpeed;
  const limitSpeed = useCallback(() => { void solveLimitSpeed(hasSleeveGeo); },
                                 [solveLimitSpeed, hasSleeveGeo]);
  // The History notice's Recompute (2026-09-22): the SAME request, `fresh:
  // true` — whichever of the two buttons produced the result on screen
  // (`res.limit_speed` present = the search, not a plain Solve).
  const recompute = useCallback(() => {
    if (res?.limit_speed) void solveLimitSpeed(hasSleeveGeo, true);
    else void solveStress(hasSleeveGeo, true);
  }, [solveStress, solveLimitSpeed, hasSleeveGeo, res?.limit_speed]);

  /* ── the mesh, as a thing you control ────────────────────────────────────
     User 2026-09-06: "по поводу сетки — как я понял, она строится отдельно, и
     ей тоже нужно как-то управлять".  It IS built separately (the mechanical
     mesher, not the EM one), so it gets its own button and its own line, and
     changing the size does NOT rebuild on its own — each build is seconds of
     gmsh.  What is on screen is what was built; the notes below say when that
     is no longer what the size field asks for. */
  const loadGeometry = st.loadGeometry;
  const buildMesh = useCallback(() => { void loadGeometry(); }, [loadGeometry]);
  const meshWanted = Number(meshMm);
  const meshOff = (built: number | undefined | null) =>
    built !== undefined && built !== null && Number.isFinite(meshWanted)
    && Math.abs(built - meshWanted) > 1e-6;
  const builtNote = meshOff(geom?.mesh_size_mm)
    ? `built at ${fmt(geom?.mesh_size_mm, 2)} mm` : null;
  const resultMeshNote = res && meshOff(res.mesh.mesh_size_mm)
    ? `result solved on a ${fmt(res.mesh.mesh_size_mm, 2)} mm mesh` : null;

  const rows: Row[] = useMemo(() => {
    const out: Row[] = [];
    const part = (c: CaseResult, name: string) => c.parts[name];

    // The sleeve rows exist only on a machine that HAS a sleeve (user
    // 2026-09-09: "выкинь всё про sleeve", when there is none).
    if (hasSleeveGeo) {
      out.push({
        key: 'sleeve_hoop', label: 'Sleeve hoop σθ',
        tip: 'Hoop (fibre-direction) stress in the retaining sleeve, and what fraction of its tensile strength that is. This is the burst check: it is the number that decides sleeve thickness.',
        cell: (c) => {
          const p = part(c, 'sleeve');
          if (!p) return <Cell value="—" tooltip="this result was solved without a sleeve" />;
          return <Cell value={fmt(p.hoop_max_mpa)} unit="MPa"
            sub={sfSub(c, 'sleeve', p)}
            colour={sfAccent(sfOf(c, 'sleeve', p))}
            tooltip={unavgTip(p, p.hoop_max_unaveraged_mpa)} />;
        },
      });
      out.push({
        key: 'sleeve_rad', label: 'Sleeve min σr',
        tip: 'Most compressive radial stress inside the sleeve. Negative is the sleeve squeezing the rotor — that squeeze is what holds the magnets. It shrinks as speed rises because the rotor grows into the sleeve.',
        cell: (c) => {
          const p = part(c, 'sleeve');
          return p ? <Cell value={fmt(p.radial_min_mpa)} unit="MPa" /> : <Cell value="—" />;
        },
      });
    }
    out.push({
      key: 'mag', label: 'Magnet max tensile',
      tip: 'Largest tensile principal stress in any magnet, against the magnet\'s TENSILE strength. Sintered NdFeB does not yield — it cracks — so tension is the check, and it is ~12× weaker in tension than in compression.',
      cell: (c) => {
        const p = part(c, 'magnet');
        if (!p) return <Cell value="—" />;
        return <Cell value={fmt(p.principal_max_mpa)} unit="MPa"
          sub={sfSub(c, 'magnet', p)}
          colour={sfAccent(sfOf(c, 'magnet', p))}
          tooltip={`${unavgTip(p, p.principal_max_unaveraged_mpa)} p99.5 of the averaged field = ${fmt(p.principal_max_p995_mpa)} MPa.`} />;
      },
    });
    out.push({
      key: 'iron', label: 'Rotor iron von Mises',
      tip: 'Peak von Mises in the rotor lamination against its yield. The peak normally sits at a bridge root, which is a stress singularity in a sharp-cornered model — read the p99.5 in the tooltip alongside it.',
      cell: (c) => {
        const p = part(c, 'rotor');
        if (!p) return <Cell value="—" />;
        return <Cell value={fmt(p.von_mises_max_mpa)} unit="MPa"
          sub={sfSub(c, 'rotor', p)}
          colour={sfAccent(sfOf(c, 'rotor', p))}
          tooltip={`${unavgTip(p, p.von_mises_max_unaveraged_mpa)} p99.5 of the averaged field = ${fmt(p.von_mises_p995_mpa)} MPa. ${c.sf_min_per_part?.rotor?.criterion ?? ''}`} />;
      },
    });
    out.push({
      key: 'shaft', label: 'Shaft von Mises',
      tip: 'Peak von Mises in the shaft tube against its yield.',
      cell: (c) => {
        const p = part(c, 'shaft');
        if (!p) return <Cell value="—" />;
        return <Cell value={fmt(p.von_mises_max_mpa)} unit="MPa"
          sub={sfSub(c, 'shaft', p)}
          colour={sfAccent(sfOf(c, 'shaft', p))}
          tooltip={unavgTip(p, p.von_mises_max_unaveraged_mpa)} />;
      },
    });
    const ifaceRow = (key: string, label: string, pair: string, tip: string) => ({
      key, label, tip,
      cell: (c: CaseResult) => {
        const i = c.interfaces?.[pair];
        if (!i) return <Cell value="—" tooltip="these two parts do not touch in this cross-section" />;
        const open = i.open_fraction * 100;
        // The tangential half is only meaningful once something is pushing
        // sideways, but it is always TRUE, so it is always in the tooltip.
        const tang = i.slip_fraction === undefined ? '' :
          ` Tangentially: ${((i.stick_fraction ?? 0) * 100).toFixed(0)} % stuck, ${((i.slip_fraction ?? 0) * 100).toFixed(0)} % sliding, largest tangential motion ${fmt(i.slip_max_um, 2)} µm. It carried ${fmt(i.torque_transmitted_nm, 1)} N·m; Coulomb would let it pass at most ${fmt(i.friction_capacity_nm, 0)} N·m (µ·∫p·r·dA). An OPEN facet and a SLIDING one both transmit nothing the joint was asked for — only the stuck share does.`;
        return <Cell value={`${open.toFixed(0)} %`}
          sub={`p̄ ${fmt(i.pressure_mean_mpa)} MPa`}
          colour={i.lift_off ? '#f87171' : (open > 5 ? '#fbbf24' : '#4ade80')}
          tooltip={`${i.type}${i.mu ? `, µ ${i.mu}` : ''} · ${open.toFixed(1)} % of the ${fmt(i.length_mm, 1)} mm interface is OPEN, largest gap ${fmt(i.gap_max_um, 2)} µm. Contact pressure ${fmt(i.pressure_min_mpa)} … ${fmt(i.pressure_max_mpa)} MPa (a negative value is tension a bonded tie is holding). Net radial force ${fmt(i.radial_force_kn_per_m, 0)} kN per metre of stack. Recovered σn across the facets: ${fmt(i.normal_min_mpa)} … ${fmt(i.normal_max_mpa)} MPa, p95 ${fmt(i.normal_p95_mpa)}.${tang}`} />;
      },
    });
    out.push(ifaceRow('iface', 'Magnet/iron open', 'magnet_rotor',
      'How much of the magnet-to-iron interface has OPENED, and the mean contact pressure on what is left. With a Separation contact the pocket can let go of the magnet — this is the number that says whether it has.'));
    if (hasSleeveGeo) {
      out.push(ifaceRow('sleeve_iface', 'Sleeve/rotor open', 'sleeve_rotor',
        'How much of the sleeve-to-rotor interface has OPENED. Some of it is open at standstill by geometry alone wherever the rotor OD dips away from the sleeve; what matters is how much more opens with speed.'));
      out.push(ifaceRow('sleeve_mag_iface', 'Sleeve/magnet open', 'sleeve_magnet',
        'The magnet faces directly under the sleeve, where the design has any. If this row is blank the magnets never touch the sleeve and something else is retaining them — read the verdict below.'));
    }
    out.push({
      key: 'retention', label: 'Magnet retention',
      tip: 'WHICH surface is carrying the magnets, and how much of their centrifugal load it takes. This is the answer the Separation contact was added for: in a bonded model the magnets hang on the iron in tension, which no glue joint does.',
      cell: (c) => {
        const r = c.magnet_retention;
        if (!r || !r.verdict) return <Cell value="—" />;
        const sh = r.share ?? {};
        const parts = Object.entries(sh)
          .filter(([, v]) => v !== null && v !== undefined)
          .map(([k, v]) => `${k}: ${((v as number) * 100).toFixed(0)} %`)
          .join(' · ');
        // The tile names the surface; the sentence (travel, seating) is the
        // tooltip's (user 2026-09-09: "не надо всё это расписывать").
        const head = r.verdict.split(' — ')[0].trim();
        return <Cell value={head} colour={r.verdict.startsWith('nothing') ? '#f87171' : 'var(--text-0)'}
          sub={r.magnet_centrifugal_kn_per_m ? `${fmt(r.magnet_centrifugal_kn_per_m, 0)} kN/m` : ''}
          tooltip={`${r.verdict}. Magnet centrifugal load ${fmt(r.magnet_centrifugal_kn_per_m, 0)} kN per metre of stack. Share of it carried — ${parts || 'nothing at this speed'}. A share above 100 % means the surface also has to resist the magnet's own stretch, not only carry its weight.`} />;
      },
    });
    /* ── can the torque get out of the poles? ────────────────────────────
       User 2026-09-07.  The spoke rotor's iron bridges are assembly features
       that yield on the first spin-up, after which each pole is held
       tangentially by friction alone — this row is the answer to whether that
       works, and the reaction beside it is the proof the answer was solved. */
    out.push({
      key: 'torque_path', label: 'Torque path',
      tip: 'Whether the electromagnetic torque can cross the contacts between the pole tops and the hub. Graded on the WEAKEST clamped separation joint: how much torque its µ·p could pass against how much is applied. Blank when no torque was applied.',
      cell: (c) => {
        const tp = c.torque_path;
        if (!tp) return <Cell value="—" tooltip="no torque was applied — pick Torque or Both in the load menu" />;
        const react = c.torque_reaction_nm;
        const bal = c.torque_balance;
        // `held` is null for two different sentences: "no verdict" (the solve
        // did not settle) and, since 2026-09-09, "poles held by the pocket
        // walls" — µ = 0, the torque crosses as normal pressure, no friction
        // margin to grade.  The second is a yes.
        const formLocked = tp.held === null && tp.verdict.startsWith('poles held by');
        const colour = formLocked ? '#4ade80' : tp.held === null ? '#fbbf24'
          : (tp.held ? '#4ade80' : '#f87171');
        const head = formLocked ? 'held · walls' : tp.held === null ? 'no verdict'
          : (tp.held ? `held · ${fmt(tp.margin, 1)}×` : 'NOT held');
        return <Cell value={head} colour={colour}
          sub={react === null || react === undefined ? ''
            : `reaction ${fmt(react, 1)} N·m`}
          tooltip={`${tp.verdict}. The bore reacted ${fmt(react, 3)} N·m against ${fmt(tp.applied_nm, 1)} applied (balance ${fmt(bal, 6)}); that is a constraint identity, so anything but 1.000000 means a piece of the rotor is held by nothing and the solve ran away — which is why there is no verdict in that state. Largest tangential motion anywhere on a separation joint: ${fmt(tp.slip_max_um, 1)} µm.`} />;
      },
    });
    out.push({
      key: 'od', label: 'Rotor OD growth',
      tip: 'Radial growth of the rotor outer surface — this comes straight off the air gap. Check it against the mechanical clearance before anything else on this page.',
      // max on the line, mean under it: the maximum is the rub criterion, the
      // mean says how much of it is the whole ring growing (user 2026-09-10:
      // "нужно ещё считать максимальное радиальное смещение верха бандажа как
      // отдельное число в таблице").
      cell: (c) => {
        const g = c.od_growth;
        return <Cell value={fmt(g ? g.max_um : c.rotor_od_growth_um, 2)} unit="µm"
          sub={g ? `mean ${fmt(g.mean_um, 1)} · ${g.part} OD` : ''}
          tooltip={g
            ? `Largest radial displacement of the ${g.part} outer surface — the top of the band when there is one — read on the ${g.n_nodes} nodes at r ${fmt(g.r_mm, 1)} mm. Max ${fmt(g.max_um, 1)} µm, mean ${fmt(g.mean_um, 1)} µm, least ${fmt(g.min_um, 1)} µm: the max is what closes the mechanical clearance (air gap minus the band) and decides whether the rotor rubs, and the spread is the lobing the magnets push into the band between the poles.`
            : undefined} />;
      },
    });
    out.push({
      key: 'gap', label: 'Air gap left',
      tip: 'What is left of the cold clearance between the rotor and the stator bore once the rotor has grown into it at speed. Manufacturing tolerance, bearing clearance and shaft whirl are NOT in it — they come off this number.',
      cell: (c) => {
        const g = c.air_gap;
        if (!g) return <Cell value="—" tooltip="this result carries no clearance: the stator was not part of the section handed to the solve" />;
        const frac = g.remaining_um / Math.max(g.clearance_um, 1e-9);
        return <Cell value={fmt(g.remaining_um, 0)} unit="µm"
          colour={frac < 0.15 ? '#f87171' : frac < 0.35 ? '#fbbf24' : '#4ade80'}
          sub={`of ${fmt(g.clearance_um, 0)} · ${fmt(g.closed_pct, 0)} % used`}
          tooltip={`Cold clearance ${fmt(g.clearance_um, 1)} µm — the stator bore at r ${fmt(g.bore_r_mm, 2)} mm against the rotor's outermost surface at r ${fmt(g.rotor_r_mm, 2)} mm, both read off the drawn section. The rotor closes ${fmt(g.closed_um, 1)} µm of it at this speed, leaving ${fmt(g.remaining_um, 1)} µm. Tolerances, bearing clearance and whirl come off that remainder.`} />;
      },
    });
    return out;
  }, []);

  const liftOffText = (key: string): string => {
    const v = res?.lift_off_rpm?.[key];
    if (v === null || v === undefined) return '—';
    if (v <= 0) return 'from 0 rpm';
    return `${Math.round(v).toLocaleString()} rpm`;
  };

  /* Which cases this RESULT has, read off the answer instead of assumed.
     2026-09-06: a single-speed result has exactly one, named by its speed, and
     the toggle can be flipped after a solve — so the table has to follow the
     result on screen, not the toggle. */
  const resCases: CaseName[] = useMemo(() => (res ? caseKeys(res) : []), [res]);
  /** the case the one-liners are quoted from: `rated`, or the single speed */
  const mainCase = res
    ? ((res.primary_case && res.cases[res.primary_case])
        ? res.primary_case : (resCases[0] ?? ''))
    : '';
  const totalMass = res && res.cases[mainCase]
    ? Object.values(res.cases[mainCase].parts)
        .reduce((a, p) => a + (p.mass_kg ?? 0), 0)
    : 0;

  /* ── a part that came loose and was SEATED (2026-09-09) ───────────────────
     The pocket grows more than the magnet does at temperature, so the magnet
     goes free by microns and then travels onto its lip — "магнит должен сесть
     на язычок, как в Fusion". ONE line, the travel in it (that is the number
     the user compares with Fusion's), the rest in the tooltip. Quoted from the
     case the other one-liners are quoted from, and worst-first so a rotor whose
     magnets moved by different amounts leads with the largest. */
  const seatedNote = useMemo(() => {
    const seated = (res && res.cases[mainCase]?.contact.seated) ?? [];
    if (!seated.length) return null;
    const byTravel = [...seated].sort((a, b) => b.travel_um - a.travel_um);
    const worst = byTravel[0]!;
    const where = SEAT_SURFACE[worst.landed_on] ?? worst.landed_on;
    const noun = PART_LABEL[worst.part] ?? worst.part;
    const what = seated.length === 1 ? `the ${noun}` : `${seated.length} ${noun}s`;
    return {
      line: `${what} seated on ${where} — ${worst.travel_um.toFixed(0)} µm of travel`,
      detail: `The separation contact left ${what} with every pair open — the usual `
        + 'reason is temperature, the iron pocket growing faster than the magnet does. '
        + 'Instead of being pinned where it floated (which used to run the solve away), '
        + 'it was TRAVELLED along its own net load until it landed: '
        + `${byTravel.map((s) => `${s.travel_um.toFixed(1)} µm`).join(', ')}, onto ${where}. `
        + 'Its stresses are contact stresses from there on — the pairs it landed on carry '
        + 'compression, the side walls stay open. Compare the travel with Fusion’s.',
    };
  }, [res, mainCase]);

  /* ── the tab's own comparison stack ──────────────────────────────────────
     The rows live in the store (so they survive leaving the tab) and are
     persisted with the tab's other fields (so they survive a reload and
     another browser); the Compare tab remains the permanent library.

     Read through `getState()` rather than the render's closure: the button
     that calls these is a child that re-renders on its own schedule, and a
     stale `compareRows` here would drop whatever was stacked meanwhile. */
  const compareRows = st.compareRows;
  const addLocal = useCallback(() => {
    const s = useMechanicalStore.getState();
    if (s.compareRows.length >= MAX_LOCAL_ROWS) {
      throw new Error(`the comparison below already holds ${MAX_LOCAL_ROWS} `
        + 'variants — remove one before adding another');
    }
    // The modal and critical-speed answers are separate solves on this tab:
    // they ride along only when they belong to THIS machine, and their absence
    // is an empty column, not a wrong one.  Same guard the Compare row uses.
    const modal = s.modal.data && !isStale(s.modal.geoSig, s.modal.backendStale)
      ? s.modal.data : null;
    const rotordyn = s.rotordyn.data
      && !isStale(s.rotordyn.geoSig, s.rotordyn.backendStale) ? s.rotordyn.data : null;
    const { inputs, results } = localMechanicalRow(s.stress.data, modal, rotordyn,
                                                   s, s.viewCase);
    const now = new Date();
    s.set('compareRows', [...s.compareRows, {
      id: `m${now.getTime().toString(36)}${Math.random().toString(36).slice(2, 6)}`,
      name: `#${s.compareRows.length + 1} · ${now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`,
      at: now.toISOString(), inputs, results,
    }]);
  }, []);
  const removeLocal = useCallback((id: string) => {
    const s = useMechanicalStore.getState();
    s.set('compareRows', s.compareRows.filter((r) => r.id !== id));
  }, []);
  const clearLocal = useCallback(() => {
    useMechanicalStore.getState().set('compareRows', []);
  }, []);
  const renameLocal = useCallback((id: string, name: string) => {
    const s = useMechanicalStore.getState();
    s.set('compareRows', s.compareRows.map((r) => (r.id === id ? { ...r, name } : r)));
  }, []);

  /* Rendered ONCE — under the case table when there is a result, on its own
     when there is not (a stack that survived a reload must not be invisible
     until something is solved again). */
  const localTable = (res || compareRows.length > 0) ? (
    <Paper sx={{ p: 1.25, mb: 1.5, bgcolor: 'var(--panel)' }}>
      <LocalCompareTable title="Local comparison" rows={compareRows}
        columns={MECH_COLS} onRemove={removeLocal} onClear={clearLocal}
        onRename={renameLocal}
        emptyHint={<>Press <b>Add to comparison</b> above to stack rotor variants here — what differs becomes the columns.</>} />
    </Paper>
  ) : null;

  return (
    <Box sx={{ height: '100%', overflow: 'auto', p: 1.5 }}>
      {/* Live solve progress — the same strip the Electromagnetic tab has, pinned to
          the very top of THIS scroller (sticky only works inside the element
          that scrolls), so a running contact solve is visible without scrolling
          back up.  Renders nothing while idle. */}
      <SolveProgressStrip endpoint="/api/mechanical/progress" unit="steps"
        kindLabels={{
          rotor_stress: 'Rotor stress', modes: 'Modal analysis',
          critical_speeds: 'Critical speeds', mesh: 'Mesh build',
          limit_speed: 'Limit speed',
        }} />

      {/* ── controls ──────────────────────────────────────────────────── */}
      <Paper sx={{ p: 1.25, mb: 1.5, bgcolor: 'var(--panel)' }}>
        <Box sx={{ display: 'flex', gap: 1.25, alignItems: 'center', flexWrap: 'wrap' }}>
          {/* ONE speed, always (user 2026-09-07: "давай делать только одно
              значение скорости для моделирования — выкинь это вообще"): the
              proof rpm on the right is the one case a Solve computes; anything
              slower is less loaded and covered by it. */}
          {single ? (
            <Tooltip title={cp.rpm !== null
              ? `Coupled thermal is ON (Electromagnetic tab): the rotor is solved at the speed of the run the loop makes — that tab's ${cp.rpm.toLocaleString()} rpm — so this box shows it and is not editable. Switch the coupling off to solve a proof speed of your own.`
              : rpm1.trim() === ''
              ? `The speed this solve runs at: the Electromagnetic tab's ${effectiveProofRpm(rpm1).toLocaleString()} rpm — the operating point always comes from there. Type a number here to solve a proof speed of your own instead (stress goes with the square of speed, so a rotor that passes at a higher speed passes below it); clear it to follow the Electromagnetic tab again.`
              : `A proof speed you typed. The Electromagnetic tab's rpm is ${effectiveProofRpm('').toLocaleString()}; clear the box to solve at that instead.`}>
              <TextField label={cp.rpm !== null ? 'rpm · coupled' : (rpm1.trim() === '' ? 'rpm' : 'proof rpm')} size="small"
                value={cp.rpm !== null ? String(cp.rpm) : (rpm1.trim() === '' ? String(effectiveProofRpm(rpm1) || '') : rpm1)}
                disabled={cp.rpm !== null}
                onChange={(e) => setField('rpm1', e.target.value)}
                sx={{ width: 110 }} inputProps={{ style: { fontSize: 12 } }}
                InputLabelProps={{ style: { fontSize: 12 } }} />
            </Tooltip>
          ) : (
            <>
              <Tooltip title="Rated speed. Defaults to the rpm set on the Electromagnetic tab — the operating point always comes from there.">
                <TextField label="rpm" size="small" value={rpm} onChange={(e) => setField('rpm', e.target.value)}
                  sx={{ width: 110 }} inputProps={{ style: { fontSize: 12 } }}
                  InputLabelProps={{ style: { fontSize: 12 } }} />
              </Tooltip>
              <Tooltip title="Overspeed factor for the proof case. Stress scales with the square of speed, so ×1.2 is +44 % stress.">
                <TextField label="overspeed ×" size="small" value={osf} onChange={(e) => setField('osf', e.target.value)}
                  sx={{ width: 105 }} inputProps={{ style: { fontSize: 12 } }}
                  InputLabelProps={{ style: { fontSize: 12 } }} />
              </Tooltip>
            </>
          )}
          {/* ── which forces act ───────────────────────────────────────────
              User 2026-09-07: "добавь ещё и момент на ротор, пусть действуют
              все силы; сделай меню, чтобы можно было выбрать центробежную,
              момент и обе."  One menu, one field, tooltips for the rest. */}
          <Tooltip title="Which forces act. Centrifugal: ρω²r only, the bore free — the model this tab started as. Torque: the electromagnetic torque alone, applied as a uniform tangential traction τ = T/(2πr²L) on the rotor iron and magnet tops facing the air gap (never on the sleeve — a carbon band carries no EM force), and reacted at the SHAFT BORE, which is held tangentially and left free radially. Both: the machine. The same T is applied at every speed, standstill included — the torque is the machine's, not the speed's, and a stalled motor is the worst case for the friction path because there is no centrifugal clamp yet." slotProps={SELECT_TIP}>
            <Select size="small" value={loads}
              onChange={(e) => setField('loads', e.target.value as LoadsMode)}
              sx={{ fontSize: 11, height: 30, minWidth: 110 }}>
              {(['centrifugal', 'torque', 'both'] as LoadsMode[]).map((l) => (
                <MenuItem key={l} value={l} sx={{ fontSize: 11 }}>{LOADS_LABEL[l]}</MenuItem>
              ))}
            </Select>
          </Tooltip>
          {withTorque && (
            <Tooltip title={cp.torque !== null
              ? `Coupled thermal is ON (Electromagnetic tab): the torque is the one the last run there produced, ${cp.torque.toFixed(2)} N·m, so this box shows it and is not editable. Switch the coupling off to type a torque of your own.`
              : cp.on
              ? 'Coupled thermal is ON but nothing has been run on the Electromagnetic tab yet: leave this empty and the backend takes that run\'s mean torque once it exists.'
              : "Electromagnetic torque, N·m. Defaults to the mean torque of the last run on the Electromagnetic tab — the operating point always comes from there — and it is yours to edit. Leave it empty and the backend reads that run itself. Positive is motoring; a negative value is braking and simply flips the traction."}>
              <TextField label={cp.torque !== null ? 'T N·m · coupled' : 'T N·m'} size="small"
                value={cp.torque !== null ? (Math.round(cp.torque * 100) / 100).toString() : torque}
                disabled={cp.torque !== null}
                onChange={(e) => setField('torque', e.target.value)}
                sx={{ width: 95 }} inputProps={{ style: { fontSize: 12 } }}
                InputLabelProps={{ style: { fontSize: 12 } }} />
            </Tooltip>
          )}
          <Tooltip title={hasSleeveGeo
            ? 'Radial interference of the sleeve fit, in mm. It pre-compresses the rotor at standstill, which is what keeps the magnets pressed on at speed.'
            : 'This machine has no sleeve (sleeve_thickness = 0), so there is no fit to specify.'}>
            <span>
              <TextField label="interference mm" size="small" value={interf} disabled={!hasSleeveGeo}
                onChange={(e) => setField('interf', e.target.value)} sx={{ width: 130 }}
                inputProps={{ style: { fontSize: 12 } }} InputLabelProps={{ style: { fontSize: 12 } }} />
            </span>
          </Tooltip>
          {/* ── the rotor temperature ─────────────────────────────────────
              User 2026-09-07: "нужно универсально добавить температуру ротора,
              чтобы можно было задавать; для моторов без бандажа этот эффект
              вообще минимальный".  Two fields, one line, tooltips for the
              rest — the project's no-walls-of-text rule. */}
          <Tooltip title={`Rotor core, magnet and shaft temperature, °C — one number for the three, which is what MANUAL means; switch the picker to "from Thermal" and each of them takes its own. ${REF_TEMP_C} °C = the reference the material library is quoted at, i.e. NO thermal load and the machine exactly as drawn. Heat it and the iron grows at 12 ppm/K under a band whose fibre-direction CTE is about zero, so the fit — and the sleeve hoop stress — get TIGHTER; the effective interference is reported below. Without a band a uniformly heated rotor only carries the small iron/magnet mismatch, which is why this hardly moves a sleeveless machine. Not seeded from the Electromagnetic tab: what that tab carries is the COIL temperature (the winding${simCoilTemp ? `, currently ${simCoilTemp} °C` : ''}), which is not the rotor's.`}>
            <TextField label={coupled && fromThermal ? 'rotor °C · coupled' : 'rotor °C'} size="small"
              value={fromThermal && thermalTemps?.rotor.avg !== null ? fmt(thermalTemps?.rotor.avg, 0) : rotorTempC}
              disabled={fromThermal}
              onChange={(e) => setField('rotorTempC', e.target.value)}
              sx={{ width: 92 }} inputProps={{ style: { fontSize: 12 } }}
              InputLabelProps={{ style: { fontSize: 12 } }} />
          </Tooltip>
          {/* Where the temperatures come from (user 2026-09-07), and since
              2026-09-08 how many of them: "в механический расчёт тоже нужно
              делать каплинг, чтобы температуры везде были одинаковы". */}
          <Tooltip title={coupled
            ? `Coupled thermal is ON (Electromagnetic tab): the loop solves the rotor stress at the temperatures of its own Thermal map, so the source is locked to "from Thermal" and the fields show what the last coupled step used. ${thermalTip}`
            : thermalTemps
            ? `Manual: the two fields. Thermal: ONE temperature per solid — magnets, core, shaft and sleeve — from the last Thermal-tab result. ${thermalTip}`
            : 'Manual: the two fields. Thermal: not available — nothing has been solved on the Thermal tab yet.'}>
            <Select size="small" value={tempSource} disabled={coupled}
              onChange={(e) => { const v = e.target.value as 'manual' | 'thermal'; setField('tempSource', v); if (v === 'thermal') void st.refreshThermalTemps(); }}
              sx={{ fontSize: 11, height: 30, minWidth: 118 }}>
              <MenuItem value="manual" sx={{ fontSize: 11 }}>°C manual</MenuItem>
              <MenuItem value="thermal" sx={{ fontSize: 11 }} disabled={!thermalTemps}>°C from Thermal</MenuItem>
            </Select>
          </Tooltip>
          {/* No band → nothing about a band on this tab (user 2026-09-09:
              "если бандажа нет, не надо ничего писать про него"). */}
          {hasSleeveGeo && (
            <Tooltip title={`Retaining-sleeve temperature, °C. Its own field because on a real machine it is not the rotor's number — the iron carries the loss, the band sits on the outside in the gap draught — and the DIFFERENCE between the two is what moves the fit. ${REF_TEMP_C} °C = no thermal load on the band.`}>
              <span>
                <TextField label={coupled && fromThermal ? 'sleeve °C · coupled' : 'sleeve °C'} size="small"
                  value={fromThermal && thermalTemps?.sleeve.avg !== null ? fmt(thermalTemps?.sleeve.avg, 0) : sleeveTempC}
                  disabled={fromThermal}
                  onChange={(e) => setField('sleeveTempC', e.target.value)}
                  sx={{ width: 96 }} inputProps={{ style: { fontSize: 12 } }}
                  InputLabelProps={{ style: { fontSize: 12 } }} />
              </span>
            </Tooltip>
          )}
          {/* ── what a coupled Solve will actually send ────────────────────
              ONE short line, tooltip for everything else (the no-walls-of-text
              rule), placed after the two fields it replaces so the temperature
              cluster reads as one thing.  User 2026-09-08: "в механический
              расчёт тоже нужно делать каплинг, чтобы температуры везде были
              одинаковы". */}
          {thermalLine && (
            <Tooltip title={thermalTip}>
              <Typography sx={{ ...lbl, cursor: 'help', borderBottom: '1px dotted var(--text-4)' }}>
                {thermalLine}
              </Typography>
            </Tooltip>
          )}
          {tempSource === 'thermal' && (
            <Tooltip title="Re-read the last Thermal result. It is read on every mount of this tab, so this is only needed after re-solving Thermal without leaving Mechanical.">
              <Typography component="span" role="button" tabIndex={0}
                onClick={() => { void st.refreshThermalTemps(); }}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); void st.refreshThermalTemps(); } }}
                sx={{ ...lbl, cursor: 'pointer', userSelect: 'none',
                      '&:hover': { color: 'var(--text-1)' } }}>
                ↻
              </Typography>
            </Tooltip>
          )}
          {tempSource === 'thermal' && thermalTemps?.stale && (
            <Tooltip title="The last Thermal result was solved on a different geometry than the one loaded now; the manual temperatures are used until Thermal is re-solved.">
              <Typography sx={{ ...warn }}>⚠ Thermal result stale — manual °C used</Typography>
            </Tooltip>
          )}
          <Button variant="contained" size="small" onClick={solve} disabled={busy}
            startIcon={busy ? <CircularProgress size={13} color="inherit" /> : undefined}>
            {busy ? 'Solving' : 'Solve'}
          </Button>
          <SolveTimer busy={busy} startedAt={st.stress.startedAt} est={st.est.stress}
            what="stress solve" />
          {/* ── Limit speed (SF = 1) ────────────────────────────────────────
              Owner 2026-09-21: "нужно искать ещё максимальную скорость
              вращения, на всякий случай — она будет, когда достигает SF = 1".
              Same case as Solve — same torque, contacts, interference,
              temperatures, mesh — swept in rpm by the backend search; the
              answer lands in the SAME slice Solve fills (`res.limit_speed`),
              so it reads the fields above it exactly as Solve does. */}
          <Tooltip title="Bisects the speed at which the minimum averaged safety factor (the same SF the tiles below print) reaches 1 — the rotor's structural limit — with everything else held exactly as this case: the same torque (not scaled with speed), contacts, interference, temperatures, mesh and element order. Not a burst test: it is the same FEM model this Solve uses, evaluated at a handful of other speeds.">
            <span>
              <Button variant="outlined" size="small" onClick={limitSpeed} disabled={busy}
                startIcon={busy ? <CircularProgress size={13} color="inherit" /> : undefined}>
                Limit speed (SF = 1)
              </Button>
            </span>
          </Tooltip>
          {res?.limit_speed && (
            <Tooltip title={`Searched from ${Math.round(res.limit_speed.analysed_rpm).toLocaleString()} rpm (SF ${res.limit_speed.sf_at_rpm0.toFixed(2)} there) toward SF = ${res.limit_speed.target_sf.toFixed(2)}, ${res.limit_speed.loads} loads, ${fmt(res.limit_speed.torque_nm, 0)} N·m held constant at every speed tried. ${res.limit_speed.n_solves} solve(s), bracket ${res.limit_speed.bracket ? `${Math.round(res.limit_speed.bracket[0]).toLocaleString()}–${Math.round(res.limit_speed.bracket[1]).toLocaleString()} rpm` : 'none'}. Pure ω² cross-check (centrifugal-only, sanity read, not the answer): ${Math.round(res.limit_speed.omega2_extrapolation_rpm).toLocaleString()} rpm. ${res.limit_speed.note}`}>
              <Typography sx={{ ...(res.limit_speed.reached ? lbl : warn), cursor: 'help' }}>
                {res.limit_speed.reached
                  ? `Limit speed: ${Math.round(res.limit_speed.rpm_sf1 ?? 0).toLocaleString()} rpm — ${
                      PART_LABEL[res.limit_speed.limiting_part ?? ''] ?? res.limit_speed.limiting_part ?? '—'
                    }, SF ${res.limit_speed.sf_at_rpm0.toFixed(2)} at ${Math.round(res.limit_speed.analysed_rpm).toLocaleString()}`
                  : `Limit speed: not reached within the searched range (SF ${res.limit_speed.sf_at_rpm0.toFixed(2)} at ${Math.round(res.limit_speed.analysed_rpm).toLocaleString()} rpm)`}
              </Typography>
            </Tooltip>
          )}
          {/* ONE short line, tooltip for the rest — the project's no-walls-of-
              text rule.  User 2026-09-06: "нужно подсвечивать неактуальность
              текущего расчёта". */}
          {staleNote && (
            <Tooltip title={staleTip}>
              <Typography sx={{ ...lbl, color: '#fbbf24', fontWeight: 700,
                cursor: 'help', borderBottom: '1px dotted #fbbf24' }}>
                ⚠ {staleNote}
              </Typography>
            </Tooltip>
          )}
          {/* "Loaded from history — computed …" (2026-09-22, owner: a repeat
              launch of the same parameters must say so, not solve again).
              One line, Recompute a click away — the project's no-walls-of-
              text rule, same shape as staleNote just above. */}
          {!staleNote && historyNoticeFor(res) && (
            <Typography sx={{ ...lbl, color: '#93c5fd' }}>
              {historyNoticeFor(res)!.text}
              {' · '}
              <Typography component="span" onClick={recompute}
                sx={{ ...lbl, color: '#93c5fd', textDecoration: 'underline',
                     cursor: 'pointer' }}>
                Recompute
              </Typography>
            </Typography>
          )}
          {res && (
            /* The context line: what was solved, on what, and how long it took
               (user 2026-09-06: "индикатор времени расчёта").  The seconds are
               the BACKEND's, measured around the solve — a client stopwatch
               would also be timing the network and this result's field payload. */
            <Tooltip title={`${solvedIn(res) || 'no timing in this result'}${
              res.mesh.mesh_s ? ` — of which ${fmtSecs(res.mesh.mesh_s)} was the mesh${res.mesh.mesh_reused ? ', already built by a previous Build mesh or Solve, so this press did not pay it' : ''}` : ''}. Measured on the backend, around the geometry build and the ${resCases.length} nonlinear contact solve${resCases.length === 1 ? '' : 's'}.${res.case_mode === 'single' ? ` Single speed: only ${Math.round(res.rpm).toLocaleString()} rpm was solved, so nothing here describes any other speed.` : ''}${
              res.torque_load ? ` Torque ${fmt(res.torque_nm, 1)} N·m (${res.torque_source ?? '—'}) as ${fmt(res.torque_load.traction_mpa, 4)} MPa of tangential traction over ${fmt(res.torque_load.surface_mm, 0)} mm of gap-facing surface at r ${fmt(res.torque_load.r_gap_mm, 1)} mm — ${fmt((res.torque_load.coverage ?? 0) * 100, 0)} % of the full circle — reacted at the ${res.torque_load.reaction_at}.` : ''}${
              res.thermal?.active ? `${tempSentence(res.thermal)}${res.thermal.fit ? ` Under the band the parts would grow ${fmt(res.thermal.fit.free_growth_under_sleeve_um, 1)} µm on their own and the band's own bore ${fmt(res.thermal.fit.free_growth_sleeve_bore_um, 1)} µm, so the ${fmt(res.interference_mm, 3)} mm fit is effectively ${fmt(res.interference_effective_mm, 3)} mm at temperature.` : ''}` : ''}`}>
              <Typography sx={{ ...lbl, ml: 'auto', cursor: 'help' }}>
                {/* What was solved, before how big it was: a one-case answer
                    must never be mistaken for three (user 2026-09-06), and a
                    centrifugal-only one for the loaded machine (2026-09-07). */}
                {res.case_mode === 'single' ? 'single speed · ' : ''}
                {res.loads ? `${LOADS_LABEL[res.loads].toLowerCase()}${
                  res.torque_nm ? ` ${Math.round(res.torque_nm).toLocaleString()} N·m` : ''} · ` : ''}
                {/* Only when something is actually heated: a 20/20 °C answer is
                    the machine as drawn and saying so every time would be noise
                    (2026-09-07).  Four numbers only when the parts differ
                    (2026-09-08) — see `tempBadge`. */}
                {res.thermal?.active ? `${tempBadge(res.thermal)} · ` : ''}
                {res.mesh.n_triangles.toLocaleString()} tri · P{res.mesh.element_order}
                {/* a sector answer says so: the map is the sector replicated
                    (2026-09-09), and the triangle count is the sector's */}
                {res.symmetry && res.symmetry.mode === 'sector'
                  ? ` · 1/${res.symmetry.n_sectors} sector` : ''}
                {solvedIn(res) && ` · ${solvedIn(res)}`}
                {st.stress.restoredAt && ` · from ${st.stress.restoredAt.replace('T', ' ').replace('+00:00', ' UTC')}`}
              </Typography>
            </Tooltip>
          )}
        </Box>

        {/* ── the mesh block: ONE size for the whole tab, built on a press ──
            2026-09-06, "по поводу сетки — как я понял, она строится отдельно, и
            ей тоже нужно как-то управлять".  The stress solve, the modal solve
            and this button all use this one size; the backend memoises the
            built mesh on the geometry, so Build mesh → Solve meshes once. */}
        <Box sx={{ display: 'flex', gap: 1.25, alignItems: 'center', flexWrap: 'wrap', mt: 1 }}>
          <Tooltip title="The structural mesh is built by the mechanical mesher, separately from the electromagnetic one, and it is shared by the stress solve and the modal solve. Finer resolves the bridge roots better and costs gmsh seconds — which is why nothing here rebuilds on its own.">
            <Typography sx={{ ...lbl, fontWeight: 700, cursor: 'help', borderBottom: '1px dotted var(--text-4)' }}>
              Mesh
            </Typography>
          </Tooltip>
          <Tooltip title="Target element size, in mm. gmsh treats it as a maximum and refines further on curvature, so the edges it produces are at or below it — the line on the right reports what was actually built. Changing this does NOT rebuild: press Build mesh, or just Solve (which meshes at this size anyway).">
            <TextField label="mesh mm" size="small" value={meshMm}
              onChange={(e) => setField('meshMm', e.target.value)}
              sx={{ width: 95 }} inputProps={{ style: { fontSize: 12 } }}
              InputLabelProps={{ style: { fontSize: 12 } }} />
          </Tooltip>
          {/* ONE pole sector with cyclic-symmetry ties, as the user solves it
              in Fusion (2026-09-09: "используй периодичность, как я во
              Fusion"): every pole identical by construction, ~40× faster on
              the G2, the field replicated for the map.  One menu, tooltip for
              the rest. */}
          <Tooltip title={st.symmetry === 'sector'
            ? 'One periodic pole sector is solved, its two cut faces tied by the rotation (u_B = R(θ)·u_A), so every pole carries the identical load by construction — the way a sector model is set up in Fusion. Stresses, contact and the torque path are the machine\'s; the map is the sector replicated around the rotor. About 40× faster than the full rotor on this machine. Rotor OD growth is read on different outline nodes than the full model\'s and can differ by a few per cent — the answer notes it.'
            : 'The whole 360° rotor is solved — every pole meshed and solved separately, which is why the poles never come out exactly equal (a few per cent at 1.5 mm, more on the magnets). Pick the pole sector for exact periodicity and a much faster solve.'}>
            <Select size="small" value={st.symmetry}
              onChange={(e) => setField('symmetry', (e.target.value === 'sector' ? 'sector' : 'full'))}
              sx={{ fontSize: 11, height: 30, minWidth: 118 }}>
              <MenuItem value="full" sx={{ fontSize: 11 }}>full rotor</MenuItem>
              <MenuItem value="sector" sx={{ fontSize: 11 }}>1 pole sector</MenuItem>
            </Select>
          </Tooltip>
          <Tooltip title="Build the rotor mesh at this size and draw it, without solving anything. The Solve that follows reuses it — the backend keys the built mesh on the geometry itself — so this costs the gmsh seconds once, not twice.">
            <span>
              <Button variant="outlined" size="small" onClick={buildMesh} disabled={geomBusy || busy}
                startIcon={geomBusy ? <CircularProgress size={13} color="inherit" /> : undefined}>
                {geomBusy ? 'Building' : 'Build mesh'}
              </Button>
            </span>
          </Tooltip>
          <SolveTimer busy={geomBusy} startedAt={st.geomStartedAt} est={st.est.mesh}
            what="mesh build" />
          {geom && (
            <Tooltip title={`${(geom.n_vertices ?? geom.n_nodes).toLocaleString()} vertices, ${geom.n_triangles.toLocaleString()} triangles, element order P${geom.element_order ?? 2}. Target size ${fmt(geom.mesh_size_mm, 2)} mm; the edges gmsh actually produced run ${fmt(geom.min_edge_mm, 2)}…${fmt(geom.max_edge_mm, 2)} mm. ${geom.mesh_reused || geom.cached ? 'This mesh already existed — nothing was re-meshed, and the seconds quoted are what it cost when it was built.' : 'Built just now.'}`}>
              <Typography sx={{ ...lbl, cursor: 'help', fontFamily: 'monospace' }}>
                {geom.n_triangles.toLocaleString()} tri · P{geom.element_order ?? 2} ·{' '}
                {fmt(geom.mesh_size_mm, 2)} mm
                {/* An API that predates this field says nothing rather than
                    "built in —": a missing measurement is not a duration. */}
                {geom.mesh_s !== undefined && ` · built in ${fmtSecs(geom.mesh_s)}`}
              </Typography>
            </Tooltip>
          )}
          {/* One short line per note, tooltip for the why — never a paragraph. */}
          {builtNote && (
            <Tooltip title={`The mesh on screen was built at ${fmt(geom?.mesh_size_mm, 2)} mm and the size field now says ${meshMm} mm. Nothing rebuilds on a keystroke (a build is seconds of gmsh) — press Build mesh to see this size, or Solve, which meshes at the field's size regardless.`}>
              <Typography sx={{ ...lbl, color: '#fbbf24', cursor: 'help',
                borderBottom: '1px dotted #fbbf24' }}>
                ⚠ {builtNote}
              </Typography>
            </Tooltip>
          )}
          {resultMeshNote && (
            <Tooltip title={`The result below was solved on a ${fmt(res?.mesh.mesh_size_mm, 2)} mm mesh, not the ${meshMm} mm this field now asks for. Stresses at a bridge root are mesh-sensitive, so the two are not interchangeable — press Solve to recompute at this size.`}>
              <Typography sx={{ ...lbl, color: '#fbbf24', cursor: 'help',
                borderBottom: '1px dotted #fbbf24' }}>
                ⚠ {resultMeshNote}
              </Typography>
            </Tooltip>
          )}
        </Box>
        {/* ── contacts: one row per interface ─────────────────────────── */}
        <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'center', flexWrap: 'wrap', mt: 1 }}>
          <Tooltip title="How each interface is joined, in Fusion terms. Separation: the surfaces may open and slide but not penetrate — compression crosses, tension does not. Bonded: welded, both tension and shear cross. Sliding: cannot separate, free to slide. Nothing here re-solves on its own; press Solve.">
            <Typography sx={{ ...lbl, fontWeight: 700, cursor: 'help', borderBottom: '1px dotted var(--text-4)' }}>
              Contacts
            </Typography>
          </Tooltip>
          {CONTACT_PAIRS.filter((pair) =>
            // A machine with no retaining band has no band joints to set
            // (user 2026-09-09: "если не sleeve, надо убрать всё, что с ним
            // связано").  The saved picks are kept, only not drawn — the two
            // rows come back with the band.
            hasSleeveGeo || (pair !== 'sleeve_rotor' && pair !== 'sleeve_magnet'),
          ).map((pair) => {
            const nf = res?.contacts?.[pair]?.n_facets;
            return (
              <Box key={pair} sx={{ display: 'flex', gap: 0.5, alignItems: 'center' }}>
                <Tooltip title={pair === 'shaft_rotor'
                  ? 'Shaft to rotor bore. Bonded by default: this is a press-fit / keyed hub, and letting it rattle in the bore would model a machine nobody built.'
                  : `${CONTACT_LABEL[pair]}. Separation by default.${nf === 0 ? ' These two parts do not touch in the current cross-section.' : ''}`}>
                  <Typography sx={{ ...lbl, opacity: nf === 0 ? 0.4 : 1 }}>
                    {CONTACT_LABEL[pair]}
                  </Typography>
                </Tooltip>
                <Select size="small" value={contacts[pair].type}
                  onChange={(e) => setPair(pair, { type: e.target.value as ContactType })}
                  sx={{ fontSize: 11, height: 26, minWidth: 96 }}>
                  <MenuItem value="separation" sx={{ fontSize: 11 }}>Separation</MenuItem>
                  <MenuItem value="bonded" sx={{ fontSize: 11 }}>Bonded</MenuItem>
                  <MenuItem value="sliding" sx={{ fontSize: 11 }}>Sliding</MenuItem>
                </Select>
                <Tooltip title="Coulomb friction coefficient, used only by a Separation contact. 0.2 is the panel's default — the usual dry steel-on-composite figure — because a torque cannot cross a frictionless separation joint at all, so µ = 0 answers 'the poles are not held' before the solver starts. Set it to 0 for the conservative sleeve-sizing question, where friction does not help anyway.">
                  <span>
                    <TextField label="µ" size="small" value={String(contacts[pair].mu)}
                      disabled={contacts[pair].type !== 'separation'}
                      onChange={(e) => setPair(pair, { mu: Number(e.target.value) || 0 })}
                      sx={{ width: 62 }} inputProps={{ style: { fontSize: 11 } }}
                      InputLabelProps={{ style: { fontSize: 11 } }} />
                  </span>
                </Tooltip>
              </Box>
            );
          })}
          {/* One short line, tooltip for the why — never a paragraph.
              2026-09-07: with a torque applied, a frictionless separation joint
              is not the conservative choice, it is a missing load path. */}
          {withTorque && CONTACT_PAIRS.every(
            (p) => contacts[p].type !== 'separation' || !contacts[p].mu) && (
            <Tooltip title="Every separation contact has µ = 0, so no shear can cross any of them. With a torque applied that is not a conservative assumption — it is a rotor whose poles are attached to nothing tangentially, and the solve will say so by running away. Give the pairs a friction coefficient (0.2 is the usual dry figure) or switch the load menu back to Centrifugal.">
              <Typography sx={{ ...lbl, color: '#fbbf24', cursor: 'help',
                borderBottom: '1px dotted #fbbf24' }}>
                ⚠ µ = 0 everywhere — no torque can cross
              </Typography>
            </Tooltip>
          )}
        </Box>
        {/* The backend downgraded the load selection because it had no torque
            to apply.  Say it on one line rather than showing an empty column. */}
        {res && res.loads_requested && res.loads
         && res.loads_requested !== res.loads && (
          <Tooltip title={`This result was asked for with loads = ${res.loads_requested} but solved as ${res.loads}: there is no mean torque to apply, because nothing has been run on the Electromagnetic tab for this machine. Run it once, or type a torque into the T field, and press Solve.`}>
            <Typography sx={{ ...lbl, mt: 0.75, color: '#fbbf24', cursor: 'help',
              borderBottom: '1px dotted #fbbf24', display: 'inline-block' }}>
              ⚠ no torque to apply — solved {LOADS_LABEL[res.loads].toLowerCase()} only
            </Typography>
          </Tooltip>
        )}
        {/* A material with no CTE, a fit that has opened at temperature: ONE
            short line, tooltip for the rest (2026-09-07).  Without a band the
            only notes the solver can produce are its two band sentences
            ("no retaining band: … not a load", "sleeve_temp_c … changed
            nothing"), and the user wants nothing about a band that is not
            there (2026-09-09) — so the line exists only with a sleeve. */}
        {res && hasSleeveGeo && (res.thermal_notes?.length ?? 0) > 0 && (
          <Tooltip title={(res.thermal_notes ?? []).join(' — ')}>
            <Typography sx={{ ...lbl, mt: 0.75, color: '#fbbf24', cursor: 'help',
              borderBottom: '1px dotted #fbbf24', display: 'inline-block' }}>
              ⚠ thermal: {res.thermal_notes?.length === 1
                ? (res.thermal_notes[0] ?? '').slice(0, 90)
                : `${res.thermal_notes?.length} notes`}
            </Typography>
          </Tooltip>
        )}
        {/* A separation joint that does not retain its part was solved BONDED
            (2026-09-09): the magnet is glued on the built machine, and the
            small-displacement model cannot follow a part that has nothing to
            press against.  One line, the whole story in the tooltip. */}
        {res?.contact_fallback && (
          <Tooltip title={`${res.contact_fallback.reason}. The contact you asked for (${res.contact_fallback.from}) was tried first and refused: ${res.contact_fallback.refusal ?? '—'} Pick Bonded for this pair on the Contacts row to make it explicit, or change the pocket so the magnet has a face to press on at standstill.`}>
            <Typography sx={{ ...lbl, mt: 0.75, color: '#fbbf24', cursor: 'help',
              borderBottom: '1px dotted #fbbf24', display: 'inline-block' }}>
              ⚠ {(res.contact_fallback.pairs ?? [res.contact_fallback.pair])
                .map((p) => CONTACT_LABEL[p as keyof typeof CONTACT_LABEL] ?? p).join(', ')} solved {res.contact_fallback.to} — the separation model leaves the part unretained
            </Typography>
          </Tooltip>
        )}
        {/* A part that came loose at temperature was TRAVELLED onto the surface
            that retains it, instead of being pinned where it floated
            (2026-09-09, "магнит должен сесть на язычок, как в Fusion"). The
            travel is the number to compare with Fusion's. */}
        {seatedNote && (
          <Tooltip title={seatedNote.detail}>
            <Typography sx={{ ...lbl, mt: 0.75, color: '#fbbf24', cursor: 'help',
              borderBottom: '1px dotted #fbbf24', display: 'inline-block' }}>
              ⚠ {seatedNote.line}
            </Typography>
          </Tooltip>
        )}
        <Typography sx={{ ...lbl, mt: 0.75, display: 'block' }}>
          Rotor solids only, plane stress; interfaces are node-to-node contact; the bore is free.
          <Tooltip title="The parts are meshed as one conforming body and then the interface nodes are DUPLICATED, so each pair can open. A Separation pair transmits compression only, solved by an active set (one linear solve per iteration) with a stiff normal spring — the penetration it allows is reported as max_penetration. Because contact is nonlinear in the load, every case is its OWN solve and nothing superposes — which is why solving one speed instead of three costs a third of the seconds. Plane stress (σz = 0) is correct at the stack ends and slightly conservative mid-stack. The CFRP sleeve is orthotropic in polar coordinates: stiff along the hoop-wound fibres, an order of magnitude softer radially. Small strain, quasi-static, no load history — friction is a regularised Coulomb law (a tangential spring whose force saturates at µ·Fn), not a sliding history. With a torque applied, the traction goes on the iron and magnet tops facing the gap and never on the sleeve, and the shaft bore is held tangentially to react it.">
            <span style={{ borderBottom: '1px dotted var(--text-4)', cursor: 'help', marginLeft: 4 }}>
              assumptions
            </span>
          </Tooltip>
          {res && !resCases.every((c) => res.cases[c].contact.converged) && (
            <Typography component="span" sx={{ fontSize: 11, color: '#fbbf24', ml: 1 }}>
              — the contact active set did not settle; read the numbers as indicative
            </Typography>
          )}
          {res && resCases.some((c) => res.cases[c].contact.unretained_parts.length > 0) && (
            <Tooltip title="A part ended up with every contact released and nothing else holding it. Its displacement then comes from the rigid-body constraint rather than from equilibrium, so its stresses are not a result — the model is telling you the part is not retained at all.">
              <Typography component="span" sx={{ fontSize: 11, color: '#f87171', ml: 1, cursor: 'help' }}>
                — NOT RETAINED: {Array.from(new Set(resCases.flatMap(
                  (c) => res.cases[c].contact.unretained_parts))).join(', ')}
              </Typography>
            </Tooltip>
          )}
        </Typography>
      </Paper>

      {/* ── the machine's bearings — right under the mesh / contacts block
          (user 2026-09-08: "подними её наверх, после этой секции"); it used to
          sit at the bottom of the Modal card where nobody found it. */}
      <Paper sx={{ p: 1.25, mb: 1.5, bgcolor: 'var(--panel)' }}>
        <BearingsSection rpm={Number(rpm) || 0} />
      </Paper>

      {err && <Alert severity="error" sx={{ mb: 1.5, fontSize: 12 }}>{err}</Alert>}

      {/* ── nothing solved: the rotor itself ───────────────────────────────
          User 2026-09-06: "если нет расчётов — рисуется просто геометрия".  An
          empty tab used to be one sentence on a blank page; it is now the same
          viewer with the same camera showing the cross-section that Solve will
          colour in, so pressing Solve fills the picture instead of creating it. */}
      {!res && (
        <Paper sx={{ p: 1.25, bgcolor: 'var(--panel)' }}>
          {!err && (
            <Typography sx={{ ...lbl, mb: 0.75, display: 'block' }}>
              Nothing solved for this machine yet — press Solve to size the retaining sleeve.
            </Typography>
          )}
          <GeometryMap mesh={geom} busy={geomBusy || busy} error={geomErr} />
        </Paper>
      )}
      {!res && localTable}

      {res && (
        <>
          {/* ── the three load cases ──────────────────────────────────── */}
          <Paper sx={{ p: 1.25, mb: 1.5, bgcolor: 'var(--panel)', overflowX: 'auto' }}>
            {/* Horizontal, like the Thermal tab's tiles (user 2026-09-07: "а эту
                таблицу сделай по горизонтали"): one tile per quantity, the
                single case's speed in the corner of the same Paper. */}
            {resCases[0] && (
              <Tooltip title="The one load case this result holds: the proof speed the contact solve was run at.">
                <Typography sx={{ ...lbl, fontWeight: 700, mb: 0.75, display: 'inline-block',
                  cursor: 'help', borderBottom: '1px dotted var(--text-4)' }}>
                  {Math.round(res.cases[resCases[0]].rpm).toLocaleString()} rpm
                </Typography>
              </Tooltip>
            )}
            <Box sx={{ display: 'grid', gap: 1,
              gridTemplateColumns: 'repeat(auto-fit, minmax(148px, 1fr))' }}>
              {resCases[0] && rows.map((r) => (
                <Tooltip key={r.key} title={r.tip} placement="top">
                  <Box sx={{ p: 1, bgcolor: 'var(--panel-2)', border: '1px solid var(--app-bg)',
                    borderRadius: 1, minWidth: 0, cursor: 'help' }}>
                    <Typography sx={{ fontSize: 9, color: 'var(--text-4)',
                      textTransform: 'uppercase', letterSpacing: '0.06em', mb: 0.25 }}>
                      {r.label}
                    </Typography>
                    {r.cell(res.cases[resCases[0]], res)}
                  </Box>
                </Tooltip>
              ))}
            </Box>
            {/* ── this answer as a row of the Compare table ─────────────────
                User 2026-09-07: "нужно везде сделать такую же кнопку для
                сравнения всех величин в механических и температурных
                моделированиях".  The same button the Configure tab has, and the
                same library — so a sleeve safety factor can be read next to the
                torque and the hot-spot of the same machine. */}
            <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap', mt: 1.25 }}>
              {/* ONE press, two tables: the row lands in the stack right below
                  (the Configure tab's way of working, asked for 2026-09-07) AND
                  in the permanent Compare library. */}
              <AddResultToCompareButton kind="mechanical" onLocalAdd={addLocal} />
            </Box>
          </Paper>

          {/* ── the same variants, stacked side by side ────────────────── */}
          {localTable}

          {/* ── the one-liners that do not vary per case ──────────────── */}
          <Paper sx={{ p: 1.25, mb: 1.5, bgcolor: 'var(--panel)', display: 'flex',
            gap: 3, flexWrap: 'wrap', alignItems: 'center' }}>
            <Tooltip title="Speed at which HALF the magnet-to-iron interface has opened — the magnets have effectively left their pockets and something else is carrying them. Found by bisection on ω, because a unilateral contact is nonlinear and there is no closed form any more.">
              <Box>
                <Typography sx={lbl}>Magnet lift-off</Typography>
                <Typography sx={{ fontSize: 13, fontWeight: 700, fontFamily: 'monospace',
                  color: (res.lift_off_rpm.magnet_rotor ?? 0) > res.rpm ? '#4ade80' : '#f87171' }}>
                  {liftOffText('magnet_rotor')}
                </Typography>
              </Box>
            </Tooltip>
            {res.has_sleeve && (
              <Tooltip title="Speed at which half the sleeve-to-rotor interface has opened. Raise the interference fit or the sleeve thickness to push it above your overspeed. Part of this interface is open at standstill wherever the rotor OD dips away from the sleeve — that is geometry, not a fault.">
                <Box>
                  <Typography sx={lbl}>Sleeve lift-off</Typography>
                  <Typography sx={{ fontSize: 13, fontWeight: 700, fontFamily: 'monospace',
                    color: (res.lift_off_rpm.sleeve_rotor ?? 0) > res.overspeed_rpm ? '#4ade80' : '#f87171' }}>
                    {liftOffText('sleeve_rotor')}
                  </Typography>
                </Box>
              </Tooltip>
            )}
            {/* The fit AT TEMPERATURE — only when something is heated and there
                is a band to feel it (2026-09-07). */}
            {res.thermal?.active && res.thermal.fit && (
              <Tooltip title={`The interference the band actually feels once the rotor is hot: the ${fmt(res.interference_mm, 3)} mm geometric oversize plus the CTE mismatch under it. Computed, not assumed — the parts under the band would grow ${fmt(res.thermal.fit.free_growth_under_sleeve_um, 2)} µm on their own at ${fmt(res.thermal.rotor_temp_c, 0)} °C and the band's own bore ${fmt(res.thermal.fit.free_growth_sleeve_bore_um, 2)} µm at ${fmt(res.thermal.sleeve_temp_c, 0)} °C, each from its own free-expansion solve at the ${fmt(res.thermal.fit.sleeve_bore_radius_mm, 2)} mm bore radius. A negative value means the fit has opened and nothing is clamping.`}>
                <Box>
                  <Typography sx={lbl}>Fit at temperature</Typography>
                  <Typography sx={{ fontSize: 13, fontWeight: 700, fontFamily: 'monospace',
                    color: (res.interference_effective_mm ?? 0) > 0 ? 'var(--text-0)' : '#f87171' }}>
                    {fmt(res.interference_effective_mm, 3)} mm
                  </Typography>
                </Box>
              </Tooltip>
            )}
            <Tooltip title="Mass of the rotating parts over the stack length — rotor iron, magnets, sleeve and shaft tube. Everything on this page scales with it.">
              <Box>
                <Typography sx={lbl}>Rotor mass</Typography>
                <Typography sx={{ fontSize: 13, fontWeight: 700, fontFamily: 'monospace' }}>
                  {fmt(totalMass, 3)} kg
                </Typography>
              </Box>
            </Tooltip>
            {Object.entries(res.materials).map(([part, m]) => (
              <Tooltip key={part} title={`${m.note || m.material} — E ${fmt(m.youngs_modulus_gpa, 0)} GPa, ν ${fmt(m.poisson_ratio, 2)}, ${m.strength_kind} ${fmt(m.strength_mpa, 0)} MPa${m.orthotropic ? `, orthotropic: E_radial ${fmt(m.youngs_modulus_transverse_gpa, 0)} GPa, G ${fmt(m.shear_modulus_gpa, 0)} GPa` : ''}${
                m.cte_ppm_k_1 === null || m.cte_ppm_k_1 === undefined
                  ? '. No thermal-expansion coefficient on this card — the part does not expand.'
                  : `. Thermal expansion ${fmt(m.cte_ppm_k_1, 2)} ppm/K${m.cte_anisotropic ? ` along axis 1 and ${fmt(m.cte_ppm_k_2, 2)} ppm/K across it` : ''}.`}`}>
                <Box>
                  <Typography sx={lbl}>{part}</Typography>
                  <Typography sx={{ fontSize: 11, color: 'var(--text-1)', fontFamily: 'monospace' }}>
                    {m.material}
                  </Typography>
                </Box>
              </Tooltip>
            ))}
          </Paper>

          {/* ── ONE result picture with a menu ─────────────────────────────
              2026-09-06, replacing the three side-by-side canvases: "нужно
              сделать одну картинку и меню для переключения выводов графиков;
              интерфейс должен быть единым для всех графиков".  The toolbar
              state stays here (this panel persists and re-reads it); StressMap
              is the host that turns it into FieldOutputs for the shared
              viewer. */}
          {res.field && (
            <Paper sx={{ p: 1.25, bgcolor: 'var(--panel)' }}>
              <StressMap
                /* The remembered choice outlives the result it was made on: a
                   single-speed answer has no "rated" column, and indexing it
                   with one would blank the picture instead of showing the one
                   case there is (2026-09-06). */
                res={res} caseName={pickCase(res, viewCase)}
                onCase={(c) => setField('viewCase', c)}
                view={view} onView={(v: MechView) => setField('view', v)}
                exagg={exagg} onExagg={(v) => setField('exagg', v)}
                contacts={showContacts} onContacts={(v) => setField('showContacts', v)}
                sfLow={sfLow} onSfLow={(v) => setField('sfLow', v)}
                sfHigh={sfHigh} onSfHigh={(v) => setField('sfHigh', v)}
                staleNote={staleNote} />
            </Paper>
          )}
        </>
      )}

      {/* Modal analysis — 2026-09-05, "нам нужно сделать ещё модальный анализ,
          чтобы понять все частоты — это очень важно для 20000 rpm".  Outside
          the `res &&` block on purpose: it is a different model of the same
          machine, not a view of the stress result, and it must be reachable
          without spending a contact solve first. */}
      <ModalSection rpm={Number(rpm) || 0} />
    </Box>
  );
};

export default MechanicalPanel;
