/** Thermal tab — the steady-state temperature of the machine, and the coupled
 *  EM↔thermal fixed point.
 *
 * Split out of the Electromagnetic tab's field viewer on 2026-09-07.  It had lived
 * there as one more entry in the electromagnetic output menu ("Temp"), with its
 * cooling inputs squeezed into that viewer's toolbar — but it is a different
 * physics with its own mesh (the solids alone: the outer air and the gap are
 * dropped), its own boundary conditions and its own minute-long solve, and an
 * EM field view that can start a conduction solve is a field view that solves
 * something nobody asked it for.  Modelled 1:1 on the Mechanical tab, which had
 * already been split out for the same reason.
 *
 * Nothing solves on mount.  The operating point comes from the Electromagnetic tab
 * (standing project rule: every physics setting of a run is read from where the
 * user set it, never from a default in a panel), and Solve is a deliberate
 * press.  The results live in `stores/thermalStore`, not in this component, so
 * leaving the tab does not throw a solve away.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, Box, Button, CircularProgress, Collapse, MenuItem, Paper, Select,
  TextField, Tooltip, Typography,
} from '@mui/material';

import { useMotorStore } from '../../stores/motorStore';
import { coolingIssue, isStale, useThermalStore } from '../../stores/thermalStore';
import AddResultToCompareButton from '../compare/AddResultToCompareButton';
import { MAX_LOCAL_ROWS, localThermalRow, partMaxLabel } from '../compare/resultRows';
import LocalCompareTable from '../common/LocalCompareTable';
import type { ColumnDef } from '../common/LocalCompareTable';
import SolveProgressStrip from '../common/SolveProgressStrip';
import { SolveTimer, solvedIn } from '../mechanical/SolveTimer';
import ThermalMap, { GeometryMap } from './ThermalMap';
import HeatPathView3D from './HeatPathView3D';
import DutyCycleEditor from './DutyCycleEditor';
import { DUTY_CYCLE_ENABLED } from '../../lib/dutyCycleFlag';
import HelpTip, { CTRL_ROW, TIP_PROPS } from './HelpTip';
// The ORCHESTRATOR's last answer, for the one line this tab reads off it.
import { fetchCoupledLast, coupledStateLine,
         coupledStateTip } from '../simulation/coupledApi';
import type { CouplingBlock } from '../simulation/coupledApi';
import {
  BORE_MODE_LABEL, COOL_MODE_LABEL, END_FACE_LABEL, END_FACE_SIDES_LABEL,
  FRAME_LABEL as FRAME_MODE_LABEL, HOW_IT_WORKS, HOW_IT_WORKS_TITLE,
  ROBOTICS_HELP, ROBOTICS_SUBTITLE, SHAFT_SIDES_LABEL,
} from './roboticsHelp';
import {
  fmt, fmtSecs, meshParams, outerCooling, simOperatingPoint, writeSimCoilTemp,
} from './api';
import type {
  BoreMode, CoolMode, EndFaceMode, FrameMode, ThermView, ThermalCoolingSurface,
} from './api';

/* A hint used to be wrapped round each Select here, pushed under the menu with
   a z-index (2026-09-07: 'падающее меню подсказки не даёт сменить воздух на
   жидкость').  That was only half the fix — a tooltip is INTERACTIVE by
   default, so its popper takes the pointer even from under the menu — and
   since 2026-09-15 no tooltip wraps a control at all: the hint hangs on a ⓘ
   beside it (`HelpTip`), and the tooltips that remain wrap READOUTS and carry
   `TIP_PROPS`. */

const lbl = { fontSize: 11, color: 'var(--text-3)', whiteSpace: 'nowrap' } as const;
const warn = { ...lbl, color: '#fbbf24', cursor: 'help',
               borderBottom: '1px dotted #fbbf24' } as const;
const bad = { ...warn, color: '#f87171', borderBottom: '1px dotted #f87171' } as const;

/* Every menu text and every hint of this cooling block lives in
   `roboticsHelp` — one module, so the panel, the 3-D view's popovers and the
   arrow tooltips cannot describe the same parameter three different ways
   (owner 2026-09-17: «надо более подробно расписать это меню»).  An option
   now says what it DOES, not what it is called internally. */
const COOL_LABEL: Record<CoolMode, string> = COOL_MODE_LABEL;
const BORE_LABEL: Record<BoreMode, string> = BORE_MODE_LABEL;
/** How the machine is BUILT.  `open` is the 40 mm CIANO14 (user 2026-09-09:
 *  *нет корпуса*) — tooth blocks between two end plates, end turns and slot
 *  channels in the propeller wash. */
const FRAME_LABEL: Record<FrameMode, string> = FRAME_MODE_LABEL;
const FLUIDS: [string, string][] = [
  ['water', 'Water'], ['water_glycol_50', 'Glycol 50 %'],
  ['ethylene_glycol', 'Ethylene glycol'], ['oil', 'Oil'],
];

/** Watts the way an engineer says them: 480 W, 5.2 kW, 12 kW. */
const fmtW = (w: unknown): string => {
  const v = Number(w);
  if (!Number.isFinite(v)) return '—';
  const a = Math.abs(v);
  if (a >= 10000) return `${(v / 1000).toFixed(0)} kW`;
  if (a >= 1000) return `${(v / 1000).toFixed(1)} kW`;
  return `${v.toFixed(0)} W`;
};

/** 210000 → "2.1e5".  A Taylor number spans decades, so a fixed-point print of
 *  one is a wall of zeros nobody reads. */
const fmtExp = (x: unknown): string => {
  const v = Number(x);
  if (!Number.isFinite(v)) return '—';
  if (v === 0) return '0';
  const a = Math.abs(v);
  if (a >= 0.01 && a < 1e4) return v.toFixed(a >= 100 ? 0 : 2);
  return v.toExponential(1).replace('e+', 'e');
};

/** The one-line summary under a surface's h: what mode it is, what temperature
 *  it works against (the OUTLET for a liquid loop — that is the result the flow
 *  produced) and how much heat it is actually taking away. */
function surfaceSub(c: ThermalCoolingSurface | null | undefined,
                    fallbackMode: string): string {
  if (!c || c.mode === 'none') return 'none';
  const bits: string[] = [String(c.mode ?? fallbackMode)];
  if (Number.isFinite(c.r_bore_mm)) bits.push(`r ${fmt(c.r_bore_mm, 1)} mm`);
  if (c.mode === 'liquid' && Number.isFinite(c.t_out_c)) {
    bits.push(`out ${fmt(c.t_out_c, 0)} °C`);
  } else if (Number.isFinite(c.t_sink_c)) {
    bits.push(`sink ${fmt(c.t_sink_c, 0)} °C`);
  }
  if (Number.isFinite(c.heat_removed_W)) bits.push(fmtW(c.heat_removed_W));
  // A spinning bore: say how much of h is the rotation's own doing (centrifugal
  // convection on ω²r, 2026-09-07) — the number the user asked to see move with
  // the speed.
  if (Number.isFinite(c.h_rotation as number) && (c.h_rotation as number) > 0) {
    bits.push(`rotation ${fmt(c.h_rotation as number, 0)}`);
  }
  return bits.join(' · ');
}

/** Everything a surface tile's tooltip has to say, in one sentence per fact. */
function surfaceTip(where: string, c: ThermalCoolingSurface | null | undefined): string {
  if (!c || c.mode === 'none') {
    return `${where}: no cooling — this surface is adiabatic, nothing leaves through it.`;
  }
  const m = String(c.mode ?? '');
  const parts = [
    `The film the solver actually applied to the ${where}, echoed back rather than recomputed here.`,
    m === 'liquid'
      ? `Inlet ${fmt(c.t_in_c, 1)} °C at ${fmt(c.flow_lpm, 2)} L/min of ${String(c.fluid ?? 'coolant')}; it leaves at ${fmt(c.t_out_c, 1)} °C — the OUTLET is a result of that flow, not something you set.`
      : m === 'air'
        ? `Air at ${fmt(c.air_speed_mps, 1)} m/s against ${fmt(c.t_sink_c, 1)} °C.`
        : 'A film coefficient given by hand, applied as it is.',
    Number.isFinite(c.heat_removed_W)
      ? `${fmtW(c.heat_removed_W)} crosses it${Number.isFinite(c.area_m2) ? ` over ${fmt((c.area_m2 as number) * 1e4, 0)} cm²` : ''}.` : '',
    Number.isFinite(c.re) ? `Re ${fmtExp(c.re)}, Nu ${fmt(c.nu, 1)}.` : '',
    c.note ? String(c.note) : '',
    'For scale: still air ≈ 7, a fan on the case 25…60, a water jacket 1 000…5 000 W/m²K.',
  ];
  return parts.filter(Boolean).join(' ');
}

/** The heading of one boundary-condition row. */
const RowLabel: React.FC<{ text: string; tip: string }> = ({ text, tip }) => (
  <Tooltip {...TIP_PROPS} title={tip} placement="top">
    <Typography sx={{ ...lbl, fontWeight: 700, cursor: 'help', minWidth: 84,
      borderBottom: '1px dotted var(--text-4)' }}>
      {text}
    </Typography>
  </Tooltip>
);

/** Blow speed — the same choices on either surface.  40–60 m/s are what a
 *  self-pumped bore reaches (impeller + inclined outlet holes, 2026-09-08). */
const SpeedSelect: React.FC<{ value: string; onChange: (v: string) => void; tip: string }> =
  ({ value, onChange, tip }) => (
    <Box sx={CTRL_ROW}>
      <Select size="small" value={value} onChange={(e) => onChange(String(e.target.value))}
        sx={{ fontSize: 11, height: 30, minWidth: 120 }}>
        {['0', '2', '5', '10', '20', '30', '40', '50', '60'].map((v) => (
          <MenuItem key={v} value={v} sx={{ fontSize: 11 }}>
            {v === '0' ? 'Still air (0 m/s)' : `${v} m/s`}
          </MenuItem>
        ))}
      </Select>
      <HelpTip title={tip} />
    </Box>
  );

const FluidSelect: React.FC<{ value: string; onChange: (v: string) => void; tip: string }> =
  ({ value, onChange, tip }) => (
    <Box sx={CTRL_ROW}>
      <Select size="small" value={value} onChange={(e) => onChange(String(e.target.value))}
        sx={{ fontSize: 11, height: 30, minWidth: 130 }}>
        {FLUIDS.map(([v, label]) => (
          <MenuItem key={v} value={v} sx={{ fontSize: 11 }}>{label}</MenuItem>
        ))}
      </Select>
      <HelpTip title={tip} />
    </Box>
  );

/** One number of a cooling loop: what goes in, or how much of it. */
const NumField: React.FC<{
  label: string; value: string; onChange: (v: string) => void;
  tip: string; width?: number; error?: boolean;
}> = ({ label, value, onChange, tip, width = 92, error }) => (
  <Box sx={CTRL_ROW}>
    <TextField label={label} size="small" value={value} error={error}
      onChange={(e) => onChange(e.target.value)}
      sx={{ width }} inputProps={{ style: { fontSize: 12 } }}
      InputLabelProps={{ style: { fontSize: 12 } }} />
    <HelpTip title={tip} />
  </Box>
);

/** One number the user is meant to read, with the sentence that explains it.
 *  The same tile the Mechanical tab uses — one short line, tooltip for the
 *  rest (the project's no-walls-of-text rule). */
/** The mean's colour when a tile carries both numbers — same type, same size,
 *  a quieter ink (user 2026-09-09: "давай будем писать одинаковыми шрифтами,
 *  но разным цветом max и mean").  Two sizes read as a headline and a
 *  footnote; two colours read as two equally real numbers, which is what they
 *  are — the peak decides the insulation class, the mean is what the coupled
 *  loop solves at. */
const MEAN_INK = 'var(--text-3)';

