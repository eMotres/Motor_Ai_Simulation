/**
 * ConfiguratorPanel — the simple tuner ("Configure" tab).
 *
 * Pick a reference motor (a FEM-extracted PASSPORT) and tune only what a
 * plan-1 user is allowed to change — lamination length, turns, wire thickness,
 * the winding connection — plus the operating point (current & speed).
 * Torque / power / voltage / efficiency / mass recompute INSTANTLY from the
 * passport via scaleMotor() — no FEM.  Snapshot configs and compare them.
 *
 * The physics is analytical and FEM-validated (see motorScaling.ts):
 *   length L : T,EMF,iron,magnet,mass ∝ L ; R = R_active·L + R_end
 *   turns  N : T,EMF ∝ N ; R ∝ N
 *   wire   h : area ∝ wire_height → R ∝ 1/h ; Imax ∝ h   (wire_width FIXED)
 *   conn  nP : T,EMF ∝ nP0/nP ; R ∝ (nP0/nP)² ; Imax ∝ nP/nP0
 */
import React, { useEffect, useMemo, useState } from 'react';
import {
  Box, Typography, Slider, ToggleButton, ToggleButtonGroup, Button,
  IconButton, Alert, CircularProgress,
} from '@mui/material';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import AddIcon from '@mui/icons-material/Add';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import BoltIcon from '@mui/icons-material/Bolt';
import { useMotorStore } from '../../stores/motorStore';
import { TextPromptDialog, type TextPromptState } from '../common/PromptDialogs';
import { pickCable } from '../../lib/cableTable';
import {
  scaleMotor, maxCurrent, type Passport, type Knobs, type ScaledResult, type PwmVariant,
} from '../../lib/motorScaling';
import {
  REFERENCE_PASSPORTS, windingConnections, connLabel, fetchCatalogReferencesAnswer, type ReferenceMotor,
} from '../../lib/referencePassports';
import GeometryProjections from './GeometryProjections';
import GreekLabel from './GreekLabel';
import BatteryPanel, { type Battery, defaultBattery } from './BatteryPanel';
import { useAuth } from '../../contexts/AuthContext';
import { referenceOfDefault } from '../../lib/accessUi';
import { CONFIGURE_REFID_LS, isOwnChoice } from './configureChoice';
import { CardBadge } from '../common/CardBadge';
import PerformanceCharts from './PerformanceCharts';
import { SHOW_CONFIGURE_CHARTS } from '../../lib/configuratorFlags';
import {
  driveRowTiles, extraLossTile, lossTailTiles, totalLossShown, lossDensityShown, systemEfficiency, motorEfficiency, controllerLossW, tempRowTiles, type DriveTileSpec, type TempTileSpec,
} from '../../lib/configuratorTiles';
import {
  isPropellerCooled, allowedPropellers, effectivePropeller, defaultPropellerFor, readCoolChoice, writeCoolChoice, PROP_CHOICE_LS, DEFAULT_AMBIENT_C,
  seriesAt, currentForTorque, tempLimits, judgeTemps, zoneGradient, zoneSamples, modelLabel, vendorLabel,
  type CoolChoice, type PropSeries, type PropSummary,
} from '../../lib/configuratorPropeller';
import { fetchPropellers, fetchSeries } from '../../lib/propellerApi';
import { estimateThermal } from '../../lib/thermalEstimate';
import ConfiguratorThermal from './ConfiguratorThermal';
import ChargePanel from './ChargePanel';
import { canCharge } from '../../lib/generatorCharge';
import { useWireStock } from '../materials/useWireStock';
import {
  batteryFromPack, readBatteryEdit, writeBatteryEdit, clearBatteryEdit, dropStockEdits, wantedBattery, sameBattery, BATTERY_BY_MACHINE_LS,
} from '../../lib/configuratorBattery';
import { isSizeInStock, nearestStockSizes, formatNearestSizes } from '../../lib/wireStock';
import { listDevices } from '../controller/controllerApi';
import { presetKnobs, presetDiff, presetOfBuild, type Preset } from '../../lib/configuratorPresets';
import {
  physicalRanges, narrowRange, speedLimit, overflows, lineVoltageWarning,
  readOverrides, writeOverrides, RANGES_LS_V2, WIRE_STEP_MM, DEFAULT_MODULATION,
  type KnobKey, type KRange, type Overrides, type PhysRange, type LimitBasis,
} from '../../lib/configuratorLimits';
import {
  fetchConfigureContext, saveLMax, catalogIdOf, type ConfigureContext,
} from '../../lib/configureContextApi';
import { useTranslation } from 'react-i18next';
import { nsT } from '../../i18n/nsT';
import i18n from '../../i18n';
import {
  usableVariants, variantFacts, readVariant, buildTuned, limitProblems,
  pickDrive, readDriveChoice, writeDriveChoice, driveText, DRIVE_LS,
  variantDevices, variantCarriers, switchDevice, switchCarrier, resolveVariant, pairKey, carrierLabel,
  type DriveRecord, type DeviceLimits,
} from '../../lib/configuratorDrive';
import { getDraft, patchDraft, draftIdFromUrl, bestDraftResult, type AgentDraft } from '../../lib/agentDrafts';
import { resolveDraftTarget, modelState, pickReference } from '../../lib/configuratorGuard';
import MyAgentDraftsBlock from './MyAgentDraftsBlock';

const tx = nsT('controller');   // every user-visible string (EN source, ZH mirror — docs/I18N.md)
/** In Chinese the captions keep their unit symbols as written (mm, rpm, ns), not MM / RPM. */
const unitCase = () => (i18n.language?.startsWith('zh') ? { textTransform: 'none' as const } : undefined);
const n0f = (x: number) => String(Number(x.toFixed(0)));

/** The machine's remembered drive choice (Sine | PWM + variant), laid over
 *  `k`.  Nothing remembered = `k` itself, so Sine stays exactly as it was. */
const withDrive = (k: Knobs, refId: string): Knobs => {
  let raw: string | null = null;
  try { raw = localStorage.getItem(DRIVE_LS); } catch { /* ignore */ }
  const c = readDriveChoice(raw, refId);
  return c ? { ...k, ...c } : k;
};

const baseKnobs = (p: Passport): Knobs => ({
  N: p.N0, L_mm: p.L0_mm, wireH_mm: p.wireH0_mm, nP: p.nP0, I_A: p.I0_A, rpm: p.rpm0,
  // The passport's own split until a machine is loaded — the base point IS the
  // measured machine, so the turns ratio starts at 1 (absent = 1 either way).
  split: p.wire_split0 ?? 1,
});

interface SavedConfig {
  id: string;
  name: string;
  refId: string;
  knobs: Knobs;
  result: ScaledResult;
  iMax: number;
  battery?: Battery;   // snapshot of the battery this config was saved with
  /** the preset (die configuration) it was made from, when it had one */
  presetConfig?: string | null;
  /** the propeller it was saved with (id + the label to show), for a propeller-cooled machine */
  propeller?: { id: string; label: string } | null;
  /** the drive (Sine | PWM + device + carrier) it was saved with */
  drive?: DriveRecord;
}

const LS_KEY = 'configurator.configs.v1';
const fmt = (v: number, d = 1) => (Number.isFinite(v) ? v.toFixed(d) : '—');
const pctDelta = (cur: number, base: number) => (base ? ((cur - base) / base) * 100 : 0);

// theme bits (match ComparePanel)
const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1 } as const;
const LABEL = { fontSize: 11, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.03em' } as const;
const TH = { px: 1.25, py: 0.7, fontSize: 10, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.03em', whiteSpace: 'nowrap', textAlign: 'right', borderBottom: '1px solid var(--line-soft)', bgcolor: 'var(--panel-2)' } as const;
const TD = { px: 1.25, py: 0.5, fontSize: 12, whiteSpace: 'nowrap', textAlign: 'right', borderBottom: '1px solid var(--app-bg)', fontFamily: 'monospace', color: 'var(--text-1)' } as const;

// The slider RANGES are physical limits now (lib/configuratorLimits.ts, owner
// 2026-10-05); an admin's local edit can only NARROW them, per machine.
const KNOBS_LS  = 'configurator.knobs.v1';
const REFID_LS  = CONFIGURE_REFID_LS;

// a small editable range endpoint (the min / max flanking a slider)
const RangeEnd: React.FC<{ value: number; d: number; title: string; onCommit: (v: number) => void }> = ({ value, d, title, onCommit }) => {
  const [t, setT] = React.useState<string | null>(null);
  return (
    <input title={title} type="number" value={t ?? fmt(value, d)}
      onChange={(e) => setT(e.target.value)}
      onBlur={(e) => { const v = parseFloat(e.target.value); if (Number.isFinite(v)) onCommit(v); setT(null); }}
      onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
      style={{ width: 44, flexShrink: 0, background: 'transparent', border: '1px solid #233149', borderRadius: 4, color: 'var(--text-3)', fontSize: 10, fontWeight: 600, fontFamily: 'monospace', textAlign: 'center', padding: '1px 2px' }} />
  );
};

// ── one tunable knob row: label + live value (+ Δ vs reference) + slider ──
const KnobSlider: React.FC<{
  label: string; unit?: string; value: number; base: number;
  min: number; max: number; step: number; d?: number;
  onChange: (v: number) => void; onRangeChange?: (min: number, max: number) => void; warn?: boolean;
  /** small grey caption after the unit — e.g. the peak value of an rms field */
  sub?: string;
  /** where its maximum comes from — set on the TITLE row, right after the title, in
   *  the title's own style (owner 2026-10-05: no small print); tooltip for details */
  limitNote?: { text: string; tip: string; hand?: boolean };
  /** admin: clear a hand-set maximum (the "default" rule applies again) */
  onClearHand?: () => void;
  /** CSS background of the slider rail: the thermal zones (green below the temperature limits, red beyond) */
  zone?: string | null;
  /** a value derived from something else (the current that the propeller needs): shown, not editable */
  disabled?: boolean;
}> = ({ label, unit, value, base, min, max, step, d = 1, onChange, onRangeChange, warn, sub, limitNote, onClearHand, zone, disabled }) => {
  const delta = pctDelta(value, base);
  const [txt, setTxt] = React.useState<string | null>(null);   // non-null while the field is being typed in
  return (
    <Box sx={{ mb: 1.25 }}>
      <Box sx={{ display: 'flex', alignItems: 'baseline', columnGap: 0.75, rowGap: 0.25, mb: 0.25, flexWrap: 'wrap' }}>
        <Typography sx={{ ...LABEL, flex: '1 1 120px', minWidth: 0 }} title={limitNote?.tip}>
          {label}
          {/* in Chinese the caption keeps its unit symbols as written (mm, rpm), not MM / RPM */}
          {limitNote && <Box component="span" sx={unitCase()}>{' · '}{limitNote.text}</Box>}
          {limitNote?.hand && onClearHand && (
            <Box component="span" onClick={onClearHand} title={tx('configureLimits.clearHandTip')}
              sx={{ ml: 0.75, color: '#60a5fa', cursor: 'pointer', '&:hover': { textDecoration: 'underline' } }}>
              {tx('configureLimits.clearHand')}
            </Box>
          )}
        </Typography>
        <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 0.75, flexShrink: 0, ml: 'auto' }}>
        <input value={txt ?? fmt(value, d)} type="number" step={step} readOnly={disabled}
          onChange={(e) => { setTxt(e.target.value); const v = parseFloat(e.target.value); if (Number.isFinite(v) && v >= min && v <= max) onChange(v); }}
          onBlur={(e) => { const v = parseFloat(e.target.value); if (Number.isFinite(v)) onChange(Math.min(max, Math.max(min, v))); setTxt(null); }}
          onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
          style={{ width: 66, background: 'transparent', border: '1px solid var(--line)', borderRadius: 4, color: warn ? '#f87171' : 'var(--text-0)', fontSize: 14, fontWeight: 700, fontFamily: 'monospace', textAlign: 'right', padding: '1px 5px' }} />
        {unit ? <Box component="span" sx={{ fontSize: 11, color: 'var(--text-3)' }}>{unit}</Box> : null}
        {sub ? <Box component="span" sx={{ fontSize: 11, color: 'var(--text-3)', fontFamily: 'monospace' }}>{sub}</Box> : null}
        {Math.abs(delta) >= 0.5 && (
          <Typography sx={{ fontSize: 11, color: delta > 0 ? '#60a5fa' : 'var(--text-2)', fontFamily: 'monospace', width: 50, textAlign: 'right' }}>
            {delta > 0 ? '+' : ''}{fmt(delta, 0)}%
          </Typography>
        )}
        </Box>
      </Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
        {onRangeChange && <RangeEnd value={min} d={d} title={tx('configureLimits.rangeMinTip')} onCommit={(v) => onRangeChange(Math.min(v, max - step), max)} />}
        <Slider value={value} min={min} max={max} step={step}
          onChange={(_, v) => onChange(v as number)} size="small" disabled={disabled}
          sx={{ flex: 1, color: warn ? '#f87171' : '#3b82f6', py: 0.5, '& .MuiSlider-thumb': { width: 13, height: 13 },
                ...(zone ? { '& .MuiSlider-rail': { background: zone, opacity: 0.9, height: 4 } } : {}) }} />
        {onRangeChange && <RangeEnd value={max} d={d} title={tx('configureLimits.rangeMaxTip')} onCommit={(v) => onRangeChange(min, Math.max(v, min + step))} />}
      </Box>
    </Box>
  );
};

/** the one width of every result tile */
const TILE_W = 132;

// ── one result tile: value + unit + Δ vs reference ──
const MetricTile: React.FC<{
  label: string; value: number | null; unit: string; d?: number; base: number; goodHi?: boolean;
  /** why the value is "—" (shown in the tooltip instead of the vs-reference line) */
  blankTip?: string;
  /** printed instead of the number (a temperature over its limit reads "> 180") */
  display?: string;
  /** no "vs the reference design" line in the tooltip (a quantity with no reference, like a temperature) */
  plain?: boolean;
  /** ABSOLUTE colouring for quantities that have a meaning of their own
   *  (current density): 'ok' | 'warn' | 'bad' overrides the vs-reference
   *  colour, because 9 A/mm² is fine whether or not it grew (user
   *  2026-08-26: "why is the current highlighted in red?"). */
  absLevel?: 'ok' | 'warn' | 'bad';
  /** What the number IS, when the label cannot say it (the heat split's
   *  terms).  Prepended to the vs-reference line in the hover title. */
  tip?: string;
}> = ({ label, value, unit, d = 1, base, goodHi, absLevel, tip, blankTip, display, plain }) => {
  const blank = value == null && display == null;
  const delta = value == null ? 0 : pctDelta(value, base);
  // No "% vs ref" line under every tile (user 2026-08-26) — the deltas are
  // carried by COLOUR only; the header's Reset button returns to the
  // reference design.
  const show = false;
  const good = goodHi === undefined ? null : goodHi ? delta > 0 : delta < 0;
  const dColor = good === null ? 'var(--text-2)' : good ? '#4ade80' : '#f87171';
  void show; void dColor;
  // Compact (user 2026-08-25 "too spread out"): fixed narrow tiles in a
  // dense wrap — the same visual weight as the Simulation summary cells.
  // The tile shows the VALUE; how it moved against the reference design is
  // told by the value's colour and by the tooltip (green = better, red =
  // worse, grey = neutral quantity).
  const changed = Math.abs(delta) >= 0.5;
  return (
    // ONE fixed width and a fixed value line: a number changing (or becoming "—") never moves
    // another tile (owner 2026-10-05: the block must not jump when the drive is toggled)
    <Box sx={{ ...PANEL, p: 0.9, flex: '0 0 auto', width: TILE_W, boxSizing: 'border-box' }}
      title={(tip ? `${tip}  ` : '') + (blank ? (blankTip ?? '') : plain ? '' : changed
        ? tx('configure.vsRef', { delta: `${delta > 0 ? '+' : ''}${fmt(delta, 1)}`, base: fmt(base, d), unit })
        : tx('configure.sameAsRef'))}>
      <Typography sx={{ ...LABEL, fontSize: 9.5, whiteSpace: 'nowrap',
        overflow: 'hidden', textOverflow: 'ellipsis' }}><GreekLabel text={label} /></Typography>
      <Typography sx={{ fontSize: 16, fontWeight: 800,
        color: blank ? 'var(--text-3)' : absLevel
          ? (absLevel === 'bad' ? '#f87171' : absLevel === 'warn' ? '#fbbf24' : '#4ade80')
          : (changed && good !== null ? dColor : 'var(--text-0)'),
        fontFamily: 'monospace', lineHeight: 1.2, whiteSpace: 'nowrap', minHeight: 19 }}>
        {blank ? '—' : (display ?? fmt(value as number, d))}{!blank && <Box component="span" sx={{ fontSize: 10.5,
          color: 'var(--text-3)', ml: 0.5 }}>{unit}</Box>}
      </Typography>
    </Box>
  );
};