const Tile: React.FC<{
  label: string; value: string; unit?: string; sub?: string; colour?: string;
  /** the second number of the same quantity — printed in `MEAN_INK` at the
   *  SAME size as the first, after a thin separator */
  value2?: string; tooltip: string;
}> = ({ label, value, unit, sub, colour = 'var(--text-0)', value2, tooltip }) => (
  <Tooltip {...TIP_PROPS} title={tooltip} placement="top">
    <Box sx={{ p: 1, bgcolor: 'var(--panel-2)', border: '1px solid var(--app-bg)',
      borderRadius: 1, minWidth: 0, cursor: 'help' }}>
      <Typography sx={{ fontSize: 9, color: 'var(--text-4)',
        textTransform: 'uppercase', letterSpacing: '0.06em' }}>
        {label}
      </Typography>
      <Typography sx={{ fontSize: 14, fontWeight: 700, color: colour,
        fontFamily: 'monospace', lineHeight: 1.15 }}>
        {value}
        {value2 != null && (
          <>
            <Typography component="span" sx={{ fontSize: 14, fontWeight: 400,
              color: 'var(--text-4)', fontFamily: 'monospace', mx: 0.4 }}>/</Typography>
            <Typography component="span" sx={{ fontSize: 14, fontWeight: 700,
              color: MEAN_INK, fontFamily: 'monospace' }}>{value2}</Typography>
          </>
        )}
        {unit && (
          <Typography component="span" sx={{ fontSize: 9, color: 'var(--text-4)', ml: 0.4, fontWeight: 400 }}>
            {unit}
          </Typography>
        )}
      </Typography>
      {sub && (
        <Typography sx={{ fontSize: 9, color: 'var(--text-4)', fontFamily: 'monospace' }}>
          {sub}
        </Typography>
      )}
    </Box>
  </Tooltip>
);

/** Colour for a temperature against the limit of what is being heated: class-F
 *  insulation gives up around 155 °C, sintered NdFeB starts losing Br well
 *  before 150 °C.  The point of the tab is that these two numbers are visible
 *  the moment they matter. */
function hotAccent(t: number | null | undefined, limit: number): string {
  if (t === null || t === undefined || !Number.isFinite(t)) return 'var(--text-0)';
  if (t >= limit) return '#f87171';
  if (t >= limit * 0.9) return '#fbbf24';
  return 'var(--text-0)';
}

/* ONE tile per PART the solver reported — every solid and every insulation,
   the sleeve and the air regions included (user 2026-09-07: "сделай список
   максимальных температур всех частей мотора, и слива, и изоляций").  The
   order and the limits are this table's; a part the backend adds tomorrow
   still shows, at the end, under its own key.  At module scope so the local
   comparison table below can order ITS columns the same way — the tiles and
   the table must not name the machine's parts in two different orders. */
/** The two means the coupled loop actually carries back into the next
 *  electromagnetic run — and NOTHING else (user 2026-09-09: the note "the
 *  coupled loop's number" was put under every tile, which made it a claim
 *  about the slot fill and the liner as well; the loop has never read those). */
const FED_BACK: Record<string, string> = {
  winding: "the coil temperature the coupled loop solves at",
  magnet: "the magnet temperature the coupled loop solves at",
};

/** The hot-spot tile carries two numbers of the same kind too. */
const HOTSPOT_SUB = 'hottest / coldest point of the whole model';

const PART_TILES: { key: string; label: string; limit?: number; tip: string }[] = [
  { key: 'winding', label: 'Winding', limit: 155,
    tip: "Peak and mean temperature of the copper. The peak is the number insulation class is judged on — ~155 °C for class F, ~180 °C for class H — and it is normally deeper in the slot than the mean suggests." },
  { key: 'enamel', label: 'Wire enamel', limit: 155,
    tip: "Peak temperature of the wire's own film (polyimide ~200 °C, polyester-imide ~180 °C, class F ~155 °C). It sits on the copper, so it is the copper's temperature — the number the enamel grade is chosen against." },
  { key: 'slot_fill', label: 'Slot fill', limit: 155,
    tip: "Peak temperature of the impregnation / air between the conductors. Varnish and epoxy fills soften and crack above their class; an unimpregnated slot is air and conducts ten times worse." },
  { key: 'liner', label: 'Insulation', limit: 155,
    tip: "Peak temperature of the ground-wall liner between the winding and the tooth (Nomex is rated 200 °C, class F liners 155 °C). It carries the whole copper loss to the iron, so its own rise is the price of its thickness." },
  { key: 'magnet', label: 'Magnet', limit: 150,
    tip: "Peak and mean magnet temperature. Sintered NdFeB loses about 0.12 % of Br per K reversibly and starts to lose it IRREVERSIBLY once the knee climbs past the working point — which is why this number, not the winding's, often sets the current limit. It is also the temperature to put in the duty's magnet-variant picker on the Electromagnetic tab." },
  { key: 'sleeve', label: 'Sleeve', limit: 150,
    tip: "Peak temperature of the retaining sleeve. A carbon/epoxy ring creeps above its resin's glass transition (~120–180 °C by resin) — and it is the rotor's series resistance to the gap, so it runs at the magnet's temperature, not the stator's." },
  { key: 'rotor', label: 'Rotor',
    tip: "Peak rotor iron temperature. It has no cooled surface of its own — everything it makes has to cross the air gap or leave through the shaft — which is why the gap conductivity (and therefore the speed) and the bore cooling move it so much. This is the number the Mechanical tab's rotor temperature field wants." },
  { key: 'shaft', label: 'Shaft',
    tip: "Peak shaft temperature — the rotor's heat path to the bore coolant, and the part bearings and fits are sized against." },
  { key: 'stator', label: 'Stator',
    tip: "Peak stator iron temperature. The iron is the path the copper's heat takes to the housing, so its rise is mostly a measure of how good that path is." },
  { key: 'gap_air', label: 'Air gap',
    tip: "Peak temperature of the air in the mechanical clearance — the film the rotor's heat crosses (turbulent, Taylor–Couette), between the sleeve and the stator bore." },
  { key: 'pocket_air', label: 'Pocket air',
    tip: "Peak temperature of the air trapped in the rotor's magnet pockets and flux barriers — closed cells at the rotor's own temperature." },
];
const PART_LABEL = (k: string) => k.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

/* ═══════════════════════════════════════════════════════════════════════════
 * The tab's own comparison table
 *
 * User 2026-09-07: *"сделай локальное сравнение по параметрам тепловой
 * симуляции, только как в Configure"*.  These are the COLUMNS; the rows are
 * built by `compare/resultRows.localThermalRow`, so the table and the Compare
 * library read the same keys and can never mean two things by `winding_max`.
 *
 * There are more input columns here than would fit on a screen, on purpose:
 * the table drops every input that is IDENTICAL in all rows into its "same for
 * all" line, so what stays is exactly what the user changed between variants.
 * ═══════════════════════════════════════════════════════════════════════════ */
const THERM_INPUT_COLS: ColumnDef[] = [
  { key: 'cool_mode', label: 'Outer', kind: 'input' },
  { key: 'air_speed_mps', label: 'Air', unit: 'm/s', d: 0, kind: 'input' },
  { key: 'fluid', label: 'Coolant', kind: 'input' },
  { key: 'fluid_in_c', label: 'In', unit: '°C', d: 0, kind: 'input' },
  { key: 'flow_lpm', label: 'Flow', unit: 'L/min', d: 1, kind: 'input' },
  { key: 'h_manual', label: 'h set', unit: 'W/m²K', d: 0, kind: 'input' },
  { key: 'ambient_c', label: 'Ambient', unit: '°C', d: 0, kind: 'input' },
  { key: 'bore_mode', label: 'Bore', kind: 'input' },
  { key: 'bore_air_speed_mps', label: 'Bore air', unit: 'm/s', d: 0, kind: 'input' },
  { key: 'bore_fluid', label: 'Bore coolant', kind: 'input' },
  { key: 'bore_in_c', label: 'Bore in', unit: '°C', d: 0, kind: 'input' },
  { key: 'bore_flow_lpm', label: 'Bore flow', unit: 'L/min', d: 1, kind: 'input' },
  // The exposed shaft — written by `thermalInputsFromResult` only when there is
  // one, so these two columns simply do not exist for a machine with no stub.
  { key: 'shaft_out_mm', label: 'Shaft out', unit: 'mm/side', d: 0, kind: 'input' },
  { key: 'shaft_sides', label: 'Shaft ends', d: 0, kind: 'input' },
  // The FRAME, and only when it is open — a column reading "housed" on every
  // row is a column nobody can read a difference off (see resultRows).
  { key: 'frame', label: 'Frame', kind: 'input' },
  { key: 'open_air_mps', label: 'Open air', unit: 'm/s', d: 1, kind: 'input' },
  // The operating point: read from the Electromagnetic tab, never from here —
  // but two cooling designs compared at two currents are not a comparison of
  // cooling designs, so it is a column.
  { key: 'I_A', label: 'I', unit: 'A', d: 0, kind: 'input' },
  { key: 'gamma_deg', label: 'γ', unit: '°', d: 1, kind: 'input' },
  { key: 'rpm', label: 'rpm', d: 0, kind: 'input' },
  { key: 'coil_c', label: 'Coil', unit: '°C', d: 0, kind: 'input' },
  // The three conductivities that decide a winding temperature, each with the
  // card it came from.
  { key: 'liner_mat', label: 'Insulation', kind: 'input' },
  { key: 'liner_k', label: 'Liner k', unit: 'W/m·K', d: 3, kind: 'input' },
  { key: 'enamel_mat', label: 'Enamel', kind: 'input' },
  { key: 'enamel_k', label: 'Enamel k', unit: 'W/m·K', d: 3, kind: 'input' },
  { key: 'fill_mat', label: 'Fill', kind: 'input' },
  { key: 'fill_k', label: 'Fill k', unit: 'W/m·K', d: 3, kind: 'input' },
];

/** What comes out, after the per-part temperatures.  Neither the housing nor
 *  the bore watts get a `better`: more heat out of the housing is better
 *  cooling and worse rotor cooling at once, and a colour would pick a side. */
const THERM_TAIL_COLS: ColumnDef[] = [
  { key: 'T_max', label: 'Hot-spot', unit: '°C', d: 0, better: 'lo', kind: 'result' },
  { key: 'housing_W', label: 'Housing', unit: 'W', d: 0, kind: 'result' },
  { key: 'bore_W', label: 'Via bore', unit: 'W', d: 0, kind: 'result' },
  { key: 'shaft_ends_W', label: 'Via shaft ends', unit: 'W', d: 1, kind: 'result' },
  // The OPEN frame's two paths (2026-09-09).  0 on a housed machine, and that
  // is a statement about the design, so they are worth a column beside it.
  { key: 'end_windings_W', label: 'Via end turns', unit: 'W', d: 1, kind: 'result' },
  { key: 'slot_channels_W', label: 'Via slot channels', unit: 'W', d: 1,
    kind: 'result' },
  { key: 'gap_W', label: 'Across gap', unit: 'W', d: 0, kind: 'result' },
  { key: 'coupled_coil_c', label: 'Coupled coil', unit: '°C', d: 1, better: 'lo',
    kind: 'result' },
];

/* ═══════════════════════════════════════════════════════════════════════════
 * The coupled EM↔thermal loop
 *
 * Its own section, below the map and outside the `res &&` block on purpose: it
 * is a different model of the same machine, not a view of the field result, and
 * it must be reachable without spending a conduction solve first (the same
 * placement the Mechanical tab's modal section has).
 * ═══════════════════════════════════════════════════════════════════════════ */
const CoupledSection: React.FC = () => {
  // READ-ONLY since 2026-09-11: the iteration cap, the Run button and the
  // cooling gate left with the launcher (the loop starts from the
  // Electromagnetic tab).  What stays is the last coupled answer and its
  // history, which this tab is the right place to read.
  const st = useThermalStore();
  const res = st.coupled.data;
  const busy = st.coupled.busy;
  const err = st.coupled.err;

  const hist = res?.coil_temp_history_C ?? [];
  // …AND HOW LONG THE POINT MAY BE HELD (owner 2026-09-17).  Read from the
  // ORCHESTRATOR's last answer (`/api/coupled/last`), which is the loop that
  // computes it: this section's own slice is the Thermal tab's one-loss-map
  // loop, which has no magnet feedback and therefore no limits to judge.  It is
  // a read, never a solve, and it says nothing at all when the last coupled run
  // was inside every limit or predates the feature.
  // THE WHOLE COUPLING BLOCK since 2026-09-18, not just its time-to-limit
  // half: a `limits` run's answer is the `limited` block, and the line has to
  // be able to say "the numbers are the machine at that moment".
  const [ttl, setTtl] = useState<CouplingBlock | null>(null);
  useEffect(() => {
    let alive = true;
    void fetchCoupledLast().then((r) => {
      if (!alive) return;
      setTtl(r && !r.stale ? (r.coupling ?? null) : null);
    });
    return () => { alive = false; };
  }, [res]);
  const [used, setUsed] = useState(false);
  // A new answer is a new number to adopt: the "used" acknowledgement belongs
  // to the result it was pressed on, not to the button.
  useEffect(() => { setUsed(false); }, [res]);

  return (
    <Paper sx={{ p: 1.25, mt: 1.5, bgcolor: 'var(--panel)' }}>
      <Box sx={{ display: 'flex', gap: 1.25, alignItems: 'center', flexWrap: 'wrap' }}>
        <Tooltip {...TIP_PROPS} title="The fixed point of the loss↔temperature feedback: the losses heat the copper, hotter copper is more resistive, more resistive copper loses more. Each iteration is a full EM solve plus a conduction solve, so this is minutes, not seconds — and it is the only honest way to get a coil temperature instead of assuming one.">
          <Typography sx={{ fontSize: 12, fontWeight: 700, color: 'var(--text-0)',
            cursor: 'help', borderBottom: '1px dotted var(--text-4)' }}>
            Coupled EM ↔ thermal
          </Typography>
        </Tooltip>
        {/* NO LAUNCHER HERE (user 2026-09-11: "у нас каплинг только из
            электромагнитов запускается").  The loop is started from the
            Electromagnetic tab, which owns the operating point it iterates on;
            a second button over here could start it from a panel whose cooling
            had been edited but not solved, and the two entry points then
            disagreed about which point the machine was at.  This block stays
            as the READ-OUT of whatever the last coupled run converged to. */}
        <Typography sx={{ ...lbl }}>
          started from the Electromagnetic tab
        </Typography>
        <SolveTimer busy={busy} startedAt={st.coupled.startedAt}
          est={st.est.coupled} what="coupled solve" />
        {res && (
          <Tooltip {...TIP_PROPS} title={`${solvedIn(res) || 'no timing in this result'}. ${res.iterations} iteration${res.iterations === 1 ? '' : 's'} of a full EM solve plus a conduction solve each, measured on the backend.${st.coupled.restoredAt ? ` Solved ${st.coupled.restoredAt.replace('T', ' ').replace('+00:00', ' UTC')}.` : ''}`}>
            <Typography sx={{ ...lbl, ml: 'auto', cursor: 'help' }}>
              {res.iterations} iter{solvedIn(res) && ` · ${solvedIn(res)}`}
            </Typography>
          </Tooltip>
        )}
      </Box>

      {err && <Alert severity="error" sx={{ mt: 1, fontSize: 12 }}>{err}</Alert>}

      {res && (
        <Box sx={{ mt: 1 }}>
          {res.runaway ? (
            <Tooltip {...TIP_PROPS} title="The loop diverges: at this operating point every iteration is hotter than the last, so there is no steady state to report. Physically the machine cooks — more cooling (a higher h, a colder sink) or less current is the only fix, and the temperatures below are simply where the iteration got to.">
              <Typography sx={{ ...warn, color: '#f87171', borderBottomColor: '#f87171',
                fontWeight: 700, display: 'inline-block', mb: 0.75 }}>
                ⚠ thermal runaway — no equilibrium at this operating point
              </Typography>
            </Tooltip>
          ) : !res.converged && (
            <Tooltip {...TIP_PROPS} title="The iteration cap was reached before the copper temperature settled. The last value is where it had got to, not a fixed point — raise the cap, or read the history to see whether it was still moving.">
              <Typography sx={{ ...warn, display: 'inline-block', mb: 0.75 }}>
                ⚠ did not converge in {res.iterations} iterations
              </Typography>
            </Tooltip>
          )}
          {/* ONE SHORT LINE (owner 2026-09-17): a machine past a limit, and how
              long it may be held before it gets there.  The model is in the
              HelpTip beside it, per the no-walls-of-text rule. */}
          {coupledStateLine(ttl) && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5,
                       mb: 0.75 }}>
              <Typography sx={{ ...warn, display: 'inline-block',
                                borderBottom: 'none', cursor: 'default',
                                whiteSpace: 'normal' }}>
                {coupledStateLine(ttl)}
              </Typography>
              <HelpTip title={coupledStateTip(ttl).split('\n')[0]} />
            </Box>
          )}

          <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            {/* The feedback made visible: one row, one column per iteration. */}
            <Tooltip {...TIP_PROPS} title="Copper temperature after each pass of the loop. A sequence that flattens is a fixed point; one that keeps climbing by the same step or more is the runaway the warning above names.">
              <Box sx={{ cursor: 'help' }}>
                <Typography sx={lbl}>copper per iteration</Typography>
                <Box sx={{ display: 'grid', gridAutoFlow: 'column', gap: 0.25, mt: 0.25 }}>
                  {hist.map((t, i) => (
                    <Typography key={i} sx={{
                      fontSize: 11, fontFamily: 'monospace', px: 0.75, py: 0.25,
                      borderRadius: 0.5,
                      bgcolor: i === hist.length - 1 ? 'var(--line-accent)' : 'var(--panel-2)',
                      color: i === hist.length - 1 ? 'var(--brand)' : 'var(--text-2)',
                    }}>
                      {fmt(t, 0)}°
                    </Typography>
                  ))}
                </Box>
              </Box>
            </Tooltip>
            <Tooltip {...TIP_PROPS} title="The copper temperature the loop settled on. It is a RESULT of this tab and an INPUT of the Electromagnetic tab — the button beside it is the only thing that moves it there, because silently changing another panel's operating point is exactly the state mutation this project has a standing rule against.">
              <Box sx={{ cursor: 'help' }}>
                <Typography sx={lbl}>equilibrium copper</Typography>
                <Typography sx={{ fontSize: 15, fontWeight: 700, fontFamily: 'monospace',
                  color: hotAccent(res.coil_temp_converged_C, 155) }}>
                  {fmt(res.coil_temp_converged_C, 1)} °C
                </Typography>
              </Box>
            </Tooltip>
            <Tooltip {...TIP_PROPS} title={`Write ${fmt(res.coil_temp_converged_C, 1)} °C into the Electromagnetic tab's coil temperature. Never automatic: this is your operating point, and the Electromagnetic tab is where every other solve reads it from.`}>
              <span>
                <Button size="small" variant="text" disabled={used || res.runaway}
                  onClick={() => { writeSimCoilTemp(res.coil_temp_converged_C); setUsed(true); }}
                  sx={{ fontSize: 11, textTransform: 'none' }}>
                  {used ? 'written to Electromagnetic' : 'Use in Electromagnetic'}
                </Button>
              </span>
            </Tooltip>
            <Tooltip {...TIP_PROPS} title="Copper loss at the converged temperature — the number that grew as the winding heated (ρ_Cu rises about 0.39 % per K).">
              <Box sx={{ cursor: 'help' }}>
                <Typography sx={lbl}>copper loss</Typography>
                <Typography sx={{ fontSize: 13, fontWeight: 700, fontFamily: 'monospace' }}>
                  {fmt(res.P_cu_W ?? res.field?.P_cu_W, 0)} W
                </Typography>
              </Box>
            </Tooltip>
            <Tooltip {...TIP_PROPS} title="Hot-spot of the converged temperature field — the peak anywhere in the machine, which is usually inside the slot and not in the copper's mean.">
              <Box sx={{ cursor: 'help' }}>
                <Typography sx={lbl}>hot-spot</Typography>
                <Typography sx={{ fontSize: 13, fontWeight: 700, fontFamily: 'monospace',
                  color: hotAccent(res.field?.T_max, 155) }}>
                  {fmt(res.field?.T_max, 0)} °C
                </Typography>
              </Box>
            </Tooltip>
          </Box>
        </Box>
      )}
    </Paper>
  );
};

/* ═══════════════════════════════════════════════════════════════════════════ */