/** Tooltip of the torque / Kt / Km tiles (owner 2026-09-30): which 3-D factor
 *  they carry — one factor for all of them, so torque and Kt agree. */
const KT_BASIS_TIP = (basis: string) => (basis === '3-D'
  ? tx('configure.ktTip3d')
  : basis === '3-D flux'
    ? tx('configure.ktTipFlux')
    : tx('configure.ktTip2d'));

const ConfiguratorPanel: React.FC = () => {
  const { isAdmin, defaultMotor, resolved: authResolved } = useAuth();   // editing the slider ranges is admin-only
  // FEM-characterised catalog motors (fetched) come first; the built-in
  // REFERENCE_PASSPORTS stay as a seed/fallback.
  const [catalogRefs, setCatalogRefs] = useState<ReferenceMotor[]>([]);
  // Has the references fetch been ANSWERED (an empty list included)?  Until it has, and
  // until the loaded machine has been matched against the answer, the panel says it is
  // loading — it never shows "no configurator model" for a machine it has not looked up.
  const [refsAnswered, setRefsAnswered] = useState(false);
  const [matchChecked, setMatchChecked] = useState(false);
  // Retry until the catalog answers, and refetch on catalog changes — a
  // single failed fetch (server restart window) left the panel with ONLY the
  // built-in 200 mm reference forever, so no loaded motor could ever match
  // (user 2026-08-25: "200 mm again").  Same illness as the Motors-tab
  // canWrite freeze, same cure.
  useEffect(() => {
    let dead = false;
    const load = () => fetchCatalogReferencesAnswer()
      .then((a) => {
        if (dead) return;
        if (a.ok) { setCatalogRefs(a.refs); setRefsAnswered(true); }
        else setTimeout(load, 3000);              // the server did not answer: still loading
      })
      .catch(() => { if (!dead) setTimeout(load, 3000); });
    load();
    const on = () => load();
    window.addEventListener('family-changed', on);
    return () => { dead = true; window.removeEventListener('family-changed', on); };
  }, []);
  const [liveMatched, setLiveMatched] = useState(false);
  // Signature of the LOADED build — the knobs adopt it whenever it changes
  // (machine loaded / rebuilt), and never while the user is tuning.
  const liveSigRef = React.useRef<string>('');
  /** The knobs the panel opened with for THIS machine — the "ref" every
   *  percentage is measured against, and what Reset returns to. */
  const [refKnobs, setRefKnobs] = useState<Knobs | null>(null);
  const allRefs = useMemo(() => [...catalogRefs, ...REFERENCE_PASSPORTS], [catalogRefs]);
  const [refId, setRefId] = useState<string>(() => {
    try { const r = localStorage.getItem(REFID_LS); if (r) return r; } catch { /* ignore */ }
    return REFERENCE_PASSPORTS[0]?.id ?? '';
  });
  useEffect(() => { try { localStorage.setItem(REFID_LS, refId); } catch { /* ignore */ } }, [refId]);
  // ── Follow the LOADED machine (user 2026-08-25: loading CIANO28 150_35 new
  //    still showed the 200 mm reference).  On every machine load the catalog
  //    dispatches 'sim-operating-point'; match the live geometry against the
  //    references (names differ between the family catalog and the cards, so
  //    match by the machine itself: slots, poles, OD, magnet height) and
  //    auto-select the matching passport.  No match → keep the current pick.
  const liveGeo = useMotorStore((s) => s.geometry) as Record<string, unknown> | null;
  // DEFAULT MOTOR (owner 2026-10-05): an admin can name the die + configuration
  // Configure opens on for an account.  While that choice is "pinned", the
  // live-geometry auto-match below stays out of the way - the live geometry is the
  // workspace's leftover, not something the user loaded.  The first machine the
  // user LOADS (the 'sim-operating-point' event) unpins it.
  const defaultPinned = React.useRef(false);
  // Did he have a machine of his own before this session?  Read ONCE, before the
  // effect below writes the automatic seed into the same key.
  const [hadOwnChoice] = useState(() => {
    try { return isOwnChoice(localStorage.getItem(CONFIGURE_REFID_LS)); } catch { return false; }
  });
  useEffect(() => {
    const pick = (fromEvent = false) => {
      const g = useMotorStore.getState().geometry as Record<string, any> | null;
      if (!g) return;
      if (refsAnswered) setMatchChecked(true);   // looked up against the ANSWER, not the seed
      if (fromEvent) defaultPinned.current = false;
      else if (defaultPinned.current) return;
      const near = (a: unknown, b: unknown, tol: number) =>
        Number.isFinite(Number(a)) && Number.isFinite(Number(b))
        && Math.abs(Number(a) - Number(b)) <= tol;
      // Cross-section match FIRST (the stamped lamination) …
      const sameSection = allRefs.filter((r) =>
        Number(r.geo?.numSlots) === Number(g.num_slots)
        && Number(r.geo?.numPoles) === Number(g.num_poles)
        && near(r.geo?.statorOR_mm, Number(g.stator_outer_radius), 0.5)
        && near(r.geo?.magnetHeight_mm, Number(g.magnet_height), 0.3));
      // … then the closest BUILD.  A die can carry many configurations (the
      // CILN28 series: 40 / 160 / 220 mm stacks on one lamination), and they
      // all match the section — picking the first one made Configure scale a
      // 220 mm passport down to a 40 mm machine and every number came out
      // wrong (user 2026-08-26: "I load the motor — I get different data").
      const dist = (r: ReferenceMotor) => {
        const p0 = r.passport;
        const rel = (a: number, b: number) =>
          (a > 0 && b > 0) ? Math.abs(Math.log(a / b)) : 5;
        return rel(p0.L0_mm, Number(g.motor_length))
             + rel(p0.N0, Number(g.num_wires_per_slot))
             + rel(p0.wireH0_mm, Number(g.wire_height));
      };
      // Same build -> closest magnet / outer-radius geometry -> a card that IS a catalogue machine
      // (a legacy duplicate of the geometry has no pack, controller or variants).
      const geoDist = (r: ReferenceMotor) =>
        Math.abs(Number(r.geo?.magnetHeight_mm) - Number(g.magnet_height) || 0)
        + Math.abs(Number(r.geo?.statorOR_mm) - Number(g.stator_outer_radius) || 0);
      const m = pickReference(sameSection, dist, geoDist);
      setLiveMatched(!!m);
      if (m) setRefId((cur) => (cur === m.id ? cur : m.id));
      // ── Open on the LOADED BUILD, not on the passport's base point ───────
      // (user 2026-08-25: loading CILN28 / G2-L40 showed 45 mm / 12 turns —
      // the passport's calibration point — instead of the machine's own
      // 40 mm / 21 turns.)  The passport calibrates the physics; the knobs
      // must start where the user's machine actually is.  Only re-adopted
      // when the live build CHANGES, so tuning inside the panel is never
      // clobbered.
      const readLS = (k: string, d: number) => {
        try { const v = localStorage.getItem('sim.' + k); return v == null ? d : Number(JSON.parse(v)); }
        catch { return d; }
      };
      const conn = (() => {
        try { return String(JSON.parse(localStorage.getItem('sim.connection') || '""')); }
        catch { return ''; }
      })();
      const live = {
        L_mm: Number(g.motor_length),
        N: Number(g.num_wires_per_slot),
        // STRIPS PER WIRE ROW of the loaded build.  Not a slider: the strips
        // are SERIES turns, so a machine split S ways has S× the turns of the
        // same rows unsplit, and a reference passport measured UNSPLIT (they
        // are matched by cross-section, not by build) would otherwise be scaled
        // with a turns ratio short by exactly S.
        split: Math.max(1, Math.round(Number(g.wire_split) || 1)),
        wireH_mm: Number(g.wire_height),
        nP: (() => {
          const mm = conn.match(/(\d+)\s*P/i);          // "2S-2P" -> 2, "4P" -> 4
          return mm ? Number(mm[1]) : 1;
        })(),
        I_A: readLS('current', NaN),
        rpm: readLS('rpm', NaN),
      };
      const sig = `${live.L_mm}|${live.N}|${live.split}|${live.wireH_mm}|${live.nP}|${live.I_A}|${live.rpm}`;
      if (liveSigRef.current !== sig
          && Number.isFinite(live.L_mm) && Number.isFinite(live.N)) {
        liveSigRef.current = sig;
        // Only adopt the live build onto a passport that GENUINELY matches its
        // cross-section (m).  Grafting these raw slider values onto some other
        // ref's passport (the previous pick, or the built-in fallback) produced
        // numbers for a machine that does not exist — e.g. a 200 mm 20p/24s
        // passport computed with a loaded 40 mm 12s/14p build's turns/current
        // (owner 2026-09-29: "three different motors on one page").  With no
        // match, leave the knobs/ranges alone; the render layer shows the
        // "no configurator model" empty state instead of a wrong-machine result.
        if (m) {
          const adopt = (k0: Knobs): Knobs => ({
            N: live.N || k0.N,
            split: live.split,
            L_mm: live.L_mm || k0.L_mm,
            wireH_mm: live.wireH_mm || k0.wireH_mm,
            nP: live.nP || k0.nP,
            I_A: Number.isFinite(live.I_A) && live.I_A > 0 ? live.I_A : k0.I_A,
            rpm: Number.isFinite(live.rpm) && live.rpm > 0 ? live.rpm : k0.rpm,
          });
          setKnobs((k0) => { const k2 = withDrive(adopt(k0), m.id); setRefKnobs(k2); return k2; });
        }
      }
    };
    const onLoaded = () => pick(true);
    window.addEventListener('sim-operating-point', onLoaded);
    pick();   // also on mount / after the references arrive
    return () => window.removeEventListener('sim-operating-point', onLoaded);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allRefs, refsAnswered, liveGeo]);
  // No machine loaded at all: after a short wait say so (instead of loading forever).
  useEffect(() => {
    if (!refsAnswered || liveGeo || matchChecked) return;
    const t = setTimeout(() => setMatchChecked(true), 4000);
    return () => clearTimeout(t);
  }, [refsAnswered, liveGeo, matchChecked]);
  const ref: ReferenceMotor = useMemo(
    () => allRefs.find((r) => r.id === refId) ?? allRefs[0],
    [refId, allRefs],
  );
  const p = ref.passport;
  const [knobs, setKnobs] = useState<Knobs>(() => {
    try { const r = localStorage.getItem(KNOBS_LS); if (r) { const k = JSON.parse(r); if (k && typeof k.N === 'number') return k as Knobs; } } catch { /* ignore */ }
    return baseKnobs(p);
  });
  // remember the user's tuning across reloads
  useEffect(() => { try { localStorage.setItem(KNOBS_LS, JSON.stringify(knobs)); } catch { /* ignore */ } }, [knobs]);
  const skipReset = React.useRef(false);
  const lastRefId = React.useRef(refId);
  // reset knobs only when the reference ACTUALLY changes — compare to the last
  // refId (robust to mount + StrictMode double-invoke, which would otherwise wipe
  // the restored tuning) and skip while loading a saved config.
  useEffect(() => {
    if (lastRefId.current === refId) return;   // mount / replay — not a real change
    lastRefId.current = refId;
    if (skipReset.current) { skipReset.current = false; return; }
    // A different machine → its own base point AND its own slider ranges.
    { const kb = withDrive(baseKnobs(ref.passport), refId); setKnobs(kb); setRefKnobs(kb); }
  }, [refId]); // eslint-disable-line react-hooks/exhaustive-deps
  // First paint after the references arrive: if the stored knobs/ranges belong
  // to another machine (they are persisted globally), adopt this one's.
  const rangedFor = React.useRef<string>('');
  useEffect(() => {
    if (!ref || rangedFor.current === refId) return;
    rangedFor.current = refId;
    const p0 = ref.passport;
    const off = (a: number, b: number) => !(b > 0) || Math.abs(a - b) / b > 1.5;
    if (off(knobs.L_mm, p0.L0_mm) || off(knobs.I_A, p0.I0_A)) {
      { const kb = withDrive(baseKnobs(p0), refId); setKnobs(kb); setRefKnobs(kb); }
    }
  }, [refId, ref]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── MCP Stage 3: an AGENT DRAFT as a simplified machine ──────────────────
  // …/?tab=configure&design=d-… (or Motors → Agent drafts → Configure) shows a
  // banner; nothing changes until the engineer clicks Open, and even then only
  // this client-side tuner moves — the server's open machine is not touched.
  const [draft, setDraft] = useState<AgentDraft | null>(null);
  const [draftMsg, setDraftMsg] = useState<string | null>(null);
  const [draftOpen, setDraftOpen] = useState(false);
  const pendingDraft = React.useRef<Knobs | null>(null);
  useEffect(() => {
    const load = (id: string | null) => {
      if (!id) return;
      getDraft(id).then((d) => { setDraft(d); setDraftOpen(false); setDraftMsg(null); })
        .catch(() => setDraftMsg(tx('configure.draftLoadFailed', { id })));
    };
    load(draftIdFromUrl());
    const on = (e: Event) => load((e as CustomEvent<{ id: string }>).detail?.id ?? null);
    window.addEventListener('agent-draft', on);
    return () => window.removeEventListener('agent-draft', on);
  }, []);
  // declared AFTER the reference effects above so it runs after them and wins
  useEffect(() => {
    const k1 = pendingDraft.current;
    if (!k1) return;
    pendingDraft.current = null;
    setKnobs(k1); setRefKnobs(k1);
  }, [refId]); // eslint-disable-line react-hooks/exhaustive-deps
  // The draft's OWN reference card — resolved the same way the backend
  // matched it (starting_point die/config), never substituted for another
  // machine's passport.  A slots/poles mismatch against that card (should
  // never happen, but the guard is cheap) is treated the same as "no card":
  // refuse the model rather than compute with the wrong one.
  const draftTarget = useMemo(() => (
    draft ? resolveDraftTarget(allRefs, draft.reference_motor_id, draft.starting_point) : undefined
  ), [draft, allRefs]);
  const openDraft = () => {
    if (!draft) return;
    setDraftOpen(true);
    if (!draftTarget) {
      // No FEM-characterised passport for THIS draft's own machine — never
      // fall back to whatever reference happened to be selected (owner
      // 2026-09-29: that produced a 200 mm passport's numbers for a 40 mm
      // draft).  The knobs/refId stay untouched; the render layer shows the
      // "no configurator model" empty state instead of any result tiles.
      setDraftMsg(tx('configure.draftNoModel'));
      return;
    }
    const pr = draft.params;
    const base = baseKnobs(draftTarget.passport);
    const n1 = draft.build?.conductors_per_slot;
    const k1: Knobs = {
      ...base, L_mm: pr.stack_mm, I_A: pr.current_a_rms, rpm: pr.speed_rpm,
      nP: pr.parallel_paths ?? base.nP,
      N: n1 && n1 > 0 ? n1 : Math.max(1, Math.round(base.N * (pr.turns_factor || 1))),
    };
    if (draftTarget.id !== refId) {
      pendingDraft.current = k1; skipReset.current = true; setRefId(draftTarget.id);
    } else { setKnobs(k1); setRefKnobs(k1); }
    setDraftMsg(null);
  };
  const saveDraft = async () => {
    if (!draft) return;
    const n0 = draft.build?.base_conductors_per_slot;
    try {
      const d = await patchDraft(draft.design_id, {
        stack_mm: knobs.L_mm, current_a_rms: knobs.I_A, speed_rpm: knobs.rpm,
        parallel_paths: knobs.nP,
        ...(n0 && n0 > 0 ? { turns_factor: knobs.N / n0 } : {}),
      });
      setDraft(d); setDraftMsg(tx('configure.draftSaved'));
    } catch { setDraftMsg(tx('configure.draftSaveFailed')); }
  };
  // The draft's own last FEM run, if it has one (get_design_result's headline,
  // already carried on the draft by GET /api/agent_designs/{id}) — shown
  // beside the scaled tuner numbers, never in place of them.
  const draftHeadline = useMemo(() => (draft ? bestDraftResult(draft) : null), [draft]);

  // ── ONE machine at a time (owner 2026-09-29: "a complete mess — three
  //    different motors on one page").  The reference name, the sliders and
  //    the result tiles must always describe the SAME machine; if the panel
  //    is showing an opened draft, that machine is the draft's; otherwise
  //    it is the currently loaded/open machine.  With no matching passport
  //    for that machine, refuse to compute rather than borrow another
  //    machine's model.
  const mState = modelState({ refsAnswered, matchChecked, draftOpen, hasDraftTarget: !!draftTarget, liveMatched });
  const loadingModel = mState === 'loading';
  const blocked = mState === 'blocked';
  const blockedLabel = draftOpen && draft
    ? `${draft.starting_point.die} / ${draft.starting_point.config}`
    : (() => {
        const g = liveGeo as Record<string, unknown> | null;
        const slots = Number(g?.num_slots), poles = Number(g?.num_poles);
        const od = Number(g?.stator_outer_radius) * 2;
        if (!Number.isFinite(slots) || !Number.isFinite(poles)) return tx('configure.loadedMotor');
        return tx('configure.loadedMotorDetail', {
          slots, poles, od: Number.isFinite(od) ? tx('configure.loadedOd', { od: od.toFixed(0) }) : '' });
      })();

  // battery the user runs the motor from (persisted; snapshotted into each saved config)
  const [battery, setBattery] = useState<Battery>(() => defaultBattery());
  const [configs, setConfigs] = useState<SavedConfig[]>(() => {
    try { const r = localStorage.getItem(LS_KEY); const a = r ? JSON.parse(r) : null; return Array.isArray(a) ? a : []; }
    catch { return []; }
  });
  useEffect(() => { try { localStorage.setItem(LS_KEY, JSON.stringify(configs)); } catch { /* ignore */ } }, [configs]);

  // ── PRESETS: the configurations of this machine's die (owner 2026-10-05), read from the
  //    machines themselves by the server; `baseConfig` is the one the knobs are measured against.
  const [baseConfig, setBaseConfig] = useState<string | null>(null);
  const catId = catalogIdOf(refId);
  const [ctx, setCtx] = useState<ConfigureContext | null>(null);
  const [limitMsg, setLimitMsg] = useState<string | null>(null);
  // `ctxDone`: the context has been ANSWERED (or there is none to ask for) — blocks that depend on
  // it (the Thermal block for a die that is not propeller-cooled) wait for it instead of flashing.
  const [ctxDone, setCtxDone] = useState(false);
  const loadCtx = React.useCallback(async () => {
    setCtx(catId ? await fetchConfigureContext(catId) : null);
    setCtxDone(true);
  }, [catId]);
  useEffect(() => { setCtx(null); setCtxDone(false); setLimitMsg(null); setBaseConfig(null); void loadCtx(); }, [loadCtx]);
  const presets: Preset[] = ctx?.presets ?? [];
  const basePreset: Preset | null = presets.find((x) => x.config === baseConfig) ?? null;
  // a freshly loaded machine starts on the configuration whose build it is
  useEffect(() => {
    if (baseConfig != null || !presets.length) return;
    const m = presetOfBuild(presets, refKnobs ?? baseKnobs(p));
    if (m) setBaseConfig(m.config);
  }, [presets, refKnobs, baseConfig]); // eslint-disable-line react-hooks/exhaustive-deps
  // ── DEFAULT MOTOR: open on it when there is no choice of his own yet, and
  //    "Reset to my default" at any time.  The reference is the catalogue card of
  //    the default die + configuration; the configuration's preset then restores
  //    every knob, its battery pack, its propeller and its default drive.
  const defaultRef = useMemo(() => referenceOfDefault(catalogRefs, defaultMotor), [catalogRefs, defaultMotor]);
  const [pendingDefault, setPendingDefault] = useState<string | null>(null);
  const goDefault = React.useCallback(() => {
    if (!defaultRef || !defaultMotor) return;
    defaultPinned.current = true;
    setRefId(defaultRef.id);
    setPendingDefault(defaultMotor.config);
  }, [defaultRef, defaultMotor]);
  const defaultDecided = React.useRef(false);
  useEffect(() => {
    if (defaultDecided.current || !refsAnswered || !authResolved) return;
    defaultDecided.current = true;
    if (!hadOwnChoice && defaultRef) goDefault();
  }, [refsAnswered, authResolved, hadOwnChoice, defaultRef, goDefault]);
  // the preset can only be applied once THIS machine's context has arrived
  useEffect(() => {
    if (!pendingDefault || !ctx || ctx.motor_id !== catId) return;
    const pr = (ctx.presets ?? []).find((x) => x.config === pendingDefault);
    setPendingDefault(null);
    if (pr) applyPreset(pr);
  }, [pendingDefault, ctx, catId]); // eslint-disable-line react-hooks/exhaustive-deps
  // ── DRIVE: Sine | PWM (owner 2026-10-05) ──────────────────────────────────
  // PWM lists ONLY the drive variants COMPUTED for this machine (the passport's
  // `pwm_variants`: a device at a carrier, already solved) and reads between
  // their computed points; nothing is calculated live here.  Sine is the
  // default and the whole block is inert unless the user picks PWM: with it
  // off `scaleKnobs === knobs`, so every Sine number is the one it always was.
  const variants = useMemo(
    () => usableVariants((basePreset && basePreset.pwm_variants.length ? basePreset.pwm_variants : p.pwm_variants) as PwmVariant[] | null | undefined),
    [p, basePreset]);
  const driveOn = knobs.drive === 'pwm' && variants.length > 0;
  const variant = driveOn ? (resolveVariant(variants, knobs) ?? null) : null;
  /** the drive choice that names a variant: its id AND its (transistor, frequency) pair */
  const choiceOf = (v: PwmVariant): Partial<Knobs> =>
    ({ drive: 'pwm', drive_variant: v.id, drive_device: v.device, drive_carrier_hz: Number(v.carrier_hz) });
  // scaleMotor() is the SINE model and never sees the drive; `scaleKnobs` stays as
  // the one name the charts and the result use for "the knobs the model reads".
  const scaleKnobs = knobs;
  const result  = useMemo(() => scaleMotor(p, scaleKnobs, ref.poles), [p, scaleKnobs, ref.poles]);

  // ── RANGES = PHYSICAL LIMITS (owner 2026-10-05) ───────────────────────────
  // Per machine, live with the knobs they depend on (lib/configuratorLimits.ts):
  // stack length (hand-set per motor, else the default rule), wire min 0.2 mm,
  // turns = what fits the slot, current = the machine's inverter device, speed
  // = the pack-maximum voltage envelope.  An admin can only NARROW them.
  const readLs = (k: string) => { try { return localStorage.getItem(k); } catch { return null; } };
  // ── The Battery block follows the MACHINE (owner 2026-10-05: it showed 100 NMC cells /
  //    300–420 V beside a 6S 18–25.2 V machine).  It opens on the pack the machine was
  //    saved with — the same source the slider ranges read (configure_context, else the
  //    passport's own battery) — or on the user's own edit FOR THIS MACHINE; a machine
  //    that names no pack keeps the stock default.
  const machinePack = useMemo(
    () => batteryFromPack(basePreset?.battery) ?? batteryFromPack(ctx?.battery) ?? batteryFromPack(p.battery as never),
    [basePreset, ctx, p.battery]);
  const batterySeeded = React.useRef<string>('');
  // One-time cleanup: an "edit" equal to the stock 100-cell default was never a user's choice
  // (it shadowed the machine's own pack); drop it before anything reads it.
  useEffect(() => {
    try {
      const raw = localStorage.getItem(BATTERY_BY_MACHINE_LS);
      const clean = dropStockEdits(raw);
      if (clean !== raw && clean != null) localStorage.setItem(BATTERY_BY_MACHINE_LS, clean);
      localStorage.removeItem('configurator.battery.v1');      // the pre-presets global battery
    } catch { /* ignore */ }
  }, []);
  useEffect(() => {
    const edit = readBatteryEdit(readLs(BATTERY_BY_MACHINE_LS), refId);
    const want = wantedBattery(edit, machinePack);
    const sig = `${refId}|${edit ? 'edit' : 'pack'}|${JSON.stringify(want)}`;
    if (!want || batterySeeded.current === sig) return;
    batterySeeded.current = sig;
    setBattery((cur) => (sameBattery(cur, want) ? cur : want));
  }, [refId, machinePack]); // eslint-disable-line react-hooks/exhaustive-deps
  /** a change the USER makes — remembered for this machine only */
  const updateBattery = (b: Battery) => {
    setBattery(b);
    try { localStorage.setItem(BATTERY_BY_MACHINE_LS, writeBatteryEdit(readLs(BATTERY_BY_MACHINE_LS), refId, b)); } catch { /* ignore */ }
  };
  const [overrides, setOverrides] = useState<Overrides>(() => readOverrides(readLs(RANGES_LS_V2), refId));
  useEffect(() => { setOverrides(readOverrides(readLs(RANGES_LS_V2), refId)); }, [refId]);
  // THE PACK THE USER SEES in the Battery block — the machine's own, or what the user changed
  // it to — drives everything that depends on the bus: the full-battery speed, the 2S/2P
  // voltage warning, the PWM bus-range check.  A machine with no pack of its own and no edit
  // has no pack to judge by (the stock 100-cell default is not this machine's pack).
  const batteryKnown = machinePack != null || readBatteryEdit(readLs(BATTERY_BY_MACHINE_LS), refId) != null;
  const packWindow = batteryKnown
    ? { min: battery.cells * battery.min, max: battery.cells * battery.max } : null;
  const packMaxV: number | null = packWindow?.max ?? null;
  const packNomV: number | null = batteryKnown ? battery.cells * battery.nom : null;
  /** back to the pack the machine was saved with (the user's edit for it is dropped) */
  const resetBattery = () => {
    if (!machinePack) return;
    try { localStorage.setItem(BATTERY_BY_MACHINE_LS, clearBatteryEdit(readLs(BATTERY_BY_MACHINE_LS), refId)); } catch { /* ignore */ }
    batterySeeded.current = '';
    setBattery(machinePack);
  };
  const modM = ctx?.modulation.m ?? DEFAULT_MODULATION;
  const speedLim = useMemo(() => {
    const at0 = scaleMotor(p, { ...knobs, rpm: 0 }, ref.poles);
    const at1k = scaleMotor(p, { ...knobs, rpm: 1000 }, ref.poles);
    const model = Number(p.Vload0_peak_V ?? 0) > 0;
    return speedLimit({
      vMax: packMaxV, m: modM, kvRpmPerV: at1k.KV_rpm_per_Vline,
      v0: model ? at0.Vline_peak_V : null, v1000: model ? at1k.Vline_peak_V : null,
    });
  }, [p, knobs, ref.poles, packMaxV, modM]);
  const phys = useMemo(() => physicalRanges({
    p, fit: ref.fit, N: knobs.N, wireH_mm: knobs.wireH_mm,
    lMaxMm: ctx?.limits.L_max_mm ?? null,
    iMaxA: ctx?.current.set ? (ctx.current.i_phase_rms_max_A ?? null) : null,
    speed: speedLim,
  }), [p, ref.fit, knobs.N, knobs.wireH_mm, ctx, speedLim]);
  const ranges: Record<KnobKey, KRange> = useMemo(() => ({
    L_mm: narrowRange(phys.L_mm, overrides.L_mm), N: narrowRange(phys.N, overrides.N),
    wireH_mm: narrowRange(phys.wireH_mm, overrides.wireH_mm),
    I_A: narrowRange(phys.I_A, overrides.I_A), rpm: narrowRange(phys.rpm, overrides.rpm),
  }), [phys, overrides]);
  /** an admin's local edit: clamped INSIDE the physical range, so it only narrows */
  const setRange = (k: KnobKey) => (min: number, max: number) => {
    const nr = narrowRange(phys[k], { min, max });
    const next: Overrides = { ...overrides, [k]: nr };
    setOverrides(next);
    try { localStorage.setItem(RANGES_LS_V2, writeOverrides(readLs(RANGES_LS_V2), refId, next)); } catch { /* ignore */ }
    setKnobs((s) => ({ ...s, [k]: Math.min(nr.max, Math.max(nr.min, (s as unknown as Record<string, number>)[k])) }));
  };
  /** the stack-length maximum is the one limit an admin SETS (stored with the motor) */
  const setLRange = (min: number, max: number) => {
    if (catId && Math.abs(max - ranges.L_mm.max) > 1e-9 && max > 0) {
      setLimitMsg(null);
      void saveLMax(catId, max).then(loadCtx).catch(() => setLimitMsg(tx('configureLimits.saveFailed')));
      setOverrides((o) => { const rest = { ...o }; delete rest.L_mm; return rest; });
      return;
    }
    setRange('L_mm')(min, max);
  };
  const clearLMax = () => {
    if (!catId) return;
    setLimitMsg(null);
    void saveLMax(catId, null).then(loadCtx).catch(() => setLimitMsg(tx('configureLimits.saveFailed')));
  };
  /** one short line under a slider: where its maximum comes from (+ tooltip) */
  const limitNote = (k: KnobKey): { text: string; tip: string; hand?: boolean } => {
    const b: LimitBasis = (phys as Record<KnobKey, PhysRange>)[k].basis;
    const mx = fmt(ranges[k].max, k === 'wireH_mm' ? 1 : 0);
    const cur = ctx?.current;
    if (k === 'L_mm') {
      return b === 'hand'
        ? { text: tx('configureLimits.lHand', { max: mx }), tip: tx('configureLimits.lHandTip'), hand: true }
        : { text: tx('configureLimits.lDefault', { max: mx }), tip: tx('configureLimits.lDefaultTip') };
    }
    if (k === 'N') {
      return { text: tx('configureLimits.nFit', { max: mx }), tip: tx('configureLimits.nFitTip', {
        wire: fmt(knobs.wireH_mm, 1), avail: fmt(ref.fit.slotHeight_mm - 2 * ref.fit.insulation_mm, 2) }) };
    }
    if (k === 'wireH_mm') {
      return { text: tx('configureLimits.wireFit', { min: fmt(ranges.wireH_mm.min, 1), max: mx, n: knobs.N }),
               tip: tx('configureLimits.wireFitTip') };
    }
    if (k === 'I_A') {
      return b === 'inverter'
        ? { text: tx('configureLimits.iInverter', { max: mx, device: cur?.device ?? '', n: cur?.devices_parallel ?? 1 }),
            tip: tx('configureLimits.iInverterTip', { rating: fmt(cur?.i_d_rating_A ?? NaN, 0), tcase: fmt(cur?.t_case_c ?? NaN, 0) }) }
        : { text: tx('configureLimits.iNoController'), tip: tx('configureLimits.iNoControllerTip') };
    }
    return b === 'envelope'
      ? { text: tx('configureLimits.rpmEnvelope', { max: mx, v: fmt(packMaxV ?? NaN, 1) }), tip: tx('configureLimits.rpmEnvelopeTip', { m: fmt(modM, 2) }) }
      : b === 'kv'
        ? { text: tx('configureLimits.rpmKv', { max: mx, v: fmt(packMaxV ?? NaN, 1) }), tip: tx('configureLimits.rpmKvTip', { m: fmt(modM, 2) }) }
        : { text: tx('configureLimits.rpmNoBattery'), tip: tx('configureLimits.rpmNoBatteryTip') };
  };
  const above = (v: number, r: KRange) => v > r.max * 1.0005 + 1e-9;
  /** the connection stays free: only a warning when the winding's line voltage exceeds the pack nominal */
  const connWarn = lineVoltageWarning(result.Vline_peak_V, packNomV);
  /** the picked variant's read-only facts, for the Drive TITLE row (+ provenance tooltip) */
  const driveFacts = (() => {
    if (!driveOn || !variant) return { line: '', tip: '' };
    const f = variantFacts(variant);
    const bus = f.bus;
    const busText = !bus ? null
      : bus.min != null && bus.max != null && bus.nom != null
        ? tx('configureDrive.factBusFull', { min: bus.min, max: bus.max, nom: bus.nom })
        : bus.min != null && bus.max != null
          ? tx('configureDrive.factBusRange', { min: bus.min, max: bus.max })
          : bus.nom != null ? tx('configureDrive.factBusNom', { nom: bus.nom }) : null;
    const bits = [
      variant.technology ? String(variant.technology) : null,
      f.deadTime ? tx('configureDrive.factDead', { value: f.deadTime }) : null,
      f.nParallel ? tx('configureDrive.factParallel', { n: f.nParallel }) : null,
      f.modulationKey ? tx(f.modulationKey) : null, busText,
    ].filter(Boolean);
    return {
      line: bits.join(' · '),
      tip: [bits.join(' · '),
            f.provenance ? tx('configureDrive.provenanceTip', { text: f.provenance }) : null,
            tx('configureDrive.variantTip')].filter(Boolean).join('\n'),
    };
  })();
  const driveFactsTip = driveFacts.tip || tx('configureDrive.variantTip');
  // "vs ref" compares against THE MACHINE AS LOADED, not against the
  // passport's calibration point (user 2026-08-26: a freshly loaded motor
  // showed −92.7 % with nothing touched — it was being compared to another
  // configuration's base build).  refKnobs is set when a machine is adopted;
  // it falls back to the passport base when nothing is loaded.
  const baseRes = useMemo(() => scaleMotor(p, refKnobs ?? baseKnobs(p), ref.poles),
                          [p, ref.poles, refKnobs]);
  const iMax    = useMemo(() => maxCurrent(p, knobs), [p, knobs]);

  // ── DRIVE, continued: the choice, its reading, the device limits ──────────
  useTranslation('controller');   // re-render on language change; lazy-loads the namespace
  /** change the drive and remember it for THIS machine (like the other knobs,
   *  it also rides in `knobs` → localStorage and the saved configurations) */
  const setDrive = (patch: Partial<Knobs>) => {
    const n: Knobs = { ...knobs, ...patch };
    setKnobs(n);
    try {
      localStorage.setItem(DRIVE_LS, writeDriveChoice(localStorage.getItem(DRIVE_LS), refId, pickDrive(n)));
    } catch { /* ignore */ }
  };
  // The device cards' published limits (a read of the catalogue, not a
  // calculation) — fetched when PWM is on, so a new card needs no UI change.
  const [devLimits, setDevLimits] = useState<Record<string, DeviceLimits>>({});
  useEffect(() => {
    if (!driveOn) return;
    let dead = false;
    listDevices().then((r) => {
      if (dead) return;
      const m: Record<string, DeviceLimits> = {};
      for (const d of r.devices) {
        if (!d.error) m[d.part] = { v_dss_V: d.v_dss_V, i_d_100c_A: d.i_d_100c_A, t_j_max_c: d.t_j_max_c };
      }
      setDevLimits(m);
    }).catch(() => { /* limits unknown: only the envelope checks apply */ });
    return () => { dead = true; };
  }, [driveOn]);
  /** the variants were computed for the loaded build: only I and rpm move */
  const driveBuildMoved = driveOn && buildTuned(knobs, refKnobs ?? baseKnobs(p));
  const driveRead = useMemo(
    () => (variant && !driveBuildMoved ? readVariant(variant, knobs.rpm, knobs.I_A) : null),
    [variant, driveBuildMoved, knobs.rpm, knobs.I_A]);
  const driveProblems = useMemo(
    () => (variant && driveRead && driveRead.ok
      ? limitProblems(variant, driveRead.values, knobs.I_A, devLimits[variant.device] ?? null, packWindow)
      : []),
    [variant, driveRead, knobs.I_A, devLimits, packWindow]);
  /** the numbers to show — null whenever anything is refused */
  const drv = driveRead && driveRead.ok && driveProblems.length === 0 ? driveRead.values : null;
  const driveMode: 'sine' | 'pwm' = driveOn ? 'pwm' : 'sine';
  /** the EFFICIENCY tile: system (motor + controller), battery -> shaft */
  const sysEff = systemEfficiency(driveMode, drv, result.P_mech_W, result.P_loss_W);
  /** …and the reference it is coloured against: the same quantity at the reference knobs (PWM reads the
   *  variant there; if that point is not computed the Sine efficiency stands in) */
  const baseSysEff = (() => {
    if (driveMode === 'sine' || !variant) return baseRes.efficiency * 100;
    const k0 = refKnobs ?? baseKnobs(p);
    const r0 = readVariant(variant, k0.rpm, k0.I_A);
    const e0 = r0.ok ? systemEfficiency('pwm', r0.values, baseRes.P_mech_W, baseRes.P_loss_W).value : null;
    return e0 ?? baseRes.efficiency * 100;
  })();
  /** one tile of the drive-dependent set, from its spec */
  const renderSpec = (t: DriveTileSpec) => {
    const detail = t.id === 'invLoss' && drv && controllerLossW(drv) != null
      ? ` ${tx('configureDrive.invLossSplit', { c: fmt(drv.inv_cond_W ?? 0, 1), s: fmt(drv.inv_sw_W ?? 0, 1), d: fmt(drv.inv_dead_W ?? 0, 1), b: fmt(drv.board_copper_W ?? 0, 1) })}` : '';
    return (
      <MetricTile key={t.id} label={tx(t.labelKey)} value={t.value} unit={t.unit} d={t.d}
        base={t.value ?? 0} goodHi={t.goodHi}
        absLevel={t.level} tip={tx(t.tipKey) + detail}
        blankTip={t.blank === 'sine' ? tx('configureDrive.blankSine')
          : t.blank === 'refused' ? tx('configureDrive.blankRefused') : tx('configureDrive.blankMissing')} />
    );
  };
  // ── PROPELLER: the load AND the cooling (owner 2026-10-05) ─────────────────────────────────
  // For a die whose only cooling is the propeller's slipstream (`propeller_air` in
  // config/cooling_options.yaml: the Ø40 drone motors) the propeller sets the shaft torque at
  // the speed knob (so the phase current is DERIVED from the passport), and the same rpm sets the
  // air over the housing (so the temperatures follow).  Every propeller number is the backend's
  // (`/api/propellers/{id}/series`); here it is only interpolated.  "Manual load" gives the
  // current back to the engineer.
  const cooled = isPropellerCooled(ctx?.cooling);
  const [propList, setPropList] = useState<PropSummary[] | null>(null);
  useEffect(() => {
    if (!cooled) return;
    let dead = false;
    const go = () => { void fetchPropellers().then((l) => { if (dead) return; if (l) setPropList(l); else setTimeout(go, 3000); }); };
    go();
    return () => { dead = true; };
  }, [cooled]);
  const allowedProps = useMemo(() => allowedPropellers(ctx?.cooling, propList ?? []), [ctx, propList]);
  const [coolChoice, setCoolChoice] = useState<CoolChoice>({});
  useEffect(() => { setCoolChoice(readCoolChoice(readLs(PROP_CHOICE_LS), refId)); }, [refId]);
  const updateCool = (patch: CoolChoice) => {
    setCoolChoice((c) => ({ ...c, ...patch }));
    try { localStorage.setItem(PROP_CHOICE_LS, writeCoolChoice(readLs(PROP_CHOICE_LS), refId, patch)); } catch { /* ignore */ }
  };
  /** the propeller a configuration opens on (config/cooling_options.yaml `defaults`; else the first with torque data) */
  const propDefaultFor = (config: string | null) => defaultPropellerFor(ctx?.cooling, config, allowedProps);
  // The user's own pick wins; with none the picker shows the default of the configuration in use.
  const propId = cooled ? effectivePropeller(coolChoice, allowedProps, propDefaultFor(baseConfig)) : null;
  const propSummary = allowedProps.find((x) => x.id === propId) ?? null;
  const ambient = coolChoice.ambient ?? DEFAULT_AMBIENT_C;
  const propLoad = cooled && (coolChoice.load ?? 'prop') === 'prop';
  // the ambient field: typed text, committed after a short pause (one /series request per value)
  const [ambTxt, setAmbTxt] = useState<string | null>(null);
  useEffect(() => {
    if (ambTxt == null) return;
    const v = parseFloat(ambTxt);
    if (!(v >= -60 && v <= 80)) return;
    const t = setTimeout(() => { if (v !== ambient) updateCool({ ambient: v }); }, 400);
    return () => clearTimeout(t);
  }, [ambTxt]); // eslint-disable-line react-hooks/exhaustive-deps
  const housingMm = ref.geo.statorOR_mm * 2;
  const [series, setSeries] = useState<PropSeries | null>(null);
  useEffect(() => {
    setSeries(null);
    if (!cooled || !propId) return;
    let dead = false;
    const go = () => { void fetchSeries(propId, ambient, housingMm).then((s) => { if (dead) return; if (s) setSeries(s); else setTimeout(go, 3000); }); };
    go();
    return () => { dead = true; };
  }, [cooled, propId, ambient, housingMm]);
  const propPoint = cooled && series ? seriesAt(series, knobs.rpm) : null;
  /** the torque of the passport at a current — every other knob as given */
  const torqueAtI = (k: Knobs) => (I: number) => scaleMotor(p, { ...k, I_A: I }, ref.poles).T_Nm;
  const propLoadRes = useMemo(
    () => (propLoad ? currentForTorque(propPoint?.torque_Nm ?? null, torqueAtI(knobs), ranges.I_A.max) : null),
    [propLoad, propPoint?.torque_Nm, p, ref.poles, knobs.N, knobs.L_mm, knobs.wireH_mm, knobs.nP, knobs.split, knobs.rpm, ranges.I_A.max]); // eslint-disable-line react-hooks/exhaustive-deps
  // the current knob FOLLOWS the propeller (a refusal parks it at the largest current the motor may carry)
  useEffect(() => {
    if (!propLoadRes || (!propLoadRes.ok && propLoadRes.kind === 'no_prop')) return;
    const I = propLoadRes.I_A;
    if (Math.abs(I - knobs.I_A) > 1e-3) setKnobs((k) => ({ ...k, I_A: I }));
  }, [propLoadRes]); // eslint-disable-line react-hooks/exhaustive-deps
  const loadRefused = !!propLoadRes && !propLoadRes.ok && propLoadRes.kind === 'torque';
  const thermalGeom = useMemo(() => ({
    statorOD_mm: ref.geo.statorOR_mm * 2,
    stackLength_mm: knobs.L_mm,
    numSlots: ref.geo.numSlots,
    slotHeight_mm: ref.fit.slotHeight_mm,
    slotWidth_mm: ref.fit.slotWidth_mm,
    insulation_mm: ref.fit.insulation_mm,
    coreThickness_mm: Math.max(0, ref.geo.statorOR_mm - ref.geo.statorIR_mm - ref.fit.slotHeight_mm),
    airGap_mm: Math.max(0, ref.geo.statorIR_mm - ref.geo.rotorOR_mm),
    magnetOD_mm: ref.geo.rotorOR_mm * 2,
  }), [ref, knobs.L_mm]);
  const tLimits = useMemo(() => tempLimits(ctx?.thermal_limits), [ctx]);
  const extraLossW = extraLossTile(driveMode, drv).value ?? 0;
  /** winding / magnet / housing temperatures at the knobs (null while the propeller data is
   *  not here, or while the load is refused — never numbers for a point that cannot be run) */
  const thermal = useMemo(() => (
    cooled && propPoint && propPoint.h_W_m2K != null && !loadRefused
      ? estimateThermal(thermalGeom, { P_cu_W: result.P_cu_W, P_fe_W: result.P_fe_W, P_mag_W: result.P_mag_W, P_extra_W: extraLossW },
        { h_Wm2K: propPoint.h_W_m2K, ambient_C: ambient })
      : null), [cooled, propPoint, loadRefused, thermalGeom, result, extraLossW, ambient]);
  const verdict = thermal ? judgeTemps(thermal.T_winding_C, thermal.T_magnet_C, tLimits) : null;
  const overheats = !!verdict?.over;
  /** the ONE red line above the results (fixed height, so it never moves the grid) */
  const propLine: { text: string; tip: string } | null = (() => {
    if (!cooled) return null;
    if (loadRefused && propLoadRes && !propLoadRes.ok && propLoadRes.kind === 'torque') {
      return { text: tx('configurePropeller.refuseTorque', { need: fmt(propLoadRes.need_Nm, 3), rpm: fmt(knobs.rpm, 0), have: fmt(propLoadRes.have_Nm, 3), imax: fmt(ranges.I_A.max, 0) }),
               tip: tx('configurePropeller.refuseTorqueTip') };
    }
    if (overheats && verdict) {
      return { text: tx('configurePropeller.overheat'),
               tip: [verdict.windingOver ? tx('configurePropeller.overWinding', { limit: fmt(tLimits.winding_C, 0), basis: tLimits.windingBasis }) : null,
                     verdict.magnetOver ? tx('configurePropeller.overMagnet', { limit: fmt(tLimits.magnet_C, 0) }) : null].filter(Boolean).join(' ') };
    }
    if (propList && !allowedProps.length) return { text: tx('configurePropeller.noPropeller'), tip: tx('configurePropeller.noPropellerTip') };
    return null;
  })();
  const propBad = !!propLine && (loadRefused || overheats);
  const tempTiles = tempRowTiles(
    thermal && propPoint ? { T_winding_C: thermal.T_winding_C, T_magnet_C: thermal.T_magnet_C, T_housing_C: thermal.T_housing_C,
      air_speed_ms: propPoint.air_speed_ms, h_W_m2K: propPoint.h_W_m2K ?? 0 } : null, tLimits);
  const renderTemp = (t: TempTileSpec) => (
    <MetricTile key={t.id} label={tx(t.labelKey)} value={t.value} display={t.display} unit={t.unit} d={t.d} base={t.value ?? 0}
      absLevel={t.level} plain
      tip={tx(t.tipKey, { limit: t.limit != null ? fmt(t.limit, 0) : '',
        air: t.air != null ? fmt(t.air, 1) : '—', h: t.h != null ? fmt(t.h, 0) : '—' })}
      blankTip={tx(loadRefused ? 'configurePropeller.blankRefused' : 'configurePropeller.blankLoading')} />
  );
  /** thermal zones on the knobs: green = continuous below both limits with this propeller, red = beyond.
   *  Recomputed with wire / turns / length / propeller / ambient (a pure function of them). */
  const zones = useMemo(() => {
    if (!cooled || !series) return { rpm: null as string | null, I: null as string | null };
    const okAt = (rpm: number, I: number): boolean | null => {
      const pt = seriesAt(series, rpm);
      if (!pt || pt.h_W_m2K == null) return null;
      const r = scaleMotor(p, { ...knobs, rpm, I_A: I }, ref.poles);
      const th = estimateThermal(thermalGeom, { P_cu_W: r.P_cu_W, P_fe_W: r.P_fe_W, P_mag_W: r.P_mag_W, P_extra_W: extraLossW },
        { h_Wm2K: pt.h_W_m2K, ambient_C: ambient });
      return !judgeTemps(th.T_winding_C, th.T_magnet_C, tLimits).over;
    };
    const rpmOk = zoneSamples(ranges.rpm.min, ranges.rpm.max).map((r) => {
      if (!propLoad) return okAt(r, knobs.I_A);
      const pt = seriesAt(series, r);
      if (!pt) return null;
      const ld = currentForTorque(pt.torque_Nm, torqueAtI({ ...knobs, rpm: r }), ranges.I_A.max);
      return ld.ok ? okAt(r, ld.I_A) : false;
    });
    const iOk = propLoad ? [] : zoneSamples(ranges.I_A.min, ranges.I_A.max).map((I) => okAt(knobs.rpm, I));
    return { rpm: zoneGradient(rpmOk), I: zoneGradient(iOk) };
  }, [cooled, series, p, ref.poles, thermalGeom, tLimits, extraLossW, ambient, propLoad, ranges.rpm.min, ranges.rpm.max, ranges.I_A.min, ranges.I_A.max,
      knobs.N, knobs.L_mm, knobs.wireH_mm, knobs.nP, knobs.split, propLoad ? 0 : knobs.I_A, propLoad ? 0 : knobs.rpm]); // eslint-disable-line react-hooks/exhaustive-deps
  /** one short line per refusal (text + tooltip), in the order they matter */
  const driveRefusals: { text: string; tip: string }[] = (() => {
    if (!driveOn) return [];
    if (driveBuildMoved) {
      return [{ text: tx('configureDrive.buildTuned'), tip: tx('configureDrive.buildTunedTip') }];
    }
    if (driveRead && !driveRead.ok) {
      const r = driveRead.refusal;
      const env = tx('configureDrive.refuseEnvelopeTip');
      const n0 = (x: number) => String(Number(x.toFixed(0)));
      const n1c = (x: number) => String(Number(x.toFixed(1)));
      return [{
        tip: env,
        text: r.kind === 'speed'
          ? tx('configureDrive.refuseSpeed', { rpm: n0(r.rpm), lo: n0(r.lo), hi: n0(r.hi) })
          : r.kind === 'current'
            ? tx('configureDrive.refuseCurrent', { amps: n1c(r.I), lo: n1c(r.lo), hi: n1c(r.hi) })
            : r.kind === 'gap' ? tx('configureDrive.refuseGap') : tx('configureDrive.refuseNoCoords'),
      }];
    }
    const tip = tx('configureDrive.refuseLimitTip');
    const dev = variant?.device ?? '';
    const n1 = (x: number) => String(Number(x.toFixed(1)));
    return driveProblems.map((q) => ({
      tip: q.kind === 'bus' ? tx('configureDrive.refuseBusTip') : tip,
      text: q.kind === 'tj'
        ? tx('configureDrive.refuseTj', { tj: n1(q.tj), limit: n0f(q.limit), device: dev })
        : q.kind === 'rating'
          ? tx('configureDrive.refuseRating', { amps: n1(q.amps), limit: n0f(q.limit), device: dev })
          : q.kind === 'vds'
            ? tx('configureDrive.refuseVds', { device: dev, vdss: n0f(q.vdss), bus: n1(q.bus), max: n1(q.max) })
            : tx('configureDrive.refuseBus', { lo: q.lo != null ? n1(q.lo) : '0', hi: q.hi != null ? n1(q.hi) : '∞', pmin: q.packMin != null ? n1(q.packMin) : '0', pmax: q.packMax != null ? n1(q.packMax) : '∞' }),
    }));
  })();
  // STRANDS IN HAND multiply the parallel paths for every current split: k
  // wires wound together each carry 1/k of the turn's current.  It is a
  // property of the BUILD (the passport records what it was measured at), not
  // a knob — the N slider stays PHYSICAL wires per slot, which is what the
  // slot-fit limiter below counts.
  const kPar = Math.max(1, Math.round(p.wire_parallel0 ?? 1));
  // STRIPS PER WIRE ROW of the machine being configured — also a property of
  // the BUILD, and also not a knob.  They are SERIES turns, so they multiply
  // the turn count the same way the strands in hand divide it; the N slider
  // stays wire ROWS either way.
  const kSplit = Math.max(1, Math.round(knobs.split ?? p.wire_split0 ?? 1));
  const rowsLabel = (() => {
    const tags = [kPar > 1 ? tx('configureLimits.inHand', { k: kPar }) : '',
                  kSplit > 1 ? tx('configureLimits.stripsInSeries', { k: kSplit }) : ''].filter(Boolean);
    return tags.length ? tx('configureLimits.wireRowsTagged', { tags: tags.join(', ') }) : tx('configureLimits.turnsPerSlot');
  })();
  // Current density in the conductor (A/mm², RMS) = strand current / wire area;
  // strand current = phase current / (parallel paths × strands in hand), wire
  // area = width × height.
  const J_A_mm2 = (knobs.I_A / Math.max(1, knobs.nP * kPar)) / Math.max(1e-6, ref.fit.wireWidth_mm * knobs.wireH_mm);
  const baseJ   = (p.I0_A / Math.max(1, p.nP0 * kPar)) / Math.max(1e-6, ref.fit.wireWidth_mm * p.wireH0_mm);
  // Phase copper section = strand area × parallel paths × strands in hand
  // (matches the FEM card's A_phase, so J = I_phase / A_phase holds on both
  // sides).
  const A_phase_mm2 = ref.fit.wireWidth_mm * knobs.wireH_mm * Math.max(1, knobs.nP * kPar);
  const baseA_phase = ref.fit.wireWidth_mm * p.wireH0_mm * Math.max(1, p.nP0 * kPar);
  // A wire count that no longer divides the strands in hand is not a machine:
  // the coil would need a fraction of a turn.  Flagged where the number is
  // typed, so the user is not told about it three screens later by the solver.
  const badTurns = kPar > 1 && (Math.round(knobs.N) % kPar) !== 0;
  // winding connections are derived from THIS reference's slot count (C = slots/6)
  const conns = useMemo(() => windingConnections(ref.geo.numSlots), [ref.geo.numSlots]);
  useEffect(() => {
    if (conns.length && !conns.some((c) => c.nP === knobs.nP)) setKnobs((s) => ({ ...s, nP: conns[0].nP }));
  }, [conns]); // eslint-disable-line react-hooks/exhaustive-deps
  // The current knob warns on REAL trouble only: a current density the copper
  // cannot survive, or the passport's measured demagnetisation limit.  The old
  // rule ("wire limit" = the passport's own base current scaled) flagged red
  // the moment the user nudged the base point by 1 A — 38 A "exceeding" 38 A
  // (user 2026-08-26).
  const overCurr = J_A_mm2 > 25 || knobs.I_A > iMax * 1.001;
  // Hard slot-fit limiter — mirrors the backend constraint
  // (geometry_constraints._wire_height_max): N rows of (wire_height + radial
  // spacing) must fit between the two insulation layers.  Each slider's max is
  // derived from the OTHER knob's current value, so the winding can never
  // overflow the slot (which would push coils across the air gap).
  const availStack_mm  = ref.fit.slotHeight_mm - 2 * ref.fit.insulation_mm;
  const rowPitch_mm    = knobs.wireH_mm + ref.fit.wireSpacingY_mm;   // one wire row + its radial gap
  const stackHeight_mm = knobs.N * rowPitch_mm;
  const overFit   = overflows(ref.fit, knobs.N, knobs.wireH_mm);
  const atLimit   = knobs.N >= ranges.N.max || knobs.wireH_mm >= ranges.wireH_mm.max - 1e-9;
  // Passive stock hint (owner, 2026-09-20): does not restrict the slider —
  // only names the nearest size actually on the shelf. wire_width is FIXED in
  // this tuner (see the module header), so only the thickness knob moves.
  const { data: wireStockData } = useWireStock();
  const stockSizes = wireStockData?.available_sizes ?? [];
  const wireStockNote = stockSizes.length && !isSizeInStock(knobs.wireH_mm, ref.fit.wireWidth_mm, stockSizes)
    ? tx('configureLimits.notInStock', { nearest: formatNearestSizes(nearestStockSizes(knobs.wireH_mm, ref.fit.wireWidth_mm, stockSizes, 2)) })
    : null;
  // The two winding sliders STOP at the slot (user 2026-09-02): turns at the rows
  // that fit the CHOSEN wire, wire at the thickest that fits the CHOSEN turns, so
  // no slider move can make an overflowing combination.  A typed value clamps to
  // the same cap; only a saved configuration or a machine change can still arrive
  // over the limit, and that is what the red line under the sliders is for.
  const turnsSliderMax = ranges.N.max;
  const wireSliderMax  = ranges.wireH_mm.max;

  const set = (k: keyof Knobs) => (v: number) => setKnobs((s) => ({ ...s, [k]: v }));
  // Reset goes back to the machine AS LOADED (the same point the deltas are
  // measured from), falling back to the passport base when nothing is loaded.
  // The drive is a choice of the user, not part of the reference design, so
  // Reset leaves it where it is.
  /** Restore EVERYTHING to a real configuration of the die: every knob, its saved pack, its
   *  default drive.  The user's battery edit for this machine is dropped (the pack is the preset's). */
  const applyPreset = (pr: Preset) => {
    const nk = presetKnobs(knobs, pr);
    setKnobs(nk); setRefKnobs(nk); setBaseConfig(pr.config);
    try { localStorage.setItem(DRIVE_LS, writeDriveChoice(localStorage.getItem(DRIVE_LS), refId, pickDrive(nk))); } catch { /* ignore */ }
    const pk = batteryFromPack(pr.battery);
    try { localStorage.setItem(BATTERY_BY_MACHINE_LS, clearBatteryEdit(readLs(BATTERY_BY_MACHINE_LS), refId)); } catch { /* ignore */ }
    batterySeeded.current = '';
    if (pk) setBattery(pk);
    // the propeller goes back to the one this configuration opens on (the user's pick is dropped)
    if (cooled) updateCool({ propId: null });
  };
  // Which preset the state IS (every knob, the pack and the drive equal) — highlighted.
  const matchedConfig = presets.find((pr) => presetDiff(pr, knobs, battery, batteryFromPack(pr.battery),
    cooled ? { current: propId, wanted: propDefaultFor(pr.config) } : undefined).length === 0)?.config ?? null;
  // The drive is a choice of the user, not part of the reference design, so Reset leaves it
  // where it is — unless a preset is the base: then Reset is "reset to preset" and restores all.
  const reset = () => {
    if (basePreset) { applyPreset(basePreset); return; }
    setKnobs((s) => ({ ...(refKnobs ?? baseKnobs(p)), ...pickDrive(s) }));
    if (cooled) updateCool({ propId: null });
  };
  /** Has the user moved anything off the reference design? */
  // "Modified": anything off the BASE — the machine as loaded, or the preset last applied (an
  // applied preset becomes the baseline: refKnobs = its knobs and drive, machinePack = its pack).
  // the drive is the (transistor, frequency) pair: two ids of one pair are the same drive
  const driveOf = (k: Knobs | null) => (k?.drive === 'pwm'
    ? (pairKey(resolveVariant(variants, k)) || (k.drive_variant ?? '')) : '');
  // the propeller is part of the configuration: another one than the base's default is a modification
  const propModified = cooled && propId !== effectivePropeller({}, allowedProps, propDefaultFor(baseConfig));
  const modified = driveOf(knobs) !== driveOf(refKnobs)
    || propModified
    || (!!machinePack && !sameBattery(battery, machinePack))
    || (() => {
    const r0 = refKnobs ?? baseKnobs(p);
    return (['N', 'L_mm', 'wireH_mm', 'nP', 'I_A', 'rpm'] as const)
      .some((kk) => Math.abs(Number(knobs[kk]) - Number(r0[kk]))
                    > 1e-6 * Math.max(1, Math.abs(Number(r0[kk]))));
  })();
  const tuned = modified;

  const addConfig = () => {
    const n = configs.filter((c) => c.refId === refId).length + 1;
    const name = tx('configure.cfgName', { conn: connLabel(knobs.nP, ref.geo.numSlots), n: knobs.N,
      L: fmt(knobs.L_mm, 0), wire: fmt(knobs.wireH_mm, 2), idx: n });
    const id = `cfg_${Math.random().toString(36).slice(2, 9)}`;
    // The drive rides with the configuration (device + carrier, or Sine), so a
    // saved PWM point says which inverter produced its numbers.
    const drive: DriveRecord = driveOn && variant ? {
      mode: 'pwm', variant_id: variant.id, device: variant.device,
      technology: variant.technology ?? null, carrier_hz: Number(variant.carrier_hz),
      dead_time_s: variant.dead_time_s ?? null, n_parallel: variant.n_parallel ?? null,
      inverter_loss_W: drv ? controllerLossW(drv) : null, tj_C: drv?.tj_C ?? null,
      eta_drive_pct: sysEff.value,
    } : { mode: 'sine' };
    // Saved under a NAME the user can change here (the auto name is the suggestion).
    setAskName({
      title: tx('configure.saveTitle'), label: tx('configure.renameLabel'), initial: name,
      cancelLabel: tx('configure.cancel'), okLabel: tx('configure.ok'),
      onSubmit: (given) => {
        const nm = given.trim() || name;
        setConfigs((cs) => [...cs, { id, name: nm, refId, knobs: { ...knobs }, result, iMax,
          battery: { ...battery }, drive, presetConfig: baseConfig,
          propeller: cooled && propSummary ? { id: propSummary.id, label: `${vendorLabel(propSummary.vendor)} ${modelLabel(propSummary.model)}` } : null }]);
      },
    });
  };
  const delConfig = (id: string) => setConfigs((cs) => cs.filter((c) => c.id !== id));
  // load a saved config back as the current design — knobs + battery (+ reference)
  // Rename a saved configuration (user 2026-08-25) — the auto name is only a
  // starting point.
  const [askName, setAskName] = useState<TextPromptState | null>(null);
  const renameConfig = (c: SavedConfig) => setAskName({
    title: tx('configure.renameTitle'),
    label: tx('configure.renameLabel'), initial: c.name,
    cancelLabel: tx('configure.cancel'), okLabel: tx('configure.ok'),
    onSubmit: (name) => {
      const n = name.trim();
      if (n) setConfigs((cs) => cs.map((x) => (x.id === c.id ? { ...x, name: n } : x)));
    },
  });

  const loadConfig = (c: SavedConfig) => {
    if (c.refId !== refId) { skipReset.current = true; setRefId(c.refId); }
    setKnobs({ ...c.knobs });
    if (c.battery) updateBattery({ ...c.battery });
    setBaseConfig(c.presetConfig ?? presetOfBuild(presets, c.knobs)?.config ?? null);
    if (cooled && c.propeller?.id) updateCool({ propId: c.propeller.id });
    try { localStorage.setItem(DRIVE_LS, writeDriveChoice(localStorage.getItem(DRIVE_LS), c.refId, pickDrive(c.knobs))); } catch { /* ignore */ }
  };

  const RES_COLS: { key: string; label: string; unit: string; d: number; goodHi?: boolean; get: (c: SavedConfig) => number }[] = [
    { key: 'T',    label: tx('configure.colTorque'),  unit: 'N·m', d: 1, goodHi: true,  get: (c) => c.result.T_Nm },
    { key: 'P',    label: tx('configure.colPower'),   unit: 'kW',  d: 2, goodHi: true,  get: (c) => c.result.P_mech_W / 1000 },
    { key: 'V',    label: tx('configure.colDcBus'),  unit: 'V',   d: 0,                get: (c) => c.result.Vphase_peak_V * Math.sqrt(3) },
    { key: 'eff',  label: tx('configure.colEta'),       unit: '%',   d: 1, goodHi: true,  get: (c) => (c.drive?.mode === 'pwm' ? (c.drive.eta_drive_pct ?? NaN) : c.result.efficiency * 100) },
    { key: 'loss', label: tx('configure.colLosses'),  unit: 'W',   d: 0, goodHi: false, get: (c) => c.result.P_loss_W },
    { key: 'J',    label: tx('configure.colJ'),       unit: 'A/mm²', d: 1, goodHi: false, get: (c) => (c.knobs.I_A / Math.max(1, c.knobs.nP)) / Math.max(1e-6, ref.fit.wireWidth_mm * c.knobs.wireH_mm) },
    { key: 'mass', label: tx('configure.colMass'),    unit: 'kg',  d: 2, goodHi: false, get: (c) => c.result.mass_kg },
    { key: 'tm',   label: tx('configure.colTPerMass'),  unit: '',    d: 2, goodHi: true,  get: (c) => c.result.torque_per_mass },
  ];
  const KNB_COLS: { label: string; get: (c: SavedConfig) => string }[] = [
    { label: tx('configureDrive.columnDrive'),
      get: (c) => driveText(c.drive ?? (c.knobs.drive === 'pwm' ? { mode: 'pwm' } : null), tx('configureDrive.sine')) },
    { label: tx('configurePropeller.title'), get: (c) => c.propeller?.label ?? '—' },
    { label: tx('configure.colConn'),   get: (c) => connLabel(c.knobs.nP, (allRefs.find((r) => r.id === c.refId)?.geo.numSlots ?? ref.geo.numSlots)) },
    { label: tx('configure.colTurns'),  get: (c) => `${c.knobs.N}` },
    { label: tx('configure.colLength'), get: (c) => fmt(c.knobs.L_mm, 0) },
    { label: tx('configure.colWireH'), get: (c) => fmt(c.knobs.wireH_mm, 2) },
    { label: tx('configure.colCurrent'),      get: (c) => fmt(c.knobs.I_A, 0) },
    { label: 'rpm',    get: (c) => fmt(c.knobs.rpm, 0) },   // a unit symbol: never translated
  ];
  // best/worst per result column across saved configs (for highlight)
  const resExt: Record<string, { min: number; max: number } | null> = {};
  RES_COLS.forEach((r) => {
    const ns = configs.map(r.get).filter(Number.isFinite);
    resExt[r.key] = ns.length ? { min: Math.min(...ns), max: Math.max(...ns) } : null;
  });


  return (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', bgcolor: 'var(--panel-2)', overflow: 'auto' }}>
      {(draft || draftMsg) && (
        <Alert severity={draft ? (draftOpen ? 'success' : 'info') : 'warning'} sx={{ m: 1, fontSize: 12 }}
          onClose={() => { setDraft(null); setDraftMsg(null); setDraftOpen(false); }}
          action={draft ? (
            <Box sx={{ display: 'flex', gap: 0.5, alignItems: 'center' }}>
              {!draftOpen
                ? <Button size="medium" variant="contained" onClick={openDraft}
                    sx={{ textTransform: 'none', fontWeight: 700, whiteSpace: 'nowrap',
                          bgcolor: '#1d4ed8', '&:hover': { bgcolor: '#2563eb' } }}>
                    {tx('configure.openInTuner')}
                  </Button>
                : <Button size="small" onClick={() => { void saveDraft(); }} sx={{ textTransform: 'none' }}>{tx('configure.saveToDraft')}</Button>}
            </Box>) : undefined}>
          {draft && (
            <>
              🤖 {tx('configure.bannerDraft', {
                name: draft.name, client: draft.created_by.client_name,
                die: draft.starting_point.die, config: draft.starting_point.config,
                L: fmt(draft.params.stack_mm, 1), I: fmt(draft.params.current_a_rms, 1),
                rpm: fmt(draft.params.speed_rpm, 0),
                conn: draft.params.connection ? tx('configure.bannerConn', { conn: draft.params.connection }) : '' })}
              {!draftOpen && tx('configure.bannerOpenHint', { button: tx('configure.openInTuner') })}
              {draftOpen && draftTarget && tx('configure.bannerOpened')}
              {draftHeadline && (
                <Box sx={{ mt: 0.5 }}>
                  {tx('configure.femOnFile', {
                    what: ({ coupled: tx('configure.whatCoupled'), thermal: tx('configure.whatThermal'),
                             em: tx('configure.whatEm') } as Record<string, string>)[draftHeadline.what] ?? draftHeadline.what,
                    T: fmt(draftHeadline.torque_nm ?? NaN, 1), P: fmt(draftHeadline.power_kw ?? NaN, 2),
                    eta: fmt(draftHeadline.efficiency_shaft_pct ?? NaN, 1) })}
                </Box>
              )}
            </>
          )}
          {draftMsg && <Box sx={{ mt: draft ? 0.5 : 0 }}>{draftMsg}</Box>}
        </Alert>
      )}
      {/* This account's OWN drafts (MCP Stage 3), moved here from the Motors
          catalog page 2026-09-30 — cross-account drafts + runs live in
          Admin -> Agent activity instead.  Renders nothing while empty. */}
      <MyAgentDraftsBlock />
      {/* Header — NO reference picker (user 2026-08-25 "drop this menu"):
          the Configurator always mirrors ONE machine — the opened draft when
          one is showing, otherwise the loaded/open machine — and never a
          passport borrowed from some OTHER machine (owner 2026-09-29). */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, px: 2, py: 1.25, borderBottom: '1px solid var(--line-soft)' }}>
        <BoltIcon sx={{ color: '#60a5fa', fontSize: 20 }} />
        <Typography sx={{ fontSize: 14, fontWeight: 800, color: 'var(--text-0)' }} title={tx('configure.instant')}>{tx('configure.title')}</Typography>
        <Box sx={{ flex: 1 }} />
        <Typography sx={{ fontSize: 12, fontWeight: 700, color: blocked ? '#f59e0b' : 'var(--text-1)' }}>
          {draftOpen && draft
            ? (draftTarget
                ? tx('configure.headerDraft', { name: draft.name, die: draft.starting_point.die, config: draft.starting_point.config })
                : tx('configure.headerDraftNoModel', { name: draft.name }))
            : (loadingModel ? '' : liveMatched ? ref.name : tx('configure.headerNoModel', { label: blockedLabel }))}
        </Typography>
        {/* One clear way back to the reference design (user 2026-08-26) —
            replaces the per-tile "% vs ref" captions.  Meaningless with no
            model loaded, so it disappears rather than resetting to nothing. */}
        {!blocked && !loadingModel && (
          <Button size="small" variant={tuned ? 'contained' : 'outlined'} onClick={reset}
            startIcon={<RestartAltIcon sx={{ fontSize: 15 }} />}
            disabled={!tuned}
            title={tx('configureLimits.resetToReferenceTip')}
            sx={{ ml: 1.5, textTransform: 'none', fontSize: 11, py: 0.1, height: 24, boxSizing: 'border-box',
                  ...(tuned ? { bgcolor: '#1d4ed8', '&:hover': { bgcolor: '#2563eb' } } : {}) }}>
            {tuned ? tx('configureLimits.resetShort') : tx('configureLimits.referenceDesign')}
          </Button>
        )}
      </Box>
      {loadingModel && (
        <Box sx={{ px: 2, py: 1.5, display: 'flex', alignItems: 'center', gap: 1 }}>
          <CircularProgress size={14} sx={{ color: '#60a5fa' }} />
          <Typography sx={{ fontSize: 14, fontWeight: 800, color: 'var(--text-0)' }}>{tx('configure.loading')}</Typography>
        </Box>
      )}
      {blocked && (
        <Box sx={{ px: 2, pb: 2 }}>
          <Alert severity="warning" sx={{ fontSize: 12 }}
            title={tx('configure.noModelBody') + (draftOpen ? tx('configure.noModelDraft') : tx('configure.noModelLoaded'))}>
            <Typography sx={{ fontSize: 13, fontWeight: 700 }}>
              {tx('configure.noModelTitle', { label: blockedLabel })}
            </Typography>
          </Alert>
        </Box>
      )}

      {/* Nothing below computes or renders while `blocked` — see the empty
          state above.  Every tile, slider and chart in this block reads
          `ref`/`p`, which the guards above only let through once it is the
          SAME machine as the header names. */}
      {!blocked && !loadingModel && (
      <>
      <Box sx={{ display: 'grid', gap: 2, p: 2, alignItems: 'start',
                 gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 560px), 1fr))' }}>
        {/* ── KNOBS, then ONE ROW: battery | cross-section | side view (owner 2026-10-05: sliders, battery and the motor's
            voltage marker visible together) ── */}
        <Box sx={{ minWidth: 0, display: 'flex', flexDirection: 'column', gap: 1.5 }}>
        <Box sx={{ ...PANEL, p: 2 }}>
          <Typography sx={{ fontSize: 12, fontWeight: 800, color: 'var(--text-1)', mb: 0.25 }}>{ref.name}</Typography>
          <Typography sx={{ fontSize: 11, color: 'var(--text-3)', mb: 1.5 }}>
            {tx('configure.subtitle', { slots: ref.slots, poles: ref.poles,
              torque: (p.T0_Nm ?? 0).toFixed((p.T0_Nm ?? 0) < 10 ? 1 : 0), rpm: p.rpm0 ?? '?' })}
          </Typography>

          {/* i18n-guard:begin — every user-visible string below goes through tx() */}
          {/* two columns when there is room (build | operating point + drive), one on a phone */}
          <Box sx={{ display: 'grid', columnGap: 3, gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 280px), 1fr))', alignItems: 'start' }}>
          <Box sx={{ minWidth: 0 }}>
          {presets.length > 0 && (
            <Box sx={{ display: 'flex', alignItems: 'center', columnGap: 1, rowGap: 0.5, mb: 1.25, flexWrap: 'wrap' }}>
              <Typography sx={{ ...LABEL, flex: '1 1 120px', minWidth: 0 }} title={tx('configure.presetTip')}>
                {tx('configure.preset')}
                {basePreset && modified && (
                  <Box component="span" sx={{ color: '#fbbf24' }}>
                    {' · '}{tx('configure.presetModified', { config: basePreset.config })}
                  </Box>
                )}
              </Typography>
              {defaultMotor && defaultRef && (
                <Button size="small" onClick={goDefault}
                  title={tx('configure.resetToDefaultTip', { die: defaultMotor.die, config: defaultMotor.config })}
                  sx={{ textTransform: 'none', fontSize: 12, minWidth: 0, py: 0.25 }}>
                  {tx('configure.resetToDefault')}
                </Button>
              )}
              <ToggleButtonGroup exclusive size="small" value={matchedConfig} sx={{ ml: 'auto', flexWrap: 'wrap' }}>
                {presets.map((pr) => (
                  <ToggleButton key={pr.config} value={pr.config} onClick={() => applyPreset(pr)}
                    title={tx('configure.presetTip')}
                    sx={{ px: 1.5, py: 0.25, fontSize: 12, color: 'var(--text-2)', borderColor: 'var(--line)',
                      '&.Mui-selected': { bgcolor: '#1d4ed8', color: '#fff', '&:hover': { bgcolor: '#2563eb' } } }}>
                    {pr.config}{pr.card_date && <Box component="span" sx={{ ml: 0.75 }}><CardBadge date={pr.card_date} /></Box>}
                  </ToggleButton>
                ))}
              </ToggleButtonGroup>
            </Box>
          )}
          <Typography sx={{ ...LABEL, color: 'var(--text-4)', mb: 0.75 }}>{tx('configureLimits.build')}</Typography>
          <KnobSlider label={tx('configureLimits.stackLength')} unit="mm" value={knobs.L_mm} base={p.L0_mm} min={ranges.L_mm.min} max={ranges.L_mm.max} step={1} d={0}
            onChange={set('L_mm')} onRangeChange={isAdmin ? setLRange : undefined} warn={above(knobs.L_mm, ranges.L_mm)}
            limitNote={limitNote('L_mm')} onClearHand={isAdmin ? clearLMax : undefined} />
          {limitMsg && <Typography sx={{ fontSize: 11, color: '#f87171', mt: -0.5, mb: 0.75 }}>{limitMsg}</Typography>}
          <KnobSlider label={rowsLabel} value={knobs.N} base={p.N0} min={ranges.N.min} max={turnsSliderMax} step={1} d={0} onChange={set('N')} onRangeChange={isAdmin ? setRange('N') : undefined} warn={overFit || badTurns}
            limitNote={limitNote('N')} />
          <KnobSlider label={tx('configureLimits.wireThickness')} unit="mm" value={knobs.wireH_mm} base={p.wireH0_mm} min={ranges.wireH_mm.min} max={wireSliderMax} step={WIRE_STEP_MM} d={1} onChange={set('wireH_mm')} onRangeChange={isAdmin ? setRange('wireH_mm') : undefined} warn={overFit}
            limitNote={limitNote('wireH_mm')} />
          {overFit ? (
            <Typography sx={{ fontSize: 11, color: '#f87171', mt: -0.5, mb: 1 }}
              title={tx('configureLimits.overFitTip', { n: knobs.N, wire: fmt(knobs.wireH_mm, 2), gap: fmt(ref.fit.wireSpacingY_mm, 2), stack: fmt(stackHeight_mm, 1), slot: fmt(ref.fit.slotHeight_mm, 1), ins: fmt(ref.fit.insulation_mm, 2), avail: fmt(availStack_mm, 1) })}>
              ⚠ {tx('configureLimits.overFit', { stack: fmt(stackHeight_mm, 1), avail: fmt(availStack_mm, 1) })}
            </Typography>
          ) : badTurns ? (
            <Typography sx={{ fontSize: 11, color: '#f87171', mt: -0.5, mb: 1 }}
              title={tx('configureLimits.badTurnsTip', { k: kPar, n: knobs.N, turns: (knobs.N / kPar).toFixed(2), lo: kPar * Math.floor(knobs.N / kPar), hi: kPar * (Math.floor(knobs.N / kPar) + 1) })}>
              ⚠ {tx('configureLimits.badTurns', { n: knobs.N, k: kPar })}
            </Typography>
          ) : atLimit ? (
            <Typography sx={{ fontSize: 11, color: 'var(--text-4)', mt: -0.5, mb: 1 }}
              title={tx('configureLimits.atLimitTip', { n: knobs.N, wire: fmt(knobs.wireH_mm, 2), gap: fmt(ref.fit.wireSpacingY_mm, 2), stack: fmt(stackHeight_mm, 1), avail: fmt(availStack_mm, 1) })}>
              {tx('configureLimits.atLimit')}
            </Typography>
          ) : wireStockNote ? (
            <Typography sx={{ fontSize: 11, color: '#f59e0b', mt: -0.5, mb: 1 }}
              title={tx('configureLimits.stockTip')}>
              {wireStockNote}
            </Typography>
          ) : null}

          <Box sx={{ display: 'flex', alignItems: 'center', columnGap: 1, rowGap: 0.5, mt: 0.5, mb: 1, flexWrap: 'wrap' }}>
            <Typography sx={{ ...LABEL, flex: '1 1 140px', minWidth: 0 }}
              title={connWarn ? tx('configureLimits.connWarnTip', { line: fmt(connWarn.line, 1), nominal: fmt(connWarn.nominal, 1) }) : undefined}>
              {tx('configureLimits.connection')}
              {connWarn && (
                <Box component="span" sx={{ color: '#fbbf24', ...unitCase() }}>
                  {' · ⚠ '}{tx('configureLimits.connWarn', { line: fmt(connWarn.line, 1), nominal: fmt(connWarn.nominal, 1) })}
                </Box>
              )}
            </Typography>
            <ToggleButtonGroup exclusive size="small" value={knobs.nP} sx={{ ml: 'auto' }} onChange={(_, v) => v != null && set('nP')(v)}>
              {conns.map((c) => (
                <ToggleButton key={c.nP} value={c.nP}
                  title={c.nP === 1 ? tx('configureLimits.connSeries') : c.nS === 1 ? tx('configureLimits.connParallel') : tx('configureLimits.connMixed', { s: c.nS, p: c.nP })}
                  sx={{ px: 1.5, py: 0.25, fontSize: 12, color: 'var(--text-2)', borderColor: 'var(--line)',
                    '&.Mui-selected': { bgcolor: '#1d4ed8', color: '#fff', '&:hover': { bgcolor: '#2563eb' } } }}>
                  {c.label}
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
          </Box>

          </Box>
          <Box sx={{ minWidth: 0 }}>
          <Typography sx={{ ...LABEL, color: 'var(--text-4)', mt: 0, mb: 0.75 }}>{tx('configureLimits.operatingPoint')}</Typography>
          {/* rms is the knob; the PEAK rides beside it (user 2026-08-26) —
              inverters and datasheets are quoted in peak, the coil sees rms. */}
          {/* ── PROPELLER (a die cooled only by its propeller): which one, the ambient air, and
              whether the propeller sets the load.  One compact block, labels in the title style. ── */}
          {cooled && (
            <Box sx={{ mb: 1.25 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', columnGap: 1, mb: 0.5, flexWrap: 'nowrap', minHeight: 28 }}>
                <Typography sx={{ ...LABEL, flex: '0 0 auto' }} title={tx('configurePropeller.pickerTip')}>{tx('configurePropeller.title')}</Typography>
                <select value={propId ?? ''} aria-label={tx('configurePropeller.title')}
                  onChange={(e) => updateCool({ propId: e.target.value })}
                  title={propSummary && propSummary.power_data === 'estimated' ? tx('configurePropeller.estimatedTip') : tx('configurePropeller.pickerTip')}
                  style={{ background: 'transparent', border: '1px solid var(--line)', borderRadius: 4, color: 'var(--text-0)', fontSize: 12, fontFamily: 'monospace', padding: '2px 4px', flex: '1 1 auto', minWidth: 0, maxWidth: 260 }}>
                  {!allowedProps.length && <option value="" style={{ color: '#000' }}>…</option>}
                  {allowedProps.map((x) => (
                    <option key={x.id} value={x.id} disabled={!x.selectable} style={{ color: '#000' }}>
                      {tx(x.blades ? 'configurePropeller.label' : 'configurePropeller.labelNoBlades', { vendor: vendorLabel(x.vendor), model: modelLabel(x.model), blades: x.blades ?? '' })}
                      {x.selectable ? '' : ` · ${tx('configurePropeller.noTestData')}`}
                    </option>
                  ))}
                </select>
                <input type="number" step={1} value={ambTxt ?? String(ambient)} aria-label={tx('configurePropeller.ambient')}
                  title={tx('configurePropeller.ambientTip')}
                  onChange={(e) => setAmbTxt(e.target.value)}
                  onBlur={() => setAmbTxt(null)}
                  style={{ width: 52, flex: '0 0 auto', background: 'transparent', border: '1px solid var(--line)', borderRadius: 4, color: 'var(--text-0)', fontSize: 13, fontWeight: 700, fontFamily: 'monospace', textAlign: 'right', padding: '1px 5px' }} />
                <Box component="span" sx={{ fontSize: 11, color: 'var(--text-3)', flex: '0 0 auto' }}>°C</Box>
              </Box>
              <Box sx={{ display: 'flex', alignItems: 'center', columnGap: 1, flexWrap: 'nowrap', minHeight: 30 }}>
                <Typography sx={{ ...LABEL, flex: '1 1 auto', minWidth: 0 }} title={tx('configurePropeller.loadTip')}>{tx('configurePropeller.load')}</Typography>
                <ToggleButtonGroup exclusive size="small" value={propLoad ? 'prop' : 'manual'}
                  onChange={(_, v) => { if (v === 'prop' || v === 'manual') updateCool({ load: v }); }}>
                  <ToggleButton value="prop" title={tx('configurePropeller.loadPropTip')}
                    sx={{ px: 1.5, py: 0.25, fontSize: 12, color: 'var(--text-2)', borderColor: 'var(--line)', '&.Mui-selected': { bgcolor: '#1d4ed8', color: '#fff', '&:hover': { bgcolor: '#2563eb' } } }}>{tx('configurePropeller.loadProp')}</ToggleButton>
                  <ToggleButton value="manual" title={tx('configurePropeller.loadManualTip')}
                    sx={{ px: 1.5, py: 0.25, fontSize: 12, color: 'var(--text-2)', borderColor: 'var(--line)', '&.Mui-selected': { bgcolor: '#1d4ed8', color: '#fff', '&:hover': { bgcolor: '#2563eb' } } }}>{tx('configurePropeller.loadManual')}</ToggleButton>
                </ToggleButtonGroup>
              </Box>
            </Box>
          )}
          <KnobSlider label={tx('configureLimits.phaseCurrent')} unit="A" value={knobs.I_A} base={p.I0_A}
            min={ranges.I_A.min} max={ranges.I_A.max} step={1} d={0}
            sub={tx('configureLimits.peakOf', { value: fmt(knobs.I_A * Math.SQRT2, 0) })}
            onChange={set('I_A')} onRangeChange={isAdmin ? setRange('I_A') : undefined} warn={overCurr || above(knobs.I_A, ranges.I_A) || (propLoad && loadRefused)}
            disabled={propLoad} zone={propLoad ? null : zones.I}
            limitNote={propLoad
              ? { text: tx('configurePropeller.currentFromProp'), tip: tx('configurePropeller.currentFromPropTip') }
              : (cooled ? { ...limitNote('I_A'), tip: `${limitNote('I_A').tip} ${tx('configurePropeller.zoneTip')}` } : limitNote('I_A'))} />
          <KnobSlider label={tx('configureLimits.speed')} unit="rpm" value={knobs.rpm} base={p.rpm0} min={ranges.rpm.min} max={ranges.rpm.max} step={50} d={0} onChange={set('rpm')} onRangeChange={isAdmin ? setRange('rpm') : undefined}
            warn={above(knobs.rpm, ranges.rpm)} zone={cooled ? zones.rpm : null}
            limitNote={(() => {
              const n = limitNote('rpm');
              if (!cooled) return n;
              const ext = propPoint?.extrapolated && series?.rpm_range_tested
                ? { text: ` · ${tx('configurePropeller.beyondTested')}`, tip: tx('configurePropeller.beyondTestedTip', { lo: fmt(series.rpm_range_tested[0], 0), hi: fmt(series.rpm_range_tested[1], 0) }) }
                : null;
              return { ...n, text: n.text + (ext ? ext.text : ''), tip: `${n.tip} ${tx('configurePropeller.zoneTip')}${ext ? ` ${ext.tip}` : ''}` };
            })()} />

          {/* ── DRIVE: Sine | PWM (owner 2026-10-05) ─────────────────────
              PWM lists only the drive variants COMPUTED for this motor — a
              device at a carrier, each already in the passport.  Device,
              dead time and parallel count are read-only facts of the
              variant; nothing is calculated here. */}
          <Typography sx={{ ...LABEL, color: 'var(--text-4)', mt: 1.5, mb: 0.75, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
            title={variants.length ? driveFactsTip : tx('configureDrive.notComputedTip')}>
            {tx('configureDrive.title')}
            {!variants.length && (
              <Box component="span" sx={{ color: '#fbbf24' }}>{' · '}{tx('configureDrive.notComputed')}</Box>
            )}
          </Typography>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.75, flexWrap: 'nowrap', minHeight: 30 }}
            title={variants.length ? undefined : tx('configureDrive.notComputedTip')}>
            <ToggleButtonGroup exclusive size="small" value={driveOn ? 'pwm' : 'sine'}
              onChange={(_, v) => {
                if (v === 'pwm' && variants.length) setDrive(choiceOf(variant ?? resolveVariant(variants, knobs) ?? variants[0]));
                else if (v === 'sine') setDrive({ drive: 'sine' });
              }}>
              <ToggleButton value="sine" title={tx('configureDrive.sineTip')}
                sx={{ px: 1.5, py: 0.25, fontSize: 12, color: 'var(--text-2)', borderColor: 'var(--line)', '&.Mui-selected': { bgcolor: '#1d4ed8', color: '#fff', '&:hover': { bgcolor: '#2563eb' } } }}>{tx('configureDrive.sine')}</ToggleButton>
              <ToggleButton value="pwm" disabled={!variants.length} title={tx('configureDrive.pwmTip')}
                sx={{ px: 1.5, py: 0.25, fontSize: 12, color: 'var(--text-2)', borderColor: 'var(--line)', '&.Mui-selected': { bgcolor: '#1d4ed8', color: '#fff', '&:hover': { bgcolor: '#2563eb' } } }}>{tx('configureDrive.pwm')}</ToggleButton>
            </ToggleButtonGroup>
            {driveOn && variant && (() => {
              const devs = variantDevices(variants);
              const carriers = variantCarriers(variants, variant.device);
              const selStyle = { background: 'transparent', border: '1px solid var(--line)', borderRadius: 4, color: 'var(--text-0)', fontSize: 12, fontFamily: 'monospace', padding: '2px 4px', minWidth: 0 } as const;
              return (
                <>
                  {/* transistor: the device only; its technology (Si / GaN) and the other facts ride in the tooltip */}
                  <select value={variant.device} aria-label={tx('configureDrive.transistor')}
                    onChange={(e) => { const v = switchDevice(variants, variant, e.target.value); if (v) setDrive(choiceOf(v)); }}
                    title={`${tx('configureDrive.transistorTip')}\n${driveFactsTip}`}
                    style={{ ...selStyle, flex: '1 1 auto', maxWidth: 200 }}>
                    {devs.map((d) => (<option key={d.device} value={d.device} style={{ color: '#000' }}>{d.device}</option>))}
                  </select>
                  {/* PWM frequency: only the carriers computed for THIS transistor in this motor */}
                  <select value={Number(variant.carrier_hz)} aria-label={tx('configureDrive.frequency')}
                    onChange={(e) => { const v = switchCarrier(variants, variant, Number(e.target.value)); if (v) setDrive(choiceOf(v)); }}
                    title={tx('configureDrive.frequencyTip')}
                    style={{ ...selStyle, flex: '0 0 auto' }}>
                    {carriers.map((c) => (<option key={c} value={c} style={{ color: '#000' }}>{carrierLabel(c)}</option>))}
                  </select>
                </>
              );
            })()}
          </Box>
          {/* ONE reserved two-line slot: a refusal fills it, it never pushes the blocks below
              (the tiles on the right turn to "—" at the same time) */}
          <Box sx={{ height: 34, overflow: 'hidden', mb: 0.5 }}
            title={driveRefusals.map((r) => `${r.text}. ${r.tip}`).join('\n')}>
            {driveRefusals.length > 0 && (
              <Typography sx={{ fontSize: 11, fontWeight: 700, color: '#f87171', lineHeight: 1.3,
                display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                ⚠ {driveRefusals[0].text}{driveRefusals.length > 1 ? ` (+${driveRefusals.length - 1})` : ''}
              </Typography>
            )}
          </Box>


          <Button onClick={reset} size="small" disabled={!tuned}
            startIcon={<RestartAltIcon sx={{ fontSize: 16 }} />}
            title={tx('configureLimits.resetToReferenceTip')}
            sx={{ fontSize: 11, textTransform: 'none',
                  color: tuned ? '#60a5fa' : 'var(--text-3)', mt: 1 }}>
            {tx('configureLimits.resetToReference')}
          </Button>
          </Box>
          </Box>
          {/* i18n-guard:end */}
        </Box>
        {/* battery | cross-section | side view: one row (they wrap on a narrow screen) */}
        <Box sx={{ display: 'flex', gap: 1.5, flexWrap: 'wrap', alignItems: 'stretch' }}>
          <Box sx={{ flex: '1 1 300px', minWidth: 0 }}>
            <BatteryPanel vDc={result.Vphase_peak_V * Math.sqrt(3)} bat={battery} onChange={updateBattery}
              machinePack={machinePack} onReset={resetBattery}
              stockNote={!batteryKnown} vDcTip={tx('configure.motorVTip', { I: fmt(knobs.I_A, 1), rpm: fmt(knobs.rpm, 0) })} />
          </Box>
          <GeometryProjections ref0={ref} knobs={knobs} />
        </Box>
        </Box>

        {/* ── RESULT ── */}
        <Box sx={{ flex: '2 1 460px', minWidth: 360, display: 'flex', flexDirection: 'column', gap: 1.25 }}>
          {/* SEVEN ROWS — the same order as the Simulation summary card
              (user 2026-09-04: "the same for both simulation and configure"):
              1 torque · power · mass · efficiency · ripple · densities
              2 total loss · iron · copper · magnet · stator/rotor heat · loss density
              3 voltages + current density (unchanged)
              4 phase section · wire coating · lead cable · R phase · R line-line
              5 Ld · Lq · ψ_PM · Lq/Ld
              6 KV · Kt · Km · Km/mass
              7 demag koef · saturation koef · total koef */}
          {/* ONE red line (fixed height: it never moves the grid) — only for a propeller-cooled machine */}
          {cooled && (
            <Typography sx={{ ...LABEL, fontSize: 11, lineHeight: '16px', height: 16, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
              color: '#f87171', mb: -0.5 }} title={propLine ? `${propLine.text}. ${propLine.tip}` : undefined}>
              {propLine ? `⚠ ${propLine.text}` : ''}
            </Typography>
          )}
          <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
            <MetricTile label={tx('configure.torque')} value={result.T_Nm} unit="N·m" d={1} base={baseRes.T_Nm} goodHi
              tip={KT_BASIS_TIP(result.kt_km_basis)} />
            <MetricTile label={tx('configure.power')} value={result.P_mech_W / 1000} unit="kW" d={2} base={baseRes.P_mech_W / 1000} goodHi
              absLevel={propBad ? 'bad' : undefined} tip={propBad && propLine ? propLine.text : undefined} />
            <MetricTile label={tx('configure.mass')} value={result.mass_kg} unit="kg" d={2} base={baseRes.mass_kg} goodHi={false} />
            <MetricTile label={tx('configure.efficiency')} value={sysEff.value} unit="%" d={1} base={baseSysEff} goodHi
              absLevel={propBad ? 'bad' : undefined}
              tip={`${tx('configure.efficiencyTip')}${propBad && propLine ? ` ${propLine.text}` : ''}`}
              blankTip={tx(sysEff.blank === 'refused' ? 'configureDrive.blankRefused' : 'configureDrive.blankMissing')} />
            {ref.passport.ripple0_pct != null && (
              <MetricTile label={tx('configure.tRipple')} value={ref.passport.ripple0_pct} unit="%" d={1}
                base={ref.passport.ripple0_pct} goodHi={false} tip={tx('configure.tRippleTip')} />
            )}
            <MetricTile label={tx('configure.tPerMass')} value={result.torque_per_mass} unit="N·m/kg" d={2} base={baseRes.torque_per_mass} goodHi />
            <MetricTile label={tx('configure.pPerMass')} value={result.power_per_mass_W_kg / 1000} unit="kW/kg" d={2} base={baseRes.power_per_mass_W_kg / 1000} goodHi />
          </Box>
          <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
            <MetricTile label={tx('configure.totalLoss')} value={totalLossShown(result.P_loss_W, driveMode, drv)} unit="W" d={0}
              base={baseRes.P_loss_W} goodHi={false} tip={tx('configure.totalLossTip')} blankTip={tx('configure.totalLossBlankTip')} />
            <MetricTile label={tx('configure.ironLoss')} value={result.P_fe_W} unit="W" d={0} base={baseRes.P_fe_W} goodHi={false} />
            <MetricTile label={tx('configure.copperLoss')} value={result.P_cu_W} unit="W" d={0} base={baseRes.P_cu_W} goodHi={false} />
            <MetricTile label={tx('configure.magnetLoss')} value={result.P_mag_W} unit="W" d={0} base={baseRes.P_mag_W} goodHi={false} />
            {/* ── HEAT TO REMOVE, per side — what the cooling is sized on ── */}
            <MetricTile label={tx('configure.statorHeat')} value={result.P_loss_stator_W} unit="W" d={0}
              base={baseRes.P_loss_stator_W} goodHi={false}
              tip={tx(result.loss_split_measured ? 'configure.statorHeatTipMeasured' : 'configure.statorHeatTipUnknown')} />
            <MetricTile label={tx('configure.rotorHeat')} value={result.P_loss_rotor_W} unit="W" d={0}
              base={baseRes.P_loss_rotor_W} goodHi={false}
              absLevel={result.loss_split_measured ? undefined : 'warn'}
              tip={tx(result.loss_split_measured ? 'configure.rotorHeatTipMeasured' : 'configure.rotorHeatTipUnknown')} />
            <MetricTile label={tx('configure.lossDensity')} value={lossDensityShown(result.loss_density_W_kg, result.P_loss_W, driveMode, drv)} unit="W/kg" d={0}
              base={baseRes.loss_density_W_kg} goodHi={false} blankTip={tx('configure.totalLossBlankTip')} />
            {/* ONE contiguous controller group at the end of the loss row (owner 2026-10-05): the PWM loss,
                the controller loss, then T_j, the efficiencies and P cont — same cells in Sine and PWM */}
            {lossTailTiles(driveMode, drv).map(renderSpec)}
            {driveRowTiles(driveMode, drv, variant ? (devLimits[variant.device]?.t_j_max_c ?? null) : null, motorEfficiency(driveMode, drv, result.P_mech_W, result.P_loss_W)).map(renderSpec)}
          </Box>
          {/* ── TEMPERATURES — ONE row for a propeller-cooled machine, the same five tiles from the first render ── */}
          {cooled && (
            <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
              {tempTiles.map(renderTemp)}
            </Box>
          )}
          <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
            <MetricTile label={tx('configure.dcBusMin')} value={result.Vphase_peak_V * Math.sqrt(3)} unit="V" d={0} base={baseRes.Vphase_peak_V * Math.sqrt(3)} />
            <MetricTile label={tx('configure.vLinePeak')} value={result.Vline_peak_V} unit="V" d={1} base={baseRes.Vline_peak_V} />
            <MetricTile label={tx('configure.vPhasePeak')} value={result.Vphase_peak_V} unit="V" d={1} base={baseRes.Vphase_peak_V} />
            <MetricTile label={tx('configure.vLineRms')} value={result.Vline_rms_V} unit="V" d={1} base={baseRes.Vline_rms_V} />
            <MetricTile label={tx('configure.vPhaseRms')} value={result.Vphase_rms_V} unit="V" d={1} base={baseRes.Vphase_rms_V} />
            {/* Absolute thresholds, not "vs reference": ≤12 A/mm² is a
                continuous-duty winding, ≤25 a short-peak one, above that the
                copper cooks whatever the reference did. */}
            <MetricTile label={tx('configure.currDensity')} value={J_A_mm2} unit="A/mm²" d={1} base={baseJ}
              absLevel={J_A_mm2 <= 12 ? 'ok' : J_A_mm2 <= 25 ? 'warn' : 'bad'} />
          </Box>
          <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
            {/* Phase copper section (user 2026-08-26) — the same quantity the
                Simulation card shows: strand area × parallel paths, so
                I_phase / A_phase is exactly the density above it. */}
            <MetricTile label={tx('configure.phaseSection')} value={A_phase_mm2} unit="mm²" d={2}
              base={baseA_phase} goodHi />
            {/* Wire coating (user 2026-08-30) — measured copper over the measured
                winding window; turns and wire height move it, so the tuner can
                say when a variant stops being windable.  Absolute thresholds:
                hand-wound rectangular wire lives at ~45-60 %. */}
            {result.slot_fill_pct != null && (
              <MetricTile label={tx('configure.fillFactor')} value={result.slot_fill_pct} unit="%" d={1}
                base={p.slot_fill0_pct ?? result.slot_fill_pct}
                absLevel={result.slot_fill_pct <= 60 ? 'ok'
                          : result.slot_fill_pct <= 75 ? 'warn' : 'bad'} />
            )}
            {/* Nearest lead cable for that section (user 2026-08-26): gauge
                number + bare conductor diameter. */}
            {(() => {
              const c = pickCable(A_phase_mm2);
              return c ? (
                <Box sx={{ ...PANEL, p: 0.9, flex: '0 0 auto', width: TILE_W, boxSizing: 'border-box' }}
                  title={tx('configure.leadCableTip', { strands: c.strands, area: c.area_mm2, section: A_phase_mm2.toFixed(2),
                      d: c.d_mm, od: c.od_mm, thk: c.thk_mm, r: c.r_ohm_km, irated: c.i_rated_A, imax: c.i_max_A, roll: c.roll_m })
                    + (c.suspect ? tx('configure.cableSuspectLine', { text: tx(c.awg === '10awg' ? 'configure.cableSuspect10' : 'configure.cableSuspect75') }) : '')}>
                  <Typography sx={{ ...LABEL, fontSize: 9.5 }}>{tx('configure.leadCable')}</Typography>
                  <Typography sx={{ fontSize: 16, fontWeight: 800, color: 'var(--text-0)',
                    fontFamily: 'monospace', lineHeight: 1.2, whiteSpace: 'nowrap' }}>
                    {c.awg.replace('awg', ' AWG')}
                    <Box component="span" sx={{ fontSize: 10.5, color: 'var(--text-3)', ml: 0.5 }}>
                      Ø{c.od_mm.toFixed(1)} mm
                    </Box>
                  </Typography>
                </Box>
              ) : null;
            })()}
            <MetricTile label={tx('configure.rPhase')} value={result.R_ohm * 1000} unit="mΩ" d={1} base={baseRes.R_ohm * 1000}
              tip={tx('configure.rPhaseTip')} />
            <MetricTile label={tx('configure.rLineLine')} value={result.R_ohm * 2000} unit="mΩ" d={1} base={baseRes.R_ohm * 2000}
              tip={tx('configure.rLineLineTip')} />
          </Box>
          {(result.Ld_mH != null || result.Lq_mH != null || result.psi_pm_mWb != null) && (
            <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
              {result.Ld_mH != null && (
                <MetricTile label="Ld" value={result.Ld_mH} unit="mH" d={3} base={baseRes.Ld_mH ?? result.Ld_mH} />
              )}
              {result.Lq_mH != null && (
                <MetricTile label="Lq" value={result.Lq_mH} unit="mH" d={3} base={baseRes.Lq_mH ?? result.Lq_mH} />
              )}
              {result.psi_pm_mWb != null && (
                <MetricTile label="ψ_PM" value={result.psi_pm_mWb} unit="mWb" d={2} base={baseRes.psi_pm_mWb ?? result.psi_pm_mWb} />
              )}
              {result.Ld_mH != null && result.Lq_mH != null && result.Ld_mH > 0 && (
                <MetricTile label={tx('configure.lqLd')} value={result.Lq_mH / result.Ld_mH} unit="" d={2}
                  base={(baseRes.Ld_mH ?? result.Ld_mH) > 0
                    ? (baseRes.Lq_mH ?? result.Lq_mH) / (baseRes.Ld_mH ?? result.Ld_mH)
                    : result.Lq_mH / result.Ld_mH}
                  tip={tx('configure.lqLdTip')} />
              )}
            </Box>
          )}
          <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
            <MetricTile label={tx('configure.kvNoLoad')} value={result.KV_rpm_per_Vline} unit="rpm/V" d={1} base={baseRes.KV_rpm_per_Vline} />
            {result.Kt_Nm_per_A != null && (
              <MetricTile label={tx('configure.kt')} value={result.Kt_Nm_per_A} unit="N·m/A" d={3} base={baseRes.Kt_Nm_per_A ?? result.Kt_Nm_per_A} goodHi
                tip={KT_BASIS_TIP(result.kt_km_basis)} />
            )}
            <MetricTile label={tx('configure.km')} value={result.Km_Nm_sqrtW} unit="N·m/√W" d={3} base={baseRes.Km_Nm_sqrtW} goodHi
              tip={KT_BASIS_TIP(result.kt_km_basis)} />
            <MetricTile label={tx('configure.kmPerMass')} value={result.Km_per_mass} unit="N·m/(√W·kg)" d={3} base={baseRes.Km_per_mass} goodHi />
          </Box>
          {(result.demag_keep_pct != null || result.saturation_pct != null) && (
            <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
              {result.demag_keep_pct != null && (
                <MetricTile label={tx('configure.demagKoef')} value={result.demag_keep_pct} unit="%" d={2} base={baseRes.demag_keep_pct ?? 100} goodHi />
              )}
              {result.saturation_pct != null && (
                <MetricTile label={tx('configure.saturationKoef')} value={result.saturation_pct} unit="%" d={1} base={baseRes.saturation_pct ?? 100} goodHi />
              )}
              {result.demag_keep_pct != null && result.saturation_pct != null && (
                <MetricTile label={tx('configure.totalKoef')} value={result.demag_keep_pct * result.saturation_pct / 100} unit="%" d={1}
                  base={(baseRes.demag_keep_pct ?? 100) * (baseRes.saturation_pct ?? 100) / 100} goodHi
                  tip={tx('configure.totalKoefTip')} />
              )}
            </Box>
          )}
          {/* NO 3D provenance line for clients (user's call 2026-08-24): every
              published reference ships WITH its measured Stage A correction
              baked into the numbers — the kitchen stays in the kitchen.  The
              k_end3d field remains in ScaledResult for admin tooling, and
              future test-bench correction factors will ride the same way. */}

          {/* The "wire coating (winding stack)" gauge was removed (user
              2026-09-02): the cross-section below shows the stack, the two
              winding sliders stop at the slot, and an over-limit design says
              "wire outside the stator" under them. */}

          {/* The wire-current banner is gone (user 2026-08-26: "we can already see
              Curr. density") — the current-density tile already says it, and
              the banner fired even when the current merely EQUALLED the cap.
              The cap still colours the current slider red. */}

          <Button onClick={addConfig} variant="contained" startIcon={<AddIcon />}
            sx={{ textTransform: 'none', fontWeight: 700, bgcolor: '#1d4ed8', '&:hover': { bgcolor: '#2563eb' }, alignSelf: 'flex-start' }}>
            {tx('configure.addToComparison')}
          </Button>
        </Box>
      </Box>

      {/* ── COMPARISON — ABOVE the geometry (user 2026-08-25): the client's
          one and only comparison view; the Compare tab is the engineer's. ── */}
      <Box sx={{ px: 2, pb: 1.5 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.75 }}>
          <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-0)' }}>{tx('configure.saved')}</Typography>
          <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{tx('configure.savedHint', { n: configs.length })}</Typography>
          <Box sx={{ flex: 1 }} />
          {configs.length > 0 && (
            <Button onClick={() => setConfigs([])} size="small" sx={{ fontSize: 11, textTransform: 'none', color: '#7f1d1d' }}>{tx('configure.clearAll')}</Button>
          )}
        </Box>
        {configs.length === 0 ? (
          <Alert severity="info" sx={{ fontSize: 12 }}>{tx('configure.savedEmpty', { button: tx('configure.addToComparison') })}</Alert>
        ) : (
          <Box sx={{ overflow: 'auto' }}>
            <Box component="table" sx={{ borderCollapse: 'collapse', width: '100%' }}>
              <Box component="thead"><Box component="tr">
                <Box component="th" sx={{ ...TH, textAlign: 'left' }}>{tx('configure.colConfiguration')}</Box>
                {KNB_COLS.map((k) => <Box component="th" key={k.label} sx={{ ...TH, color: '#fbbf24' }}><GreekLabel text={k.label} /></Box>)}
                {RES_COLS.map((r) => <Box component="th" key={r.key} sx={{ ...TH, color: '#4ade80' }}><GreekLabel text={r.label} />{r.unit ? <Box component="span" sx={{ color: 'var(--line)', fontWeight: 400, textTransform: 'none' }}> {r.unit}</Box> : null}</Box>)}
                <Box component="th" sx={{ ...TH, textAlign: 'center' }} />
                <Box component="th" sx={{ ...TH, textAlign: 'center' }}>✕</Box>
              </Box></Box>
              <Box component="tbody">
                {configs.map((c) => (
                  <Box component="tr" key={c.id} sx={{ '&:hover': { bgcolor: 'var(--panel-2)' } }}>
                    <Box component="td" sx={{ ...TD, textAlign: 'left', fontFamily: 'inherit', whiteSpace: 'nowrap' }}>
                      <Box component="span" onClick={() => loadConfig(c)} title={tx('configure.applyTip')}
                        sx={{ color: '#60a5fa', fontWeight: 600, cursor: 'pointer', '&:hover': { textDecoration: 'underline' } }}>{c.name}</Box>
                      <IconButton size="small" onClick={() => renameConfig(c)} title={tx('configure.rename')}
                        sx={{ color: 'var(--text-3)', p: 0.2, ml: 0.5, fontSize: 12 }}>✎</IconButton>
                    </Box>
                    {KNB_COLS.map((k) => <Box component="td" key={k.label} sx={{ ...TD, color: '#fbbf24' }}>{k.get(c)}</Box>)}
                    {RES_COLS.map((r) => {
                      const v = r.get(c);
                      let col = 'var(--text-1)';
                      const e = resExt[r.key];
                      if (e && e.min !== e.max && r.goodHi !== undefined) {
                        const best = r.goodHi ? e.max : e.min;
                        const worst = r.goodHi ? e.min : e.max;
                        if (Math.abs(v - best) < 1e-9) col = '#4ade80';
                        else if (Math.abs(v - worst) < 1e-9) col = '#f87171';
                      }
                      return <Box component="td" key={r.key} sx={{ ...TD, color: col, fontWeight: col !== 'var(--text-1)' ? 700 : 400 }}>{fmt(v, r.d)}</Box>;
                    })}
                    {/* Explicit apply (user's ask) — same action as clicking
                        the name, but discoverable. */}
                    <Box component="td" sx={{ ...TD, textAlign: 'center' }}>
                      <Button size="small" onClick={() => loadConfig(c)}
                        sx={{ fontSize: 10.5, py: 0, px: 0.9, minWidth: 0, textTransform: 'none',
                              color: '#34d399', border: '1px solid #34d39955' }}>
                        {tx('configure.apply')}
                      </Button>
                    </Box>
                    <Box component="td" sx={{ ...TD, textAlign: 'center' }}>
                      <IconButton size="small" onClick={() => delConfig(c.id)} sx={{ color: 'var(--text-3)', p: 0.25, '&:hover': { color: '#f87171' } }}><DeleteOutlineIcon sx={{ fontSize: 15 }} /></IconButton>
                    </Box>
                  </Box>
                ))}
              </Box>
            </Box>
          </Box>
        )}
      </Box>

      {/* ── BOOST CHARGING — generators only, and only when the machine has a
          pack.  A motor's Configure tab is unchanged.  UNDER the geometry
          (user 2026-09-02): the cross-section must stay in view while the
          winding knobs are turned, the charge map reads below it. ── */}
      {canCharge(p) ? (
        <Box sx={{ px: 2, pb: 1.5 }}>
          <ChargePanel p={p} knobs={scaleKnobs} poles={ref.poles} result={result}
            onPickCurrent={(I) => setKnobs((s) => ({ ...s, I_A: I }))} />
        </Box>
      ) : String(p.role ?? p.mode0 ?? '').toLowerCase() === 'generator' && !p.battery ? (
        <Box sx={{ px: 2, pb: 1.5 }}>
          <Typography sx={{ fontSize: 11, color: '#fbbf24' }}
            title={tx('configure.generatorNeedsPackTip')}>
            {tx('configure.generatorNeedsPack')}
          </Typography>
        </Box>
      ) : null}

      {/* ── THERMAL block: only for a machine that is NOT cooled by its propeller alone — there the
          cooling is the propeller, and the temperatures are one row in the tiles above ── */}
      {ctxDone && !cooled && (
        <Box sx={{ px: 2, pb: 1.5 }}>
          <ConfiguratorThermal
            geom={thermalGeom}
            losses={{ P_cu_W: result.P_cu_W, P_fe_W: result.P_fe_W, P_mag_W: result.P_mag_W }}
          />
        </Box>
      )}

      {/* ── PERFORMANCE VS SPEED ── */}
      {SHOW_CONFIGURE_CHARTS && (
        <Box sx={{ px: 2, pb: 1.5 }}>
          <PerformanceCharts p={p} knobs={scaleKnobs} packMin={battery.cells * battery.min} packMax={battery.cells * battery.max} />
        </Box>
      )}
      </>
      )}

      <TextPromptDialog state={askName} onClose={() => setAskName(null)} />
    </Box>
  );
};

export default ConfiguratorPanel;