const ThermalPanel: React.FC = () => {
  // The store's own geometry is the live machine; reading it here is what makes
  // the staleness badge re-evaluate the moment the geometry changes, without a
  // round trip to the backend.
  const liveGeometry = useMotorStore((s) => s.geometry);

  const st = useThermalStore();
  const {
    coolMode, ambientT, airSpeed, fluid, tIn, hConv, flowLpm,
    boreMode, boreAirSpeed, boreFluid, boreTIn, boreFlowLpm,
    shaftExtMm, shaftExtSides, frame, openAirSpeed,
    emissivity, mountG, mountT, endFaces, endFaceSides,
    view, eqTemp, showFlux, geom, geomBusy, geomErr,
  } = st;
  const res = st.field.data;
  const busy = st.field.busy;
  const err = st.field.err;
  // The action identities are stable for the store's lifetime, so they are safe
  // effect dependencies — `st` as a whole is not (a new object per change).
  const setField = st.set;
  const hydrate = st.hydrate;
  const solveField = st.solveField;
  const loadGeometry = st.loadGeometry;

  // Come back to whatever was on screen: the store first (a tab switch), then
  // the backend's persisted last result (a reload / API restart), then the bare
  // cross-section.  Nothing is SOLVED by this — `hydrate` is a read.
  // …and on EVERY mount, ask whether the backend has something newer — the
  // coupled loop's own map is filed as the last thermal result, and `hydrate`
  // only ever runs once per session (2026-09-10).
  const refreshLastThermal = st.refreshLast;
  useEffect(() => { void hydrate().then(() => refreshLastThermal()); },
            [hydrate, refreshLastThermal]);

  /* ── the operating point, from the Electromagnetic tab ────────────────────────
     Read on every mount (this tab is not keepMounted, so entering it is a
     fresh read) and refreshed on the events that mean those settings moved —
     a duty load fires `sim-settings-restored`, a finished run re-stamps the
     panel.  This panel NEVER writes them and never keeps a copy of its own. */
  const [op, setOp] = useState(simOperatingPoint);
  /* The "How this cooling model works" note — CLOSED by default and not
     persisted: it is a thing you read once, and a panel that reopens a wall of
     text on every visit is the wall of text this project forbids. */
  const [howOpen, setHowOpen] = useState(false);
  useEffect(() => {
    const on = () => setOp(simOperatingPoint());
    window.addEventListener('sim-settings-restored', on);
    window.addEventListener('sim-transient-done', on);
    return () => {
      window.removeEventListener('sim-settings-restored', on);
      window.removeEventListener('sim-transient-done', on);
    };
  }, []);

  /* ── is the shown result still this machine's? ───────────────────────────
     We do NOT re-solve: an expensive solve started by a geometry edit the user
     has not finished making is worse than a badge.  `liveGeometry` is in the
     dependency list so the badge appears the moment the machine changes. */
  const stale = useMemo(
    () => !!res && isStale(st.field.geoSig, st.field.backendStale),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [res, st.field.geoSig, st.field.backendStale, liveGeometry]);
  const staleNote = stale ? 'result is for a previous geometry — re-Solve' : null;
  const staleTip = st.field.backendStale === true
    ? `The backend fingerprinted the machine this was solved on (${res?.geometry_fingerprint ?? '—'}) and it is not the one loaded now. Every temperature below, and the picture, describe the previous cross-section. Press Solve to recompute them for this machine.${st.field.restoredAt ? ` Solved ${st.field.restoredAt}.` : ''}`
    : 'The geometry changed after this result was solved, so the temperatures and the picture below belong to the previous cross-section. Press Solve to recompute them for the machine currently loaded.';

  const solve = useCallback(() => { void solveField(); }, [solveField]);
  const buildMesh = useCallback(() => { void loadGeometry(); }, [loadGeometry]);

  /* ── the Electromagnetic run this Solve had to make for itself ────────────
     Non-null only while it is in flight (stores/thermalStore.emRunThroughLoop).
     It is what turns a one-minute conduction solve into a several-minute wait,
     so it gets the progress strip, a line saying why, and a Stop. */
  const emFb = st.emFallback;
  const stopEm = useCallback(
    () => useThermalStore.getState().stopEmFallback(), []);

  /* ── the mesh, as a thing you can see ────────────────────────────────────
     Unlike Mechanical's, the thermal mesh has no size field of its own: it is
     built by the SAME `geo_mesh` pipeline the Electromagnetic tab solves on, from
     the Mesh tab's settings.  So this block REPORTS those settings and gives
     them a Build button; changing them is done where they live, and nothing
     rebuilds on its own (a build is seconds of the mesher). */
  // The server's mesh block once the store has read it; the browser's keys
  // only before that (see api.fetchMeshParams for the 683-second lesson).
  const mp = st.meshCfg ?? meshParams();
  const meshOff = (built: number | undefined | null) =>
    built !== undefined && built !== null
    && Math.abs(built - mp.mesh_size_mm) > 1e-6;
  const builtNote = meshOff(geom?.mesh_size_mm)
    ? `built at ${fmt(geom?.mesh_size_mm, 2)} mm` : null;
  /* No "this result used a different mesh" note here, unlike Mechanical: the
     field payload does not carry the size it was meshed at, and a note derived
     from anything else would be a guess about how much the answer is worth. */

  /* What the solver did at each boundary.  `outerCooling` is the ONE reader of
     the cooling block, so a result restored from before the two-surface split
     prints in exactly these tiles. */
  const outer = outerCooling(res?.cooling);
  const inner = res?.cooling?.inner;
  const gap = res?.cooling?.gap;
  const sleeve = res?.cooling?.sleeve;
  const showInner = !!inner && inner.mode !== undefined && inner.mode !== 'none';
  /* The rotor's third path: the shaft outside the housing.  `mode: 'off'` is a
     statement about the machine, not a missing block, so the tile appears only
     when there really is a stub in the air. */
  const shaftEnds = res?.cooling?.shaft_ends;
  const showShaft = !!shaftEnds && shaftEnds.mode !== undefined
    && shaftEnds.mode !== 'off';
  /* The OPEN frame's two paths (2026-09-09).  `mode: 'housed'` is an answer,
     not a missing block, so the tiles appear only when the machine really has
     no housing — same rule the shaft tile above follows. */
  const endWind = res?.cooling?.end_windings;
  const slotCh = res?.cooling?.slot_channels;
  const showOpen = res?.cooling?.frame === 'open';
  const gapK = gap?.k_eff ?? res?.gap_k;
  /* The rotor's heat budget: what the rotor side makes leaves either through
     the shaft bore or across the gap — the split the cooling design of a
     sleeved rotor turns on. */
  const budget = res?.cooling?.heat_budget;
  /* The ROBOTICS paths (2026-09-14): the bolted mount and the four axial end
     faces.  `mode: 'off'` is an answer about the machine, not a missing block,
     so the line below appears only when one of them is really there — the same
     rule the shaft and the open-frame tiles follow. */
  const mount = res?.cooling?.mount;
  const endFaceBlk = res?.cooling?.end_faces;
  const sSplit = budget?.stator_heat_split;
  const showRobot = mount?.mode === 'conduction' || endFaceBlk?.mode === 'still'
    || (budget?.housing_radiation_W ?? 0) > 0;
  /* Which SIDE of the machine the heat left through, in the solver's own
     convention (`thermal_duty_cycle.average_split`): the stator side is the
     housing, the mount and the stator-side axial faces (plus an open frame's
     two paths); the rotor side is the bore, the shaft stubs and the rotor's and
     magnets' own end faces.  Shares are of what LEFT, so they add to 100 %
     whatever the closure error is — a share of the generation would move with
     the residual and read as physics. */
  const statorSideW = (budget?.housing_W ?? 0) + (budget?.mount_W ?? 0)
    + (sSplit?.end_faces_W ?? 0) + (budget?.end_windings_W ?? 0)
    + (budget?.slot_channels_W ?? 0);
  const rotorSideW = (budget?.bore_W ?? 0) + (budget?.shaft_ends_W ?? 0)
    + (budget?.rotor_heat_split?.axial_end_faces_W ?? 0);
  const outTotalW = statorSideW + rotorSideW;
  const sidePct = (w: number): number | null =>
    (Math.abs(outTotalW) > 1e-9 ? 100 * w / outTotalW : null);
  /* The solver reports ∫q dV inside the slip radius directly since 2026-09-07.
     The sum of the two outflows is the same number only while the solve closes,
     which is exactly the case where a panel must not paper over the difference;
     it stays as the fallback for a result cached before that field existed. */
  const rotorW = budget
    ? (budget.rotor_W ?? ((budget.bore_W ?? 0) + (budget.gap_W ?? 0)
                          + (budget.shaft_ends_W ?? 0)))
    : null;
  /* Everything that leaves the rotor WITHOUT crossing the air gap: the bore
     film and the exposed shaft ends.  That sum against `rotorW` is the number
     a shaft-cooled design is read on. */
  const rotorOutW = (budget?.bore_W ?? 0) + (budget?.shaft_ends_W ?? 0);
  /* …and the same split as the solver states it (2026-09-10), with the shares.
     User: "нужно считать два числа: сколько тепла от ротора уходит через
     внешний диаметр, а сколько через внутренний — то есть через зазор и через
     вал".  Both numbers were on the tile as small print among four others; they
     are the two the cooling design turns on, so they are now the tile itself.
     Falls back to the pieces for a result solved before the block existed. */
  const split = budget?.rotor_heat_split;
  const gapOutW = split?.gap_W ?? budget?.gap_W ?? 0;
  /* The INNER diameter, 2-D: the bore surface.  The shaft stubs are an axial
     path out of the page and are named on their own, never folded in here. */
  const boreOutW = split?.bore_W ?? budget?.bore_W ?? 0;
  const pct = (v: number | null | undefined, w: number): string =>
    (v != null ? `${fmt(v, 0)} %`
      : (rotorW ? `${fmt(100 * w / rotorW, 0)} %` : '—'));
  /* The conductivities of everything in the slot and the gap that is not metal.
     One short line, tooltip for the rest (the project's UI rule): the numbers
     that decide a winding temperature are the liner's and the fill's, and an
     engineer has to be able to see at a glance whether they are their datasheet
     or our documented default. */
  const mu = res?.materials_used;
  const air = res?.air_domains;
  /** the cooling as typed — a sentence while it cannot be solved, else null */
  const coolErr = coolingIssue(st);
  const comps = (res?.components ?? {}) as Record<string, { max?: number; avg?: number } | null | undefined>;
  const partTiles = [
    ...PART_TILES.filter((p) => comps[p.key]),
    ...Object.keys(comps).filter((k) => comps[k] && !PART_TILES.some((p) => p.key === k))
      .map((k) => ({ key: k, label: PART_LABEL(k), limit: undefined as number | undefined,
                     tip: `Peak and mean temperature of "${PART_LABEL(k)}" — a part the solver reported that this panel has no note for yet.` })),
  ];

  /* ── the tab's own comparison stack ──────────────────────────────────────
     User 2026-09-07: "сделай локальное сравнение по параметрам тепловой
     симуляции, только как в Configure".  The rows live in the store (so they
     survive leaving the tab) and are persisted with the tab's other fields (so
     they survive a reload and another browser); the Compare tab remains the
     permanent, cross-physics library. */
  const compareRows = st.compareRows;

  /** One column per PART the STORED rows carry — the tiles' order first, then
   *  anything the backend has started reporting since (the same rule the tiles
   *  follow: a new domain must not silently vanish from the comparison). */
  const compareCols = useMemo<ColumnDef[]>(() => {
    const keys = new Set<string>();
    compareRows.forEach((r) => Object.keys(r.results).forEach((k) => {
      if (k.endsWith('_max') && k !== 'T_max') keys.add(k);
    }));
    const known = PART_TILES.map((p) => `${p.key}_max`).filter((k) => keys.has(k));
    const rest = [...keys].filter((k) => !known.includes(k)).sort();
    return [
      ...THERM_INPUT_COLS,
      ...[...known, ...rest].map((k): ColumnDef => ({
        key: k, label: partMaxLabel(k), unit: '°C', d: 0, better: 'lo',
        kind: 'result',
      })),
      ...THERM_TAIL_COLS,
    ];
  }, [compareRows]);

  /* Read through `getState()` rather than the render's closure: the button
     that calls this is a child that re-renders on its own schedule, and a
     stale `compareRows` here would drop whatever was stacked meanwhile. */
  const addLocal = useCallback(() => {
    const s = useThermalStore.getState();
    if (s.compareRows.length >= MAX_LOCAL_ROWS) {
      throw new Error(`the comparison below already holds ${MAX_LOCAL_ROWS} `
        + 'variants — remove one before adding another');
    }
    // The coupled loop rides along only when it belongs to THIS machine — the
    // same guard the Compare row uses.
    const coupled = s.coupled.data
      && !isStale(s.coupled.geoSig, s.coupled.backendStale) ? s.coupled.data : null;
    // The operating point is read where it is set, at the moment of the press,
    // exactly like the context line above (standing project rule).
    const { inputs, results } = localThermalRow(s.field.data, coupled, s,
                                                simOperatingPoint());
    const now = new Date();
    s.set('compareRows', [...s.compareRows, {
      id: `t${now.getTime().toString(36)}${Math.random().toString(36).slice(2, 6)}`,
      name: `#${s.compareRows.length + 1} · ${now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`,
      at: now.toISOString(), inputs, results,
    }]);
  }, []);
  const removeLocal = useCallback((id: string) => {
    const s = useThermalStore.getState();
    s.set('compareRows', s.compareRows.filter((r) => r.id !== id));
  }, []);
  const clearLocal = useCallback(() => {
    useThermalStore.getState().set('compareRows', []);
  }, []);
  const renameLocal = useCallback((id: string, name: string) => {
    const s = useThermalStore.getState();
    s.set('compareRows', s.compareRows.map((r) => (r.id === id ? { ...r, name } : r)));
  }, []);

  /* Rendered ONCE — under the tiles when there is a result, on its own when
     there is not (a stack that survived a reload must not be invisible until
     something is solved again). */
  const localTable = (res || compareRows.length > 0) ? (
    <Paper sx={{ p: 1.25, mb: 1.5, bgcolor: 'var(--panel)' }}>
      <LocalCompareTable title="Local comparison" rows={compareRows}
        columns={compareCols} onRemove={removeLocal} onClear={clearLocal}
        onRename={renameLocal}
        emptyHint={<>Press <b>Add to comparison</b> above to stack cooling variants here — what differs becomes the columns.</>} />
    </Paper>
  ) : null;

  return (
    <Box sx={{ height: '100%', overflow: 'auto', p: 1.5 }}>
      {/* Live solve progress — the same strip the Electromagnetic tab has, pinned to
          the very top of THIS scroller (sticky only works inside the element
          that scrolls).  A coupled EM ↔ thermal run is the long one worth
          watching.  Renders nothing while idle.

          While this tab is making the Electromagnetic run its own solve was
          missing, the bar with something to say is the ORCHESTRATOR's: the
          thermal tracker is silent through the whole transient, which is most
          of the wait.  Keyed, so the strip remounts instead of showing the
          other endpoint's last reading for a poll. */}
      {emFb ? (
        <SolveProgressStrip key="coupled" endpoint="/api/coupled/progress"
          unit="steps"
          kindLabels={{ coupled: 'Electromagnetic run for this map' }} />
      ) : (
        <SolveProgressStrip key="thermal" endpoint="/api/thermal/progress"
          unit="steps"
          kindLabels={{
            field: 'Thermal solve', coupled: 'Coupled EM ↔ thermal',
            mesh: 'Mesh build',
          }} />
      )}

      {/* ── controls ──────────────────────────────────────────────────── */}
      <Paper sx={{ p: 1.25, mb: 1.5, bgcolor: 'var(--panel)' }}>
        {/* ── the ambient both air modes work against, and Solve ────────── */}
        <Box sx={{ display: 'flex', gap: 1.25, alignItems: 'center', flexWrap: 'wrap' }}>
          {/* The air temperature is an input ONLY when air is blown somewhere
              (over the housing, through the bore).  With a liquid jacket and
              no air in the bore it decides nothing, and a field that decides
              nothing is a question the user should not be asked (2026-09-07:
              "зачем тебе это, если всё равно все граничные условия задаём"). */}
          {(coolMode === 'air' || boreMode === 'air' || coolMode === 'robotics') && (
            <Box sx={CTRL_ROW}>
              <TextField
                label={coolMode === 'robotics' ? ROBOTICS_HELP.ambientT.label : 'air °C'}
                size="small" value={ambientT}
                onChange={(e) => setField('ambientT', e.target.value)}
                sx={{ width: coolMode === 'robotics' ? 122 : 96 }}
                inputProps={{ style: { fontSize: 12 } }}
                InputLabelProps={{ style: { fontSize: 12 } }} />
              <HelpTip title={coolMode === 'robotics' ? ROBOTICS_HELP.ambientT.tip
                : 'Temperature of the blown air, °C — the one ambient every air path works against.'} />
            </Box>
          )}

          <Button variant="contained" size="small" onClick={solve}
            disabled={busy || !!coolErr}
            startIcon={busy ? <CircularProgress size={13} color="inherit" /> : undefined}>
            {busy ? 'Solving' : 'Solve'}
          </Button>
          <SolveTimer busy={busy} startedAt={st.field.startedAt} est={st.est.field}
            what="thermal solve" />

          {/* This Solve turned into an Electromagnetic run.  ONE short line and
              a tooltip that says exactly which run was missing — the sentence
              the backend refused with. */}
          {emFb && (
            <>
              <Tooltip {...TIP_PROPS} title={`This tab never computes electromagnetic losses — they are the Electromagnetic tab's result — so it is being made for you first, through the EM ↔ thermal orchestrator: one electromagnetic run at ${emFb.label ? 'the point that run is for' : 'the temperatures set on that tab'}, then a thermal solve with the cooling above. What was missing: ${emFb.why}`}>
                <Typography sx={{ ...warn, fontWeight: 700, whiteSpace: 'normal' }}>
                  {/* The duty-cycle editor borrows this same fallback for a
                      point that is NOT the one on screen, and says so itself —
                      its label replaces the sentence rather than letting the
                      strip claim the wrong point. */}
                  ⚠ {emFb.label
                    ? emFb.label
                    : 'no Electromagnetic run at this point — running it through the coupled loop first…'}
                </Typography>
              </Tooltip>
              <Tooltip {...TIP_PROPS} title="Cancel the electromagnetic run. It stops between phases and, inside a transient, at the next frame — so it can take a few seconds, and it leaves no result.">
                <span>
                  <Button variant="outlined" size="small" color="warning"
                    onClick={stopEm} disabled={emFb.stopping}>
                    {emFb.stopping ? 'Stopping' : 'Stop'}
                  </Button>
                </span>
              </Tooltip>
            </>
          )}

          {/* ONE short line, tooltip for the rest — the no-walls-of-text rule. */}
          {coolErr && (
            <Tooltip {...TIP_PROPS} title="The boundary conditions below do not describe a machine that can be solved, so Solve is disabled until they do — this tab validates the input rather than sending it and translating the solver's refusal back.">
              <Typography sx={{ ...bad, fontWeight: 700 }}>⚠ {coolErr}</Typography>
            </Tooltip>
          )}
          {staleNote && (
            <Tooltip {...TIP_PROPS} title={staleTip}>
              <Typography sx={{ ...warn, fontWeight: 700 }}>⚠ {staleNote}</Typography>
            </Tooltip>
          )}

          {res && (
            <Tooltip {...TIP_PROPS} title={`${solvedIn(res) || 'no timing in this result'}. Measured on the backend, around the electromagnetic loss solve and the conduction solve.${st.field.restoredAt ? ` Solved ${st.field.restoredAt.replace('T', ' ').replace('+00:00', ' UTC')}.` : ''}`}>
              <Typography sx={{ ...lbl, ml: 'auto', cursor: 'help' }}>
                {fmt(res.P_loss_total_W, 0)} W loss
                {solvedIn(res) && ` · ${solvedIn(res)}`}
              </Typography>
            </Tooltip>
          )}
        </Box>

        {/* ── the ROBOTICS block's own header (owner 2026-09-17) ────────────
            One line that says what the mode IS, and one click that lists the
            heat paths in the order the 3-D view draws them with the parameter
            that governs each.  Closed by default: the rule is one short line
            plus a tooltip, and this is the tooltip made readable for the one
            reader who wants the whole map at once.  Every word comes from
            `roboticsHelp`, which the 3-D view reads too. */}
        {coolMode === 'robotics' && (
          <Box sx={{ mt: 1 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
              <Typography sx={{ ...lbl, fontStyle: 'italic' }}>
                {ROBOTICS_SUBTITLE}
              </Typography>
              <Typography component="button" type="button"
                onClick={() => setHowOpen((v) => !v)}
                sx={{ ...lbl, background: 'none', border: 'none', p: 0,
                  cursor: 'pointer', color: 'var(--brand)', fontWeight: 700,
                  textDecoration: 'underline', textUnderlineOffset: 2 }}>
                {howOpen ? '▾' : '▸'} {HOW_IT_WORKS_TITLE}
              </Typography>
            </Box>
            <Collapse in={howOpen} unmountOnExit>
              <Box component="ul" sx={{ listStyle: 'none', m: 0, mt: 0.5, p: 0,
                pl: 0.5, borderLeft: '2px solid var(--line-accent, #334155)' }}>
                {HOW_IT_WORKS.map((h) => (
                  <Box component="li" key={h.path}
                    sx={{ ...lbl, whiteSpace: 'normal', pl: 1, py: 0.125 }}>
                    <Box component="span" sx={{ color: 'var(--text-2)' }}>{h.path}</Box>
                    {h.param && (
                      <Box component="span" sx={{ fontWeight: 700, color: 'var(--text-0)' }}>
                        {' — '}{h.param}
                      </Box>
                    )}
                    {': '}{h.text}
                  </Box>
                ))}
              </Box>
            </Collapse>
          </Box>
        )}

        {/* ── boundary 1: the OUTER stator surface ──────────────────────────
            Two rows, one per surface, because they are two independent boundary
            conditions of the same solve (user 2026-09-07) — a machine cooled
            through its hollow shaft alone was not expressible before. */}
        <Box sx={{ display: 'flex', gap: 1.25, alignItems: 'center', flexWrap: 'wrap', mt: 1 }}>
          <RowLabel text="Outer surface"
            tip="The outside of the stator — the housing, the jacket, whatever the machine is wrapped in. This is where almost all of the loss leaves on a normally-built motor: the copper's heat crosses the iron to get here." />
          <Box sx={CTRL_ROW}>
            <Select size="small" value={coolMode}
              onChange={(e) => {
                const m = e.target.value as CoolMode;
                setField('coolMode', m);
                // The bore rides WITH the mode: `still` is evaluated with the
                // robotics emissivity and is refused by name outside it, so
                // leaving it behind would arm a refusal the user never chose.
                // A joint's bore is open — that is decision 2 of the plan — so
                // picking the mode picks it, and leaving the mode gives the
                // bore back to the machine it belongs to.
                if (m === 'robotics' && boreMode === 'none') setField('boreMode', 'still');
                if (m !== 'robotics' && boreMode === 'still') setField('boreMode', 'none');
              }}
              sx={{ fontSize: 11, height: 30, minWidth: 218 }}>
              {(['air', 'liquid', 'manual', 'none', 'robotics'] as CoolMode[]).map((m) => (
                <MenuItem key={m} value={m} sx={{ fontSize: 11 }}>{COOL_LABEL[m]}</MenuItem>
              ))}
            </Select>
            <HelpTip title={ROBOTICS_HELP.coolMode.tip} />
          </Box>

          {/* ── the ROBOTICS mode's own four inputs (2026-09-14) ───────────
              One line, one tooltip each — the project's no-walls-of-text rule.
              They only appear with the mode, because they are only sent by it. */}
          {coolMode === 'robotics' && (
            <>
              <NumField label={ROBOTICS_HELP.emissivity.label} value={emissivity}
                onChange={(v) => setField('emissivity', v)} width={128}
                error={!!coolErr && /emissivity/.test(coolErr)}
                tip={ROBOTICS_HELP.emissivity.tip} />
              <NumField label={ROBOTICS_HELP.mountG.label} value={mountG}
                onChange={(v) => setField('mountG', v)} width={160}
                error={!!coolErr && /mount conductance/.test(coolErr)}
                tip={ROBOTICS_HELP.mountG.tip} />
              <NumField label={ROBOTICS_HELP.mountT.label} value={mountT}
                onChange={(v) => setField('mountT', v)} width={196}
                tip={ROBOTICS_HELP.mountT.tip} />
              <Box sx={CTRL_ROW}>
                <Select size="small" value={endFaces}
                  onChange={(e) => setField('endFaces', e.target.value as EndFaceMode)}
                  sx={{ fontSize: 11, height: 30, minWidth: 176 }}>
                  {(['still', 'none'] as EndFaceMode[]).map((m) => (
                    <MenuItem key={m} value={m} sx={{ fontSize: 11 }}>{END_FACE_LABEL[m]}</MenuItem>
                  ))}
                </Select>
                <HelpTip title={ROBOTICS_HELP.endFaces.tip} />
              </Box>
              {endFaces === 'still' && (
                <Box sx={CTRL_ROW}>
                  <Select size="small" value={endFaceSides}
                    onChange={(e) => setField('endFaceSides', String(e.target.value))}
                    sx={{ fontSize: 11, height: 30, minWidth: 186 }}>
                    {['2', '1'].map((n) => (
                      <MenuItem key={n} value={n} sx={{ fontSize: 11 }}>{END_FACE_SIDES_LABEL[n]}</MenuItem>
                    ))}
                  </Select>
                  <HelpTip title={ROBOTICS_HELP.endFaceSides.tip} />
                </Box>
              )}
            </>
          )}

          {coolMode === 'air' && (
            <SpeedSelect value={airSpeed} onChange={(v) => setField('airSpeed', v)}
              tip="Blow speed over the housing, m/s → the convection coefficient (Churchill–Bernstein for a cylinder in cross-flow). Still air is not zero cooling — it is natural convection, about 7 W/m²K." />
          )}

          {coolMode === 'liquid' && (
            <>
              <FluidSelect value={fluid} onChange={(v) => setField('fluid', v)}
                tip="The coolant. Its heat capacity is what turns litres per minute into a temperature rise, so glycol and oil come back hotter than water at the same flow." />
              <NumField label="in °C" value={tIn} onChange={(v) => setField('tIn', v)} width={88}
                tip="Coolant inlet temperature, °C — what comes out of the radiator and into the jacket. The outlet is NOT an input: it is what this machine does to that coolant at the flow beside it." />
              <NumField label="L/min" value={flowLpm} onChange={(v) => setField('flowLpm', v)}
                width={88} error={!!coolErr && coolMode === 'liquid'}
                tip="The pump, in litres per minute — required, and greater than zero. This is the number an engineer actually chooses; the outlet temperature and the jacket's film coefficient both follow from it. Doubling the flow buys about 2^0.8 ≈ 1.74 times the h and halves the coolant's own temperature rise." />
            </>
          )}

          {coolMode === 'manual' && (
            <NumField label="h W/m²K" value={hConv} onChange={(v) => setField('hConv', v)}
              width={105} error={!!coolErr && coolMode === 'manual'}
              tip="Convection coefficient at the outer surface, W/m²K, applied as it is. For scale: still air ≈ 7, a fan on the case 25…60, a water jacket 1 000…5 000." />
          )}

          {coolMode === 'none' && (
            <Tooltip {...TIP_PROPS} title="Adiabatic: nothing leaves through the outer surface at all. Only meaningful with the bore cooled below — a machine with no cooled surface anywhere has no steady state, and Solve says so.">
              <Typography sx={{ ...lbl, cursor: 'help' }}>adiabatic — nothing leaves here</Typography>
            </Tooltip>
          )}
        </Box>

        {/* ── boundary 2: the INNER rotor bore ─────────────────────────────── */}
        <Box sx={{ display: 'flex', gap: 1.25, alignItems: 'center', flexWrap: 'wrap', mt: 1 }}>
          <RowLabel text="Rotor bore"
            tip="The rotor's inner diameter — the hollow shaft. Cooling it is the only path that reaches the rotor and the magnets WITHOUT crossing the air gap, which is why it moves the magnet temperature far more than a bigger fan on the housing does. Off by default: most shafts are solid and nothing flows through them." />
          <Box sx={CTRL_ROW}>
            <Select size="small" value={boreMode}
              onChange={(e) => setField('boreMode', e.target.value as BoreMode)}
              sx={{ fontSize: 11, height: 30, minWidth: 208 }}>
              {/* `still` is offered ONLY with the robotics mode: it is
                  evaluated with that mode's emissivity, an input no other mode
                  sends, and the backend refuses it elsewhere by name. */}
              {(coolMode === 'robotics'
                ? (['still', 'none', 'air', 'liquid'] as BoreMode[])
                : (['none', 'air', 'liquid'] as BoreMode[])).map((m) => (
                <MenuItem key={m} value={m} sx={{ fontSize: 11 }}>{BORE_LABEL[m]}</MenuItem>
              ))}
            </Select>
            <HelpTip title={ROBOTICS_HELP.boreMode.tip} />
          </Box>

          {boreMode === 'air' && (
            <SpeedSelect value={boreAirSpeed} onChange={(v) => setField('boreAirSpeed', v)}
              tip="Air speed through the bore, m/s, at the ambient temperature above (it is the same air). Internal flow, so the film follows a pipe correlation rather than the cross-flow one used on the housing." />
          )}

          {boreMode === 'liquid' && (
            <>
              <FluidSelect value={boreFluid} onChange={(v) => setField('boreFluid', v)}
                tip="The bore coolant — its own loop, chosen independently of the outer one." />
              <NumField label="in °C" value={boreTIn} onChange={(v) => setField('boreTIn', v)}
                width={88}
                tip="Bore coolant inlet temperature, °C. Its outlet is a result, exactly as the outer loop's is." />
              <NumField label="L/min" value={boreFlowLpm}
                onChange={(v) => setField('boreFlowLpm', v)} width={88}
                error={!!coolErr && boreMode === 'liquid'}
                tip="Bore pump, litres per minute — required and greater than zero. A bore is a much smaller wetted area than the housing, so the litres buy less total heat here but land where the magnets are." />
            </>
          )}

          {boreMode === 'none' && !(Number(shaftExtMm) > 0) && (
            <Tooltip {...TIP_PROPS} title="Nothing flows through the bore: it is adiabatic, and everything the rotor and the magnets make has to cross the air gap to get out. That is the default machine.">
              <Typography sx={{ ...lbl, cursor: 'help' }}>adiabatic — all rotor heat crosses the gap</Typography>
            </Tooltip>
          )}

          {/* ── the SHAFT OUTSIDE the housing ────────────────────────────────
              User 2026-09-07: "торцы и лобовые части — только для вала, всё
              остальное вращается внутри мотора".  The end faces and the end
              windings turn inside a closed housing and are deliberately NOT
              modelled — they have nowhere else to send their heat.  The shaft
              does: it comes out through the bearings, so its exposed stubs are
              a third path off the rotor and they belong in this row, beside
              the bore, because both of them bypass the air gap. */}
          <NumField label={ROBOTICS_HELP.shaftExtMm.label} value={shaftExtMm}
            onChange={(v) => setField('shaftExtMm', v)} width={202}
            tip={ROBOTICS_HELP.shaftExtMm.tip} />
          {Number(shaftExtMm) > 0 && (
            <Box sx={CTRL_ROW}>
              <Select size="small" value={shaftExtSides}
                onChange={(e) => setField('shaftExtSides', String(e.target.value))}
                sx={{ fontSize: 11, height: 30, minWidth: 138 }}>
                {['2', '1'].map((n) => (
                  <MenuItem key={n} value={n} sx={{ fontSize: 11 }}>{SHAFT_SIDES_LABEL[n]}</MenuItem>
                ))}
              </Select>
              <HelpTip title={ROBOTICS_HELP.shaftExtSides.tip} />
            </Box>
          )}
        </Box>

        {/* ── HOW THE MACHINE IS BUILT ──────────────────────────────────────
            User 2026-09-09, on the 40 mm CIANO14: "нет корпуса" — the tooth
            blocks with their coils hang between two end plates on standoff
            pins, and the end turns plus the axial channels between neighbouring
            coils are in the propeller wash.  Every row above assumes the
            opposite, which is why this is a MODE and not a tick box: on a
            housed motor the end turns really do have nowhere to send their
            heat, and adding a path there would flatter every housed design. */}
        <Box sx={{ display: 'flex', gap: 1.25, alignItems: 'center', flexWrap: 'wrap', mt: 1 }}>
          <RowLabel text="Frame" tip={ROBOTICS_HELP.frame.tip} />
          <Box sx={CTRL_ROW}>
            <Select size="small" value={frame}
              onChange={(e) => setField('frame', e.target.value as FrameMode)}
              sx={{ fontSize: 11, height: 30, minWidth: 178 }}>
              {(['housed', 'open'] as FrameMode[]).map((m) => (
                <MenuItem key={m} value={m} sx={{ fontSize: 11 }}>{FRAME_LABEL[m]}</MenuItem>
              ))}
            </Select>
            <HelpTip title="Open adds two paths a housed machine does not have: end turns in cross flow and the slot channels." />
          </Box>
          {frame === 'open' && (
            <NumField label={ROBOTICS_HELP.openAirSpeed.label} value={openAirSpeed}
              onChange={(v) => setField('openAirSpeed', v)} width={112}
              tip={ROBOTICS_HELP.openAirSpeed.tip} />
          )}
        </Box>

        {/* ── WHAT THE MACHINE DOES WITH THIS POINT, over TIME ───────────────
            Right here, under the frame, and always open (user 2026-09-16:
            «Меню Duty cycle должно быть всегда открыто и находиться вверху,
            после frame»).  It used to sit at the very bottom of this tab
            behind a Hide/Show button, which is where the LAST thing goes — and
            the kind chosen here decides what the Run button three panels away
            actually does: S1 is the plain coupled loop, S3 makes the coupled
            loop search the allowable ED.  That is the first decision of a run,
            so it is read before the mesh and the map, not after them.

            Still inert: nothing in it solves on mount, and its own RUN CYCLE
            button remains the standalone tool.

            …and BEHIND A FLAG since 2026-09-17 (owner: «давай пока уберём duty
            cycle из Thermal, оставим только стандартный каплинг»).  Off by
            default, so this tab is the cooling and the coupled loop and nothing
            else; `VITE_DUTY_CYCLE=1` at build time brings the block back
            exactly as it is.  Nothing was deleted — see `lib/dutyCycleFlag`. */}
        {DUTY_CYCLE_ENABLED && <DutyCycleEditor />}

        {/* ── where the physics comes from: the Electromagnetic tab, always ──── */}
        <Box sx={{ display: 'flex', gap: 1.25, alignItems: 'center', flexWrap: 'wrap', mt: 1 }}>
          <Tooltip {...TIP_PROPS} title="This tab owns the COOLING and nothing else. The current, the current angle, the speed, the coil temperature the losses are computed at and the steps per period are read from the Electromagnetic tab every time you press Solve — a standing rule of this project, so that one operating point describes the machine on every tab instead of each panel inventing its own. Change them there.">
            <Typography sx={{ ...lbl, cursor: 'help', borderBottom: '1px dotted var(--text-4)',
              fontFamily: 'monospace' }}>
              from Electromagnetic: {fmt(op.I_phase_rms, 0)} A · γ {fmt(op.gamma_deg, 1)}°
              {' · '}{Math.round(op.rpm).toLocaleString()} rpm
              {' · coil '}{fmt(op.coil_temp_c, 0)} °C · {op.n_steps_per_period} steps
            </Typography>
          </Tooltip>
          {!(op.I_phase_rms > 0) && (
            <Tooltip {...TIP_PROPS} title="The Electromagnetic tab's current is zero, so there is no I²R at all: what this solve will show is the no-load temperature — iron loss and magnet eddy only. That is a real answer, just not the one a duty point usually asks for.">
              <Typography sx={warn}>⚠ no current set — no-load losses only</Typography>
            </Tooltip>
          )}
        </Box>

        {/* ── the mesh, reported and built ──────────────────────────────── */}
        <Box sx={{ display: 'flex', gap: 1.25, alignItems: 'center', flexWrap: 'wrap', mt: 1 }}>
          <Tooltip {...TIP_PROPS} title="The conduction solve runs on the SOLID sub-mesh of the same mesh the Electromagnetic tab uses — the outer air and the gap are dropped (there is nothing to conduct through in them; the gap is a conductivity instead). Its settings therefore belong to the Mesh tab and are shown here, not edited here.">
            <Typography sx={{ ...lbl, fontWeight: 700, cursor: 'help',
              borderBottom: '1px dotted var(--text-4)' }}>
              Mesh
            </Typography>
          </Tooltip>
          <Tooltip {...TIP_PROPS} title="The Mesh tab's current settings — target element size, the minimum gmsh may refine to, the outer-air factor, and how many sectors of the machine are actually solved (a sector answer is tiled to the full ring for display). Change them on the Mesh tab.">
            <Typography sx={{ ...lbl, cursor: 'help', fontFamily: 'monospace' }}>
              {fmt(mp.mesh_size_mm, 2)} mm · min {fmt(mp.min_size_mm, 2)} mm ·{' '}
              {mp.n_sectors} sector{mp.n_sectors === 1 ? '' : 's'}
            </Typography>
          </Tooltip>
          <Tooltip {...TIP_PROPS} title="Build the thermal sub-mesh at those settings and draw it, without solving anything. The Solve that follows reuses it — the backend keys the built mesh on the geometry — so this costs the mesher's seconds once, not twice.">
            <span>
              <Button variant="outlined" size="small" onClick={buildMesh}
                disabled={geomBusy}
                startIcon={geomBusy ? <CircularProgress size={13} color="inherit" /> : undefined}>
                {geomBusy ? 'Building' : 'Build mesh'}
              </Button>
            </span>
          </Tooltip>
          <SolveTimer busy={geomBusy} startedAt={st.geomStartedAt} est={st.est.mesh}
            what="mesh build" />
          {geom && (
            <Tooltip {...TIP_PROPS} title={`${(geom.n_vertices ?? geom.vertices.length).toLocaleString()} vertices, ${geom.n_triangles.toLocaleString()} triangles at a ${fmt(geom.mesh_size_mm, 2)} mm target. ${geom.cached ? 'This mesh already existed — nothing was re-meshed, and the seconds quoted are what it cost when it was built.' : 'Built just now.'}`}>
              <Typography sx={{ ...lbl, cursor: 'help', fontFamily: 'monospace' }}>
                {geom.n_triangles.toLocaleString()} tri · {fmt(geom.mesh_size_mm, 2)} mm
                {geom.mesh_s !== undefined && ` · built in ${fmtSecs(geom.mesh_s)}`}
              </Typography>
            </Tooltip>
          )}
          {builtNote && (
            <Tooltip {...TIP_PROPS} title={`The mesh on screen was built at ${fmt(geom?.mesh_size_mm, 2)} mm and the Mesh tab now asks for ${fmt(mp.mesh_size_mm, 2)} mm. Nothing rebuilds on its own — press Build mesh, or Solve, which meshes at the Mesh tab's size regardless.`}>
              <Typography sx={warn}>⚠ {builtNote}</Typography>
            </Tooltip>
          )}
        </Box>

        {/* ── the same cooling, ON THE MACHINE ───────────────────────────
            User 2026-09-15: "лучше нарисовать 3D модель с катушками (end
            windings) и на ней прямо показывать, куда и сколько тепла может
            отводиться … дай возможность задавать значения прямо в нём — так
            намного удобнее, и определи его в это окно, где всё и задаётся".
            So it lives HERE, under the fields it duplicates, and not in a
            section of its own further down: it is an alternative way to set
            the same `thermalStore` values — one state, two faces — plus what
            the last solve got out of each surface.  It never solves.  All the
            logic is in HeatPathView3D / heatPaths; this is the mount. */}
        <HeatPathView3D
          res={res} geometry={liveGeometry} staleNote={staleNote}
          settings={{
            coolMode, ambientT, airSpeed, fluid, tIn, hConv, flowLpm,
            boreMode, boreAirSpeed, boreTIn, boreFlowLpm,
            shaftExtMm, shaftExtSides, frame, openAirSpeed,
            emissivity, mountG, mountT, endFaces, endFaceSides,
          }}
          onChange={(k, v) => setField(k as Parameters<typeof setField>[0],
                                       v as never)} />

        <Typography sx={{ ...lbl, mt: 0.75, display: 'block' }}>
          Solids only, steady state; the gap and the slot are effective conductivities; cooling acts on the outer surface, on the bore when set{Number(shaftExtMm) > 0 ? ', and down the exposed shaft ends' : ''}{frame === 'open' ? ', plus the end turns and the slot channels in the wash' : ''}.
          <Tooltip {...TIP_PROPS} title="Steady-state conduction (∇·k∇T + q = 0) over the solid cross-section. The air gap is not meshed — it is represented by an effective conductivity COMPUTED from the gap width and the rotor speed (turbulent Taylor–Couette: the rotating film carries far more than still air), and the in-slot bundle by a second one that stands for copper, enamel, impregnation and the liner together. The heat source is the electromagnetic loss density of the operating point set on the Electromagnetic tab, per material. The outlets are the two convection films you set above: one on the outer stator surface, one in the rotor bore — each an h against its own sink temperature, and for a liquid loop that sink follows from the coolant you pump through it. A third outlet appears when you give the shaft an exposed length: the stubs outside the housing, as a fin in ambient air, which is the ONE axial path modelled — the rotor's end faces and the end windings spin inside the closed housing and have nowhere else to send their heat. Steady state means no thermal mass and no duty cycle — this is the temperature after the machine has been at this point long enough to stop changing, which is the worst case for a continuous rating and optimistic for a short burst.">
            <span style={{ borderBottom: '1px dotted var(--text-4)', cursor: 'help', marginLeft: 4 }}>
              assumptions
            </span>
          </Tooltip>
        </Typography>
      </Paper>

      {err && <Alert severity="error" sx={{ mb: 1.5, fontSize: 12 }}>{err}</Alert>}

      {/* ── nothing solved: the cross-section itself ──────────────────────── */}
      {!res && (
        <Paper sx={{ p: 1.25, bgcolor: 'var(--panel)' }}>
          {!err && (
            <Typography sx={{ ...lbl, mb: 0.75, display: 'block' }}>
              Nothing solved for this machine yet — press Solve for the temperature map.
            </Typography>
          )}
          <GeometryMap mesh={geom} busy={geomBusy || busy} error={geomErr} />
        </Paper>
      )}
      {!res && localTable}

      {res && (
        <>
          {/* ── the numbers, one short line each ───────────────────────── */}
          <Paper sx={{ p: 1.25, mb: 1.5, bgcolor: 'var(--panel)' }}>
            <Box sx={{ display: 'grid', gap: 1,
              gridTemplateColumns: 'repeat(auto-fit, minmax(126px, 1fr))' }}>
              {partTiles.map((p) => {
                const c = comps[p.key]!;
                return (
                  // The headline is the MAX and the label says so (user
                  // 2026-09-09: "опять температуры разные" — the coupled loop
                  // and the Electromagnetic tab's fields carry the AVERAGE,
                  // because that is what a bulk resistivity and a bulk Br
                  // mean, while this tile led with the peak and printed the
                  // mean in small type underneath.  Two conventions for one
                  // quantity read as two answers.)
                  <Tile key={p.key} label={p.label}
                    value={fmt(c.max, 0)} value2={fmt(c.avg, 0)} unit="°C"
                    sub={FED_BACK[p.key] ? `max / mean — ${FED_BACK[p.key]}`
                                         : 'max / mean'}
                    colour={p.limit ? hotAccent(c.max, p.limit) : 'var(--text-0)'}
                    tooltip={`${p.tip}  Both numbers are this part's: the first is its hottest element, the second its mean.`
                      + (FED_BACK[p.key]
                        ? `  The mean is ${FED_BACK[p.key]}: the loop feeds a BULK property back into the Electromagnetic run — a resistivity for the copper, a Br for the magnets — and one element's temperature is not one.`
                        : '  Neither goes back into the Electromagnetic run; this part carries no bulk property the field solve reads.')} />
                );
              })}
              <Tile label="Hot-spot" value={fmt(res.T_max, 0)}
                value2={fmt(res.T_min, 0)} unit="°C"
                sub={HOTSPOT_SUB}
                colour={hotAccent(res.T_max, 155)}
                tooltip="The hottest and coldest point anywhere in the model, whatever part they are in. The map's max marker names the part." />
              {/* one tile per BOUNDARY, not one per quantity: an h without the
                  surface it belongs to stopped meaning anything the moment
                  there were two of them. */}
              {/* A liquid jacket PINS the housing at the coolant outlet (user
                  rule): the solver does that with a 100 000 W/m²K clamp, which
                  is a numerical device, not a film — so the tile says the
                  temperature that is held and the jacket's own film in the
                  sub-line (user 2026-09-09: "что это такое?"). */}
              {Number.isFinite(outer.h_jacket) && (outer.h_conv ?? 0) >= 1e5 ? (
                <Tile label="Outer" value={fmt(outer.t_out_c ?? outer.t_sink_c, 0)} unit="°C held"
                  sub={[`liquid`, `jacket h ${fmt(outer.h_jacket, 0)} W/m²K`,
                        fmtW(outer.heat_removed_W)].join(' · ')}
                  tooltip={`${surfaceTip('outer stator surface', outer)} The housing surface is HELD at the coolant outlet temperature — the 100 000 W/m²K the solver applies for that is a numerical clamp (a wall a thousand times better coupled than any film), not a physical coefficient. The jacket's own film is ${fmt(outer.h_jacket, 0)} W/m²K: ${fmt(outer.flow_lpm, 1)} L/min through a ${fmt(outer.channel_width_mm, 0)} × ${fmt(outer.channel_height_mm, 0)} mm helical groove at ${fmt(outer.channel_velocity_mps, 2)} m/s (Re ${fmtExp(outer.re)}, ${String(outer.regime ?? '')}); across ${fmtW(outer.heat_removed_W)} that film alone would put the housing ${fmt((outer.heat_removed_W ?? 0) / Math.max((outer.h_jacket ?? 1) * (outer.area_m2 ?? 1), 1e-9), 1)} K above the coolant — which is why holding the surface at the outlet is the honest simplification.`} />
              ) : (
                <Tile label="Outer" value={fmt(outer.h_conv ?? res.h_conv, 0)} unit="W/m²K"
                  sub={surfaceSub(outer, coolMode)}
                  tooltip={surfaceTip('outer stator surface', outer)} />
              )}
              {showInner && (
                <Tile label="Inner bore" value={fmt(inner?.h_conv, 0)} unit="W/m²K"
                  sub={surfaceSub(inner, boreMode)}
                  tooltip={surfaceTip('rotor bore', inner)} />
              )}
              {showShaft && (
                <Tile label="Shaft ends" value={fmt(shaftEnds?.h_conv, 0)} unit="W/m²K"
                  sub={[
                    `${fmt(shaftEnds?.G_W_per_K, 3)} W/K`,
                    fmtW(shaftEnds?.heat_removed_W),
                    `η ${fmt((shaftEnds?.fin_efficiency ?? 0) * 100, 0)} %`,
                  ].join(' · ')}
                  tooltip={`The shaft outside the housing, as a FIN. ${shaftEnds?.sides ?? 2} × ${fmt(shaftEnds?.length_each_side_mm, 0)} mm of Ø${fmt(shaftEnds?.diameter_mm, 1)} mm shaft (${String(shaftEnds?.diameter_source ?? 'from the geometry')}) turning in ${fmt(shaftEnds?.t_sink_c, 0)} °C air: h ${fmt(shaftEnds?.h_conv, 0)} W/m²K on a cylinder spinning in still air (Re_ω ${fmtExp(shaftEnds?.re_omega)}, ${String(shaftEnds?.regime ?? '—')}), which the steel then has to feed — so the conductance is ${fmt(shaftEnds?.G_W_per_K, 3)} W/K and only ${fmt((shaftEnds?.fin_efficiency ?? 0) * 100, 0)} % of the stub is doing anything (mL ${fmt(shaftEnds?.mL, 2)}). At a shaft mean of ${fmt(shaftEnds?.t_shaft_mean_c, 1)} °C that removes ${fmtW(shaftEnds?.heat_removed_W)} — heat that never crosses the air gap. The rotor's end faces and the end windings are NOT modelled: they spin inside the closed housing and have nowhere else to send their heat.`} />
              )}
              {showOpen && (
                <Tile label="End turns" value={fmt(endWind?.h_conv, 0)} unit="W/m²K"
                  sub={[
                    `${fmt(endWind?.G_W_per_K, 3)} W/K`,
                    fmtW(endWind?.heat_removed_W),
                    `A ${fmt((endWind?.area_m2 ?? 0) * 1e4, 1)} cm²`,
                  ].join(' · ')}
                  tooltip={`The end windings in the airflow — the path an OPEN machine has and a housed one does not. ${endWind?.n_coils ?? '—'} coils × ${endWind?.n_sides ?? 2} sides of ${fmt(endWind?.end_turn_length_mm, 2)} mm end turn, a ${fmt(endWind?.bundle_thickness_mm, 2)} × ${fmt(endWind?.bundle_width_mm, 2)} mm bundle with ${fmt(endWind?.perimeter_mm, 2)} mm of it in the wash (the tooth-facing face is not) → A_ew ${fmt((endWind?.area_m2 ?? 0) * 1e4, 1)} cm². Their LENGTH is (k_end − 1)·L_stack/2 per side at k_end ${fmt(endWind?.k_end, 3)}, taken from ${String(endWind?.k_end_source ?? '—')} — the same factor the copper loss was billed at, never a second estimate. Film: ${String(endWind?.regime ?? '—')} at ${fmt(endWind?.air_speed_mps, 1)} m/s (${String(endWind?.air_speed_source ?? '')}), Re ${fmtExp(endWind?.re)}, on the bundle's equivalent Ø${fmt(endWind?.d_equiv_mm, 2)} mm. Copper's fin efficiency is taken as 1 — the end turn is the same conductor, k ≈ 400 W/m·K and a few millimetres thick. At a winding mean of ${fmt(endWind?.t_winding_mean_c, 1)} °C that removes ${fmtW(endWind?.heat_removed_W)}, which never has to cross the iron.`} />
              )}
              {showOpen && (
                <Tile label="Slot channels" value={fmt(slotCh?.h_conv, 0)} unit="W/m²K"
                  sub={[
                    `${fmt(slotCh?.G_W_per_K, 3)} W/K`,
                    fmtW(slotCh?.heat_removed_W),
                    `D_h ${fmt(slotCh?.hydraulic_diameter_mm, 2)} mm`,
                  ].join(' · ')}
                  tooltip={`The axial channels between neighbouring coils, ventilated. ${slotCh?.n_channels ?? '—'} of them; the free cross-section (${fmt(slotCh?.cross_section_mm2, 1)} mm² over the whole machine) and the wetted perimeter (${fmt(slotCh?.wetted_perimeter_per_slot_mm, 2)} mm per slot) are MEASURED on the mesh — what is left of a slot once the wires are in it is not a number anybody types — giving D_h ${fmt(slotCh?.hydraulic_diameter_mm, 2)} mm. Film: ${String(slotCh?.regime ?? '—')} duct flow at ${fmt(slotCh?.air_speed_mps, 1)} m/s, Re ${fmtExp(slotCh?.re)}, Nu ${fmt(slotCh?.nu, 2)}. The fully-developed Nusselt is used on a duct only a few diameters long, which UNDER-reads it — the entrance region exchanges considerably more. At a channel-air mean of ${fmt(slotCh?.t_air_mean_c, 1)} °C that removes ${fmtW(slotCh?.heat_removed_W)}.`} />
              )}
              {gapK !== undefined && (
                <Tile label="Air gap" value={fmt(gapK, 2)} unit="W/m·K"
                  sub={[
                    `Ta ${fmtExp(gap?.Ta ?? res.gap_Ta)}`,
                    `Nu ${fmt(gap?.Nu ?? res.gap_Nu, 1)}`,
                    gap?.regime ? String(gap.regime) : null,
                    gap?.delta_mm !== undefined ? `δ ${fmt(gap.delta_mm, 2)} mm` : null,
                  ].filter(Boolean).join(' · ')}
                  tooltip={`Effective conductivity of the air gap — a RESULT, computed for this machine from the gap width (${fmt(gap?.delta_mm, 2)} mm at a mean radius of ${fmt(gap?.r_mean_mm, 1)} mm) and the rotor speed, never a number typed in. The rotating film is turbulent well before a machine reaches its rated speed (Taylor number ${fmtExp(gap?.Ta ?? res.gap_Ta)}, Nusselt ${fmt(gap?.Nu ?? res.gap_Nu, 1)}, regime ${String(gap?.regime ?? '—')}), so it carries ${gap?.k_air ? `about ${fmt((gapK ?? 0) / gap.k_air, 1)}×` : 'far more than'} still air (${fmt(gap?.k_air, 3)} W/m·K). It is what sets the rotor and magnet temperature of a machine whose bore is not cooled — everything they make crosses this gap.${gap?.note ? ` ${String(gap.note)}` : ''}`} />
              )}
              {budget && rotorW !== null && (
                <Tile label="Rotor heat out" value={`${fmt(gapOutW, 0)} gap`}
                  unit="W"
                  value2={`${fmt(boreOutW, 0)} bore`}
                  // ONE short line (user 2026-09-10: "не надо так подробно
                  // расписывать") — the two shares of what the rotor makes.
                  // Every other watt is in the tooltip.
                  sub={`${pct(split?.gap_pct, gapOutW)} / ${pct(split?.bore_pct, boreOutW)} of ${fmt(rotorW, 0)} W`}
                  colour={boreOutW >= 0.5 * rotorW ? 'var(--text-0)' : '#fbbf24'}
                  tooltip={`THE TWO WAYS OUT of the rotor on this 2-D section: ${fmt(gapOutW, 0)} W across the AIR GAP into the stator (${pct(split?.gap_pct, gapOutW)} of what it makes) and ${fmt(boreOutW, 0)} W off the BORE surface (${pct(split?.bore_pct, boreOutW)}).${showShaft ? ` Beside them, ${fmt(split?.axial_shaft_ends_W ?? budget.shaft_ends_W, 1)} W leaves AXIALLY down the ${shaftEnds?.sides ?? 2} exposed shaft end(s) — a lumped path out of the page, not a facet of this section, so it is not folded into either number.` : ''}${split?.closure_W != null ? ` They add back to the rotor's own generation to within ${fmt(split.closure_W, 2)} W.` : ''} Integrated on the solved mesh (machine watts). The rotor side makes ${fmt(rotorW, 0)} W (magnets, shaft, sleeve, rotor iron); ${fmt(rotorOutW, 0)} W of it leaves through the shaft — ${fmt(budget.bore_W, 0)} W through the bore${showShaft ? ` and ${fmt(budget.shaft_ends_W, 1)} W down the ${shaftEnds?.sides ?? 2} shaft end(s) sticking out of the housing` : ' (nothing sticks out of the housing, so there are no shaft ends to lose heat from — the rotor faces and the end windings turn inside it and have nowhere to send theirs)'} — and ${fmt(budget.gap_W, 0)} W crosses the air gap into the stator, through the sleeve. The housing removes ${fmt(budget.housing_W, 0)} W in total.${showOpen ? ` This machine has no housing: another ${fmt(budget.end_windings_W, 1)} W leaves off the end turns and ${fmt(budget.slot_channels_W, 1)} W out of the slot channels, straight into the wash without crossing any iron.` : ''} Balance closes to ${fmt(budget.residual_pct, 1)} % of the ${fmt(budget.losses_W, 0)} W of losses on the mesh. A rotor that must be cooled through the shaft wants the first number to be the big one.`} />
              )}
              <Tile label="Total loss" value={fmt(res.P_loss_total_W, 0)} unit="W"
                sub={`Cu ${fmt(res.P_cu_W, 0)} · Fe ${fmt(res.P_fe_W, 0)}`}
                tooltip={`All the heat this solve had to get rid of: copper ${fmt(res.P_cu_W, 0)} W, iron ${fmt(res.P_fe_W, 0)} W, magnet eddy ${fmt(res.P_mag_eddy_W, 1)} W. It is the electromagnetic loss of the operating point set on the Electromagnetic tab, at the coil temperature set there — the coupled solve below is what makes that temperature agree with the answer.`} />
            </Box>

            {/* ── WHERE IT ACTUALLY LEFT, on a joint in still air ────────────
                ONE short line, everything else in the tooltip (2026-09-14).
                On a robot joint the interesting number is not a film
                coefficient: it is that the housing gives the room a few watts
                and the BOLTS take the rest, which is what the flange has to be
                designed for.  The second half is the stator/rotor share — the
                answer to "which side of this machine do I have to cool". */}
            {showRobot && budget && (
              <Tooltip {...TIP_PROPS} title={`Every watt that left the model, on its own path, integrated on the solved mesh. HOUSING ${fmt(budget.housing_W, 1)} W = ${fmt(budget.housing_convection_W, 1)} W of natural convection (Churchill–Chu on the cylinder, h ${fmt(outer.h_conv, 1)} W/m²K) + ${fmt(budget.housing_radiation_W, 1)} W of radiation at ε ${fmt(outer.emissivity as number, 2)} — the split is exact, not apportioned: both films act on the same area and the same ΔT, so it is the ratio of the two coefficients. MOUNT ${fmt(budget.mount_W, 1)} W = ${fmt(mount?.G_W_per_K, 3)} W/K × (${fmt(mount?.t_housing_mean_c, 1)} − ${fmt(mount?.t_sink_c, 0)} °C), the bolted flange (${String(mount?.t_sink_source ?? '')}) — an ASSUMED conductance until this joint is measured. END FACES ${fmt(budget.end_faces_W, 1)} W over ${endFaceBlk?.sides ?? 0} exposed end(s): end turns ${fmt(endFaceBlk?.winding?.heat_removed_W, 1)} W, stator core ${fmt(endFaceBlk?.stator?.heat_removed_W, 1)} W, rotor core ${fmt(endFaceBlk?.rotor?.heat_removed_W, 1)} W, magnets ${fmt(endFaceBlk?.magnet?.heat_removed_W, 2)} W. BORE ${fmt(budget.bore_W, 1)} W off the open inner diameter.${sSplit ? ` The STATOR side makes ${fmt(sSplit.stator_W, 1)} W and takes ${fmt(sSplit.gap_in_W, 1)} W more across the gap; of that ${fmt(sSplit.housing_pct, 0)} % leaves through the housing, ${fmt(sSplit.mount_pct, 0)} % through the mount and ${fmt(sSplit.end_faces_pct, 0)} % off its end faces (closure ${fmt(sSplit.closure_W, 2)} W).` : ''} The whole budget closes to ${fmt(budget.residual_pct, 2)} % of ${fmt(budget.losses_W, 1)} W.`}>
                <Typography sx={{ ...lbl, mt: 0.75, display: 'inline-block', cursor: 'help',
                  borderBottom: '1px dotted var(--text-4)', fontFamily: 'monospace' }}>
                  out: housing {fmt(budget.housing_W, 1)} W ({fmt(budget.housing_convection_W, 1)} conv + {fmt(budget.housing_radiation_W, 1)} rad)
                  {' · '}mount {fmt(budget.mount_W, 1)} W
                  {' · '}end faces {fmt(budget.end_faces_W, 1)} W
                  {' · '}bore {fmt(budget.bore_W, 1)} W
                  {sSplit && (
                    <>
                      {' · '}stator side {fmt(sidePct(statorSideW), 0)} %
                      {' / rotor side '}{fmt(sidePct(rotorSideW), 0)} %
                    </>
                  )}
                </Typography>
              </Tooltip>
            )}

            {/* One short line, tooltip for the rest — a sleeve is a series
                resistance the bare-rotor intuition does not have. */}
            {sleeve?.present && (
              <Tooltip {...TIP_PROPS} title={`A retaining sleeve sits in the heat path between the magnets and the air gap: ${fmt(sleeve.thickness_mm, 2)} mm of it at k ${fmt(sleeve.k, 2)} W/m·K. Everything the rotor and the magnets make has to cross it BEFORE it reaches the gap, so the magnet temperature of a sleeved rotor is not the magnet temperature of a bare one at the same losses.`}>
                <Typography sx={{ ...lbl, mt: 0.75, display: 'inline-block', cursor: 'help',
                  borderBottom: '1px dotted var(--text-4)', fontFamily: 'monospace' }}>
                  sleeve in the gap path — {fmt(sleeve.thickness_mm, 2)} mm · k {fmt(sleeve.k, 2)} W/m·K
                </Typography>
              </Tooltip>
            )}

            {/* The insulation, in one line.  Before 2026-09-07 the liner and the
                enamel were lumped into the winding's k and the slot around the
                wires was simply not solved; they are meshed domains now, and
                the k each of them got — and whether it came from the material
                card or from our own default — is the difference between a
                winding temperature an engineer can argue with and one they
                cannot. */}
            {mu && (
              <Tooltip {...TIP_PROPS} title={`What the slot and the gap are filled with, and what each was solved at. Insulation ${mu.liner?.material ?? '—'} at k ${fmt(mu.liner?.k, 3)} W/m·K (${mu.liner?.source ?? '—'}), wire enamel ${mu.enamel?.material ?? '—'} at k ${fmt(mu.enamel?.k, 3)} W/m·K (${mu.enamel?.source ?? '—'}), wire coating k ${fmt(mu.slot_fill?.k, 3)} W/m·K (${mu.slot_fill?.source ?? '—'}${mu.slot_fill?.note ? ` — ${mu.slot_fill.note}` : ''}), air gap k_eff ${fmt(mu.gap_air?.k_eff, 3)} W/m·K and rotor pocket air k ${fmt(mu.pocket_air?.k, 4)} W/m·K. The winding itself is solved at k ${fmt(mu.winding?.k, 3)} W/m·K — ${mu.winding?.model ?? ''}. ${air?.note ?? ''} Switch any of them off in the part tree under the picture.`}>
                <Typography sx={{ ...lbl, mt: 0.5, display: 'inline-block', cursor: 'help',
                  borderBottom: '1px dotted var(--text-4)', fontFamily: 'monospace' }}>
                  insulation — liner k {fmt(mu.liner?.k, 2)} · enamel k {fmt(mu.enamel?.k, 2)} · fill k {fmt(mu.slot_fill?.k, 2)} W/m·K
                  {mu.slot_fill?.source === 'default' ? ' (fill: default)' : ''}
                </Typography>
              </Tooltip>
            )}

            {/* ── these temperatures as a row of the Compare table ───────────
                User 2026-09-07: "нужно везде сделать такую же кнопку … в
                температурном нужно все максимальные температуры всех частей
                мотора сравнивать между собой".  The row carries the peak of
                EVERY part the solve resolved — not the five tiles above — so two
                cooling designs can be read against each other part by part. */}
            <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap', mt: 1.25 }}>
              {/* ONE press, two tables: the row lands in the stack right below
                  (the Configure tab's way of working, asked for 2026-09-07) AND
                  in the permanent Compare library. */}
              <AddResultToCompareButton kind="thermal" onLocalAdd={addLocal} />
            </Box>
          </Paper>

          {/* ── the same variants, stacked side by side ────────────────── */}
          {localTable}

          {/* ── ONE result picture with a menu ─────────────────────────── */}
          <Paper sx={{ p: 1.25, bgcolor: 'var(--panel)' }}>
            <ThermalMap
              res={res}
              view={view} onView={(v: ThermView) => setField('view', v)}
              eqTemp={eqTemp} onEqTemp={(v) => setField('eqTemp', v)}
              showFlux={showFlux} onShowFlux={(v) => setField('showFlux', v)}
              staleNote={staleNote} />
          </Paper>
        </>
      )}

      {/* The duty cycle used to live HERE, at the bottom.  It is at the top of
          the cooling block now, under the frame — see the note beside it. */}

      <CoupledSection />
    </Box>
  );
};

export default ThermalPanel;
