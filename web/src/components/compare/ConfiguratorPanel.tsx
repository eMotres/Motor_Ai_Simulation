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
  IconButton, Alert,
} from '@mui/material';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import AddIcon from '@mui/icons-material/Add';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import BoltIcon from '@mui/icons-material/Bolt';
import { useMotorStore } from '../../stores/motorStore';
import { TextPromptDialog, type TextPromptState } from '../common/PromptDialogs';
import { pickCable } from '../../lib/cableTable';
import {
  scaleMotor, maxCurrent, type Passport, type Knobs, type ScaledResult,
} from '../../lib/motorScaling';
import {
  REFERENCE_PASSPORTS, windingConnections, connLabel, fetchCatalogReferences, type ReferenceMotor,
} from '../../lib/referencePassports';
import GeometryProjections from './GeometryProjections';
import BatteryPanel, { type Battery, defaultBattery, PRESETS } from './BatteryPanel';
import { useAuth } from '../../contexts/AuthContext';
import PerformanceCharts from './PerformanceCharts';
import ConfiguratorThermal from './ConfiguratorThermal';
import ChargePanel from './ChargePanel';
import { canCharge } from '../../lib/generatorCharge';

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
}

const LS_KEY = 'configurator.configs.v1';
const fmt = (v: number, d = 1) => (Number.isFinite(v) ? v.toFixed(d) : '—');
const pctDelta = (cur: number, base: number) => (base ? ((cur - base) / base) * 100 : 0);

// theme bits (match ComparePanel)
const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1 } as const;
const LABEL = { fontSize: 11, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.03em' } as const;
const TH = { px: 1.25, py: 0.7, fontSize: 10, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.03em', whiteSpace: 'nowrap', textAlign: 'right', borderBottom: '1px solid var(--line-soft)', bgcolor: 'var(--panel-2)' } as const;
const TD = { px: 1.25, py: 0.5, fontSize: 12, whiteSpace: 'nowrap', textAlign: 'right', borderBottom: '1px solid var(--app-bg)', fontFamily: 'monospace', color: 'var(--text-1)' } as const;

// ── user-editable slider ranges (persisted) ──────────────────────────────────
type KnobKey = 'L_mm' | 'N' | 'wireH_mm' | 'I_A' | 'rpm';
interface KRange { min: number; max: number; }
const DEFAULT_RANGES: Record<KnobKey, KRange> = {
  L_mm:     { min: 15,  max: 150 },
  N:        { min: 3,   max: 24 },
  wireH_mm: { min: 0.3, max: 2.0 },
  I_A:      { min: 0,   max: 300 },
  rpm:      { min: 0,   max: 8000 },
};
const RANGES_LS = 'configurator.ranges.v1';
const KNOBS_LS  = 'configurator.knobs.v1';
const REFID_LS  = 'configurator.refId.v1';

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
}> = ({ label, unit, value, base, min, max, step, d = 1, onChange, onRangeChange, warn, sub }) => {
  const delta = pctDelta(value, base);
  const [txt, setTxt] = React.useState<string | null>(null);   // non-null while the field is being typed in
  return (
    <Box sx={{ mb: 1.25 }}>
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 0.75, mb: 0.25 }}>
        <Typography sx={{ ...LABEL, flex: 1 }}>{label}</Typography>
        <input value={txt ?? fmt(value, d)} type="number" step={step}
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
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
        {onRangeChange && <RangeEnd value={min} d={d} title="Range min — editable" onCommit={(v) => onRangeChange(Math.min(v, max - step), max)} />}
        <Slider value={value} min={min} max={max} step={step}
          onChange={(_, v) => onChange(v as number)} size="small"
          sx={{ flex: 1, color: warn ? '#f87171' : '#3b82f6', py: 0.5, '& .MuiSlider-thumb': { width: 13, height: 13 } }} />
        {onRangeChange && <RangeEnd value={max} d={d} title="Range max — editable" onCommit={(v) => onRangeChange(min, Math.max(v, min + step))} />}
      </Box>
    </Box>
  );
};

// ── one result tile: value + unit + Δ vs reference ──
const MetricTile: React.FC<{
  label: string; value: number; unit: string; d?: number; base: number; goodHi?: boolean;
  /** ABSOLUTE colouring for quantities that have a meaning of their own
   *  (current density): 'ok' | 'warn' | 'bad' overrides the vs-reference
   *  colour, because 9 A/mm² is fine whether or not it grew (user
   *  2026-08-26: "почему ток красным подсвечивается?"). */
  absLevel?: 'ok' | 'warn' | 'bad';
  /** What the number IS, when the label cannot say it (the heat split's
   *  terms).  Prepended to the vs-reference line in the hover title. */
  tip?: string;
}> = ({ label, value, unit, d = 1, base, goodHi, absLevel, tip }) => {
  const delta = pctDelta(value, base);
  // No "% vs ref" line under every tile (user 2026-08-26) — the deltas are
  // carried by COLOUR only; the header's Reset button returns to the
  // reference design.
  const show = false;
  const good = goodHi === undefined ? null : goodHi ? delta > 0 : delta < 0;
  const dColor = good === null ? 'var(--text-2)' : good ? '#4ade80' : '#f87171';
  void show; void dColor;
  // Compact (user 2026-08-25 "слишком размазано"): fixed narrow tiles in a
  // dense wrap — the same visual weight as the Simulation summary cells.
  // The tile shows the VALUE; how it moved against the reference design is
  // told by the value's colour and by the tooltip (green = better, red =
  // worse, grey = neutral quantity).
  const changed = Math.abs(delta) >= 0.5;
  return (
    <Box sx={{ ...PANEL, p: 0.9, flex: '0 1 auto', minWidth: 108, maxWidth: 168 }}
      title={(tip ? `${tip}  ` : '') + (changed
        ? `${delta > 0 ? '+' : ''}${fmt(delta, 1)} % vs the reference design (${fmt(base, d)} ${unit})`
        : 'same as the reference design')}>
      <Typography sx={{ ...LABEL, fontSize: 9.5, whiteSpace: 'nowrap',
        overflow: 'hidden', textOverflow: 'ellipsis' }}>{label}</Typography>
      <Typography sx={{ fontSize: 16, fontWeight: 800,
        color: absLevel
          ? (absLevel === 'bad' ? '#f87171' : absLevel === 'warn' ? '#fbbf24' : '#4ade80')
          : (changed && good !== null ? dColor : 'var(--text-0)'),
        fontFamily: 'monospace', lineHeight: 1.2, whiteSpace: 'nowrap' }}>
        {fmt(value, d)}<Box component="span" sx={{ fontSize: 10.5,
          color: 'var(--text-3)', ml: 0.5 }}>{unit}</Box>
      </Typography>
    </Box>
  );
};

const ConfiguratorPanel: React.FC = () => {
  const { isAdmin } = useAuth();   // editing the slider ranges is admin-only
  // FEM-characterised catalog motors (fetched) come first; the built-in
  // REFERENCE_PASSPORTS stay as a seed/fallback.
  const [catalogRefs, setCatalogRefs] = useState<ReferenceMotor[]>([]);
  // Retry until the catalog answers, and refetch on catalog changes — a
  // single failed fetch (server restart window) left the panel with ONLY the
  // built-in 200 mm reference forever, so no loaded motor could ever match
  // (user 2026-08-25: "опять 200 mm").  Same illness as the Motors-tab
  // canWrite freeze, same cure.
  useEffect(() => {
    let dead = false;
    const load = () => fetchCatalogReferences()
      .then((rs) => {
        if (dead) return;
        if (rs.length) setCatalogRefs(rs);
        else setTimeout(load, 3000);
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
  useEffect(() => {
    const pick = () => {
      const g = useMotorStore.getState().geometry as Record<string, any> | null;
      if (!g) return;
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
      // wrong (user 2026-08-26: "загружаю мотор — получаю другие данные").
      const dist = (r: ReferenceMotor) => {
        const p0 = r.passport;
        const rel = (a: number, b: number) =>
          (a > 0 && b > 0) ? Math.abs(Math.log(a / b)) : 5;
        return rel(p0.L0_mm, Number(g.motor_length))
             + rel(p0.N0, Number(g.num_wires_per_slot))
             + rel(p0.wireH0_mm, Number(g.wire_height));
      };
      const m = sameSection.length
        ? sameSection.slice().sort((a, b) => dist(a) - dist(b))[0]
        : undefined;
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
        const pp = (m ?? allRefs.find((r) => r.id === refId) ?? allRefs[0])?.passport;
        const adopt = (k0: Knobs): Knobs => ({
          N: live.N || k0.N,
          split: live.split,
          L_mm: live.L_mm || k0.L_mm,
          wireH_mm: live.wireH_mm || k0.wireH_mm,
          nP: live.nP || k0.nP,
          I_A: Number.isFinite(live.I_A) && live.I_A > 0 ? live.I_A : k0.I_A,
          rpm: Number.isFinite(live.rpm) && live.rpm > 0 ? live.rpm : k0.rpm,
        });
        setKnobs((k0) => { const k1 = adopt(k0); setRefKnobs(k1); return k1; });
        if (pp) {
          // Ranges must contain BOTH the passport base and the loaded build.
          const r0 = rangesForRef(pp);
          setRanges({
            L_mm: { min: Math.min(r0.L_mm.min, live.L_mm * 0.3), max: Math.max(r0.L_mm.max, live.L_mm * 2) },
            N: { min: Math.min(r0.N.min, Math.max(1, Math.round(live.N * 0.3))), max: Math.max(r0.N.max, Math.ceil(live.N * 2)) },
            wireH_mm: { min: Math.min(r0.wireH_mm.min, live.wireH_mm * 0.3), max: Math.max(r0.wireH_mm.max, live.wireH_mm * 2) },
            I_A: { min: 0, max: Math.max(r0.I_A.max, (Number.isFinite(live.I_A) ? live.I_A : 0) * 1.5) },
            rpm: { min: 0, max: Math.max(r0.rpm.max, (Number.isFinite(live.rpm) ? live.rpm : 0) * 1.5) },
          });
        }
      }
    };
    window.addEventListener('sim-operating-point', pick);
    pick();   // also on mount / after the references arrive
    return () => window.removeEventListener('sim-operating-point', pick);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allRefs]);
  void liveGeo;
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
  // Slider ranges FOLLOW THE MACHINE (user 2026-08-25: loading the 850 N·m
  // motor left the 40 mm ranges — its 205 mm stack and 400 A sat outside the
  // sliders and the fields showed the previous motor's values).  Derived from
  // the passport's own base point, then user-editable per parameter.
  const rangesForRef = React.useCallback((pp: Passport): Record<KnobKey, KRange> => {
    const r2 = (v: number, lo: number, hi: number) => ({
      min: Math.max(0, Number((v * lo).toPrecision(2))),
      max: Number((v * hi).toPrecision(2)),
    });
    return {
      L_mm:     r2(pp.L0_mm, 0.3, 3.0),
      N:        { min: Math.max(1, Math.round(pp.N0 * 0.3)), max: Math.ceil(pp.N0 * 2.0) },
      wireH_mm: r2(pp.wireH0_mm, 0.3, 2.5),
      I_A:      { min: 0, max: Number((pp.I0_A * 2.0).toPrecision(2)) },
      rpm:      { min: 0, max: Number((pp.rpm0 * 2.0).toPrecision(2)) },
    };
  }, []);
  const [ranges, setRanges] = useState<Record<KnobKey, KRange>>(() => {
    try { const r = localStorage.getItem(RANGES_LS); if (r) return { ...DEFAULT_RANGES, ...JSON.parse(r) }; } catch { /* ignore */ }
    return DEFAULT_RANGES;
  });
  useEffect(() => { try { localStorage.setItem(RANGES_LS, JSON.stringify(ranges)); } catch { /* ignore */ } }, [ranges]);
  const setRange = (k: KnobKey) => (min: number, max: number) => {
    setRanges((s) => ({ ...s, [k]: { min, max } }));
    setKnobs((s) => ({ ...s, [k]: Math.min(max, Math.max(min, (s as unknown as Record<string, number>)[k])) }));
  };
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
    { const kb = baseKnobs(ref.passport); setKnobs(kb); setRefKnobs(kb); }
    setRanges(rangesForRef(ref.passport));
  }, [refId]); // eslint-disable-line react-hooks/exhaustive-deps
  // First paint after the references arrive: if the stored knobs/ranges belong
  // to another machine (they are persisted globally), adopt this one's.
  const rangedFor = React.useRef<string>('');
  useEffect(() => {
    if (!ref || rangedFor.current === refId) return;
    rangedFor.current = refId;
    const p0 = ref.passport;
    const off = (a: number, b: number) => !(b > 0) || Math.abs(a - b) / b > 1.5;
    if (off(knobs.L_mm, p0.L0_mm) || off(knobs.I_A, p0.I0_A)
        || knobs.I_A > ranges.I_A.max || knobs.L_mm > ranges.L_mm.max) {
      { const kb = baseKnobs(p0); setKnobs(kb); setRefKnobs(kb); }
      setRanges(rangesForRef(p0));
    }
  }, [refId, ref]); // eslint-disable-line react-hooks/exhaustive-deps

  // battery the user runs the motor from (persisted; snapshotted into each saved config)
  const [battery, setBattery] = useState<Battery>(() => {
    try { const r = localStorage.getItem('configurator.battery.v1'); if (r) { const b = JSON.parse(r); if (b?.type) return b; } } catch { /* ignore */ }
    return defaultBattery();
  });
  useEffect(() => { try { localStorage.setItem('configurator.battery.v1', JSON.stringify(battery)); } catch { /* ignore */ } }, [battery]);
  // A machine that carries its own pack (family configuration → passport
  // `battery`) seeds the voltage-match panel with THAT pack when it is loaded
  // — the user 2026-09-02 saw a 370 V default beside a 750 V charging card.
  // Once per machine; edits afterwards are the user's and stay.
  const packSeededFor = React.useRef<string>('');
  useEffect(() => {
    const b = p.battery;
    if (!b || packSeededFor.current === refId) return;
    packSeededFor.current = refId;
    const ns = Math.round(Number(b.cells ?? 0));
    const vNom = Number(b.v_nom), vMax = Number(b.v_max), vMin = Number(b.v_min);
    if (!(ns > 0) || !(vNom > 0)) return;
    const lfp = /lfp|lifepo|iron/i.test(String(b.chemistry ?? ''));
    const preset = lfp ? PRESETS.LFP : PRESETS.NMC;
    const seeded: Battery = {
      type: lfp ? 'LFP' : 'NMC', cells: ns,
      nom: vNom / ns,
      max: vMax > 0 ? vMax / ns : preset.max,
      min: vMin > 0 ? vMin / ns : preset.min,
    };
    setBattery((cur) => (cur.cells === seeded.cells && cur.type === seeded.type
      && Math.abs(cur.nom - seeded.nom) < 1e-6 && Math.abs(cur.max - seeded.max) < 1e-6
      && Math.abs(cur.min - seeded.min) < 1e-6) ? cur : seeded);
  }, [refId, p.battery]); // eslint-disable-line react-hooks/exhaustive-deps

  const [configs, setConfigs] = useState<SavedConfig[]>(() => {
    try { const r = localStorage.getItem(LS_KEY); const a = r ? JSON.parse(r) : null; return Array.isArray(a) ? a : []; }
    catch { return []; }
  });
  useEffect(() => { try { localStorage.setItem(LS_KEY, JSON.stringify(configs)); } catch { /* ignore */ } }, [configs]);

  const result  = useMemo(() => scaleMotor(p, knobs, ref.poles), [p, knobs, ref.poles]);
  // "vs ref" compares against THE MACHINE AS LOADED, not against the
  // passport's calibration point (user 2026-08-26: a freshly loaded motor
  // showed −92.7 % with nothing touched — it was being compared to another
  // configuration's base build).  refKnobs is set when a machine is adopted;
  // it falls back to the passport base when nothing is loaded.
  const baseRes = useMemo(() => scaleMotor(p, refKnobs ?? baseKnobs(p), ref.poles),
                          [p, ref.poles, refKnobs]);
  const iMax    = useMemo(() => maxCurrent(p, knobs), [p, knobs]);
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
    const tags = [kPar > 1 ? `${kPar} in hand` : '',
                  kSplit > 1 ? `${kSplit} strips in series` : ''].filter(Boolean);
    return tags.length ? `Wire rows / slot (${tags.join(', ')})` : 'Turns / slot';
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
  const overFit   = stackHeight_mm > availStack_mm + 1e-9;
  const turnsMax  = Math.max(3, Math.min(30, Math.floor(availStack_mm / rowPitch_mm)));
  const wireMax   = Math.max(0.3, Math.min(2.5, Math.round(Math.floor((availStack_mm / knobs.N - ref.fit.wireSpacingY_mm) / 0.1 + 1e-9) * 0.1 * 10) / 10));
  const atLimit   = knobs.N >= turnsMax || knobs.wireH_mm >= wireMax - 1e-9;
  // The two winding sliders STOP at the slot (user 2026-09-02: the stack gauge
  // is gone — the cross-section shows the stack, the slider just must not let
  // the wire leave the stator).  A typed value clamps to the same cap; only a
  // saved configuration or a machine change can still arrive over the limit,
  // and that is what the red line under the sliders is for.
  const turnsSliderMax = Math.max(ranges.N.min, Math.min(ranges.N.max, turnsMax));
  const wireSliderMax  = Math.max(ranges.wireH_mm.min, Math.min(ranges.wireH_mm.max, wireMax));

  const set = (k: keyof Knobs) => (v: number) => setKnobs((s) => ({ ...s, [k]: v }));
  // Reset goes back to the machine AS LOADED (the same point the deltas are
  // measured from), falling back to the passport base when nothing is loaded.
  const reset = () => setKnobs(refKnobs ?? baseKnobs(p));
  /** Has the user moved anything off the reference design? */
  const tuned = (() => {
    const r0 = refKnobs ?? baseKnobs(p);
    return (['N', 'L_mm', 'wireH_mm', 'nP', 'I_A', 'rpm'] as const)
      .some((kk) => Math.abs(Number(knobs[kk]) - Number(r0[kk]))
                    > 1e-6 * Math.max(1, Math.abs(Number(r0[kk]))));
  })();

  const addConfig = () => {
    const n = configs.filter((c) => c.refId === refId).length + 1;
    const name = `${connLabel(knobs.nP, ref.geo.numSlots)} · ${knobs.N}t · ${fmt(knobs.L_mm, 0)}mm · ${fmt(knobs.wireH_mm, 2)}mm (#${n})`;
    const id = `cfg_${Math.random().toString(36).slice(2, 9)}`;
    setConfigs((cs) => [...cs, { id, name, refId, knobs: { ...knobs }, result, iMax, battery: { ...battery } }]);
  };
  const delConfig = (id: string) => setConfigs((cs) => cs.filter((c) => c.id !== id));
  // load a saved config back as the current design — knobs + battery (+ reference)
  // Rename a saved configuration (user 2026-08-25) — the auto name is only a
  // starting point.
  const [askName, setAskName] = useState<TextPromptState | null>(null);
  const renameConfig = (c: SavedConfig) => setAskName({
    title: 'Rename configuration',
    label: 'Name', initial: c.name,
    onSubmit: (name) => {
      const n = name.trim();
      if (n) setConfigs((cs) => cs.map((x) => (x.id === c.id ? { ...x, name: n } : x)));
    },
  });

  const loadConfig = (c: SavedConfig) => {
    if (c.refId !== refId) { skipReset.current = true; setRefId(c.refId); }
    setKnobs({ ...c.knobs });
    if (c.battery) setBattery({ ...c.battery });
  };

  const RES_COLS: { key: string; label: string; unit: string; d: number; goodHi?: boolean; get: (c: SavedConfig) => number }[] = [
    { key: 'T',    label: 'Torque',  unit: 'N·m', d: 1, goodHi: true,  get: (c) => c.result.T_Nm },
    { key: 'P',    label: 'Power',   unit: 'kW',  d: 2, goodHi: true,  get: (c) => c.result.P_mech_W / 1000 },
    { key: 'V',    label: 'DC bus',  unit: 'V',   d: 0,                get: (c) => c.result.Vphase_peak_V * Math.sqrt(3) },
    { key: 'eff',  label: 'η',       unit: '%',   d: 1, goodHi: true,  get: (c) => c.result.efficiency * 100 },
    { key: 'loss', label: 'Losses',  unit: 'W',   d: 0, goodHi: false, get: (c) => c.result.P_loss_W },
    { key: 'J',    label: 'J',       unit: 'A/mm²', d: 1, goodHi: false, get: (c) => (c.knobs.I_A / Math.max(1, c.knobs.nP)) / Math.max(1e-6, ref.fit.wireWidth_mm * c.knobs.wireH_mm) },
    { key: 'mass', label: 'Mass',    unit: 'kg',  d: 2, goodHi: false, get: (c) => c.result.mass_kg },
    { key: 'tm',   label: 'T/mass',  unit: '',    d: 2, goodHi: true,  get: (c) => c.result.torque_per_mass },
  ];
  const KNB_COLS: { label: string; get: (c: SavedConfig) => string }[] = [
    { label: 'Conn',   get: (c) => connLabel(c.knobs.nP, (allRefs.find((r) => r.id === c.refId)?.geo.numSlots ?? ref.geo.numSlots)) },
    { label: 'Turns',  get: (c) => `${c.knobs.N}` },
    { label: 'Length', get: (c) => fmt(c.knobs.L_mm, 0) },
    { label: 'Wire h', get: (c) => fmt(c.knobs.wireH_mm, 2) },
    { label: 'I',      get: (c) => fmt(c.knobs.I_A, 0) },
    { label: 'rpm',    get: (c) => fmt(c.knobs.rpm, 0) },
  ];
  // best/worst per result column across saved configs (for highlight)
  const resExt: Record<string, { min: number; max: number } | null> = {};
  RES_COLS.forEach((r) => {
    const ns = configs.map(r.get).filter(Number.isFinite);
    resExt[r.key] = ns.length ? { min: Math.min(...ns), max: Math.max(...ns) } : null;
  });


  return (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', bgcolor: 'var(--panel-2)', overflow: 'auto' }}>
      {/* Header — NO reference picker (user 2026-08-25 "выкинь это меню"):
          the Configurator always mirrors the LOADED motor; the name shown is
          the matched passport's. */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, px: 2, py: 1.25, borderBottom: '1px solid var(--line-soft)' }}>
        <BoltIcon sx={{ color: '#60a5fa', fontSize: 20 }} />
        <Typography sx={{ fontSize: 14, fontWeight: 800, color: 'var(--text-0)' }}>Configurator</Typography>
        <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>instant — no simulation</Typography>
        <Box sx={{ flex: 1 }} />
        <Typography sx={{ fontSize: 12, fontWeight: 700, color: liveMatched ? 'var(--text-1)' : '#f59e0b' }}>
          {liveMatched ? ref.name : `${ref.name} (last matched — the loaded motor has no passport yet)`}
        </Typography>
        {/* One clear way back to the reference design (user 2026-08-26) —
            replaces the per-tile "% vs ref" captions. */}
        <Button size="small" variant={tuned ? 'contained' : 'outlined'} onClick={reset}
          startIcon={<RestartAltIcon sx={{ fontSize: 15 }} />}
          disabled={!tuned}
          title="Put every knob back to the reference design (the motor as loaded)"
          sx={{ ml: 1.5, textTransform: 'none', fontSize: 11, py: 0.1,
                ...(tuned ? { bgcolor: '#1d4ed8', '&:hover': { bgcolor: '#2563eb' } } : {}) }}>
          {tuned ? 'Reset to reference' : 'reference design'}
        </Button>
      </Box>

      <Box sx={{ display: 'flex', gap: 2, p: 2, flexWrap: 'wrap' }}>
        {/* ── KNOBS ── */}
        <Box sx={{ ...PANEL, p: 2, flex: '1 1 360px', minWidth: 320 }}>
          <Typography sx={{ fontSize: 12, fontWeight: 800, color: 'var(--text-1)', mb: 0.25 }}>{ref.name}</Typography>
          <Typography sx={{ fontSize: 11, color: 'var(--text-3)', mb: 1.5 }}>{ref.subtitle}</Typography>

          <Typography sx={{ ...LABEL, color: 'var(--text-4)', mb: 0.75 }}>Build</Typography>
          <KnobSlider label="Stack length" unit="mm" value={knobs.L_mm} base={p.L0_mm} min={ranges.L_mm.min} max={ranges.L_mm.max} step={1} d={0} onChange={set('L_mm')} onRangeChange={isAdmin ? setRange('L_mm') : undefined} />
          <KnobSlider label={rowsLabel} value={knobs.N} base={p.N0} min={ranges.N.min} max={turnsSliderMax} step={1} d={0} onChange={set('N')} onRangeChange={isAdmin ? setRange('N') : undefined} warn={overFit || badTurns} />
          <KnobSlider label="Wire thickness" unit="mm" value={knobs.wireH_mm} base={p.wireH0_mm} min={ranges.wireH_mm.min} max={wireSliderMax} step={0.1} d={1} onChange={set('wireH_mm')} onRangeChange={isAdmin ? setRange('wireH_mm') : undefined} warn={overFit} />
          {overFit ? (
            <Typography sx={{ fontSize: 11, color: '#f87171', mt: -0.5, mb: 1 }}
              title={`${knobs.N} rows × (${fmt(knobs.wireH_mm, 2)} wire + ${fmt(ref.fit.wireSpacingY_mm, 2)} gap) = ${fmt(stackHeight_mm, 1)} mm; the slot leaves ${fmt(ref.fit.slotHeight_mm, 1)} − 2×${fmt(ref.fit.insulation_mm, 2)} insulation = ${fmt(availStack_mm, 1)} mm. Lower the turns or the wire thickness.`}>
              ⚠ Wire outside the stator — {fmt(stackHeight_mm, 1)} mm stack in a {fmt(availStack_mm, 1)} mm slot
            </Typography>
          ) : badTurns ? (
            <Typography sx={{ fontSize: 11, color: '#f87171', mt: -0.5, mb: 1 }}
              title={`This build is wound ${kPar} wires in hand, so the slot's wire count must be a multiple of ${kPar}: ${knobs.N} wires would make ${(knobs.N / kPar).toFixed(2)} turns per coil, which is not a winding. Use ${kPar * Math.floor(knobs.N / kPar)} or ${kPar * (Math.floor(knobs.N / kPar) + 1)}.`}>
              ⚠ {knobs.N} wires is not a whole number of {kPar}-in-hand turns
            </Typography>
          ) : atLimit ? (
            <Typography sx={{ fontSize: 11, color: 'var(--text-4)', mt: -0.5, mb: 1 }}
              title={`${knobs.N} rows × (${fmt(knobs.wireH_mm, 2)} wire + ${fmt(ref.fit.wireSpacingY_mm, 2)} gap) = ${fmt(stackHeight_mm, 1)} mm of ${fmt(availStack_mm, 1)} mm usable slot height — the sliders stop here so the winding stays inside the stator.`}>
              at the slot limit
            </Typography>
          ) : null}

          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 0.5, mb: 1 }}>
            <Typography sx={{ ...LABEL, flex: 1 }}>Winding connection</Typography>
            <ToggleButtonGroup exclusive size="small" value={knobs.nP} onChange={(_, v) => v != null && set('nP')(v)}>
              {conns.map((c) => (
                <ToggleButton key={c.nP} value={c.nP} title={c.hint}
                  sx={{ px: 1.5, py: 0.25, fontSize: 12, color: 'var(--text-2)', borderColor: 'var(--line)',
                    '&.Mui-selected': { bgcolor: '#1d4ed8', color: '#fff', '&:hover': { bgcolor: '#2563eb' } } }}>
                  {c.label}
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
          </Box>

          <Typography sx={{ ...LABEL, color: 'var(--text-4)', mt: 1.5, mb: 0.75 }}>Operating point</Typography>
          {/* rms is the knob; the PEAK rides beside it (user 2026-08-26) —
              inverters and datasheets are quoted in peak, the coil sees rms. */}
          <KnobSlider label="Phase current (rms)" unit="A" value={knobs.I_A} base={p.I0_A}
            min={ranges.I_A.min} max={ranges.I_A.max} step={1} d={0}
            sub={`= ${fmt(knobs.I_A * Math.SQRT2, 0)} A peak`}
            onChange={set('I_A')} onRangeChange={isAdmin ? setRange('I_A') : undefined} warn={overCurr} />
          <KnobSlider label="Speed" unit="rpm" value={knobs.rpm} base={p.rpm0} min={ranges.rpm.min} max={ranges.rpm.max} step={50} d={0} onChange={set('rpm')} onRangeChange={isAdmin ? setRange('rpm') : undefined} />

          {/* ── EXCITATION ──────────────────────────────────────────────
              Only when the passport carries MEASURED PWM deltas.  A toggle
              backed by an assumption would be worse than no toggle. */}
          {p.pwm && p.pwm.points?.length ? (
            <>
              <Typography sx={{ ...LABEL, color: 'var(--text-4)', mt: 1.5, mb: 0.75 }}>Excitation</Typography>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1, flexWrap: 'wrap' }}>
                <ToggleButtonGroup exclusive size="small" value={knobs.pwm ? 'pwm' : 'sine'}
                  onChange={(_, v) => v != null && setKnobs((s) => ({ ...s, pwm: v === 'pwm' }))}>
                  <ToggleButton value="sine" title="Ideal sinusoidal current — the passport's own measurement"
                    sx={{ px: 1.5, py: 0.25, fontSize: 12, color: 'var(--text-2)', borderColor: 'var(--line)', '&.Mui-selected': { bgcolor: '#1d4ed8', color: '#fff', '&:hover': { bgcolor: '#2563eb' } } }}>Sine</ToggleButton>
                  <ToggleButton value="pwm" title={`Add the measured carrier deltas (${p.pwm.controller_class}, measured at ${p.pwm.f_sw_Hz.map((x) => (x / 1000).toFixed(0)).join(' / ')} kHz on a ${p.pwm.v_bus_V.toFixed(0)} V bus)`}
                    sx={{ px: 1.5, py: 0.25, fontSize: 12, color: 'var(--text-2)', borderColor: 'var(--line)', '&.Mui-selected': { bgcolor: '#1d4ed8', color: '#fff', '&:hover': { bgcolor: '#2563eb' } } }}>PWM</ToggleButton>
                </ToggleButtonGroup>
                {knobs.pwm && (
                  <>
                    <Box component="span" sx={{ fontSize: 11, color: 'var(--text-3)' }}>carrier</Box>
                    <select value={String(knobs.f_sw_Hz ?? p.pwm.f_sw_ref_Hz)}
                      onChange={(e) => setKnobs((s) => ({ ...s, f_sw_Hz: Number(e.target.value) }))}
                      title={`${p.pwm.controller_class} — the settings this power stage offers. Carriers outside the measured pair are extrapolated and flagged.`}
                      style={{ background: 'transparent', border: '1px solid var(--line)', borderRadius: 4, color: 'var(--text-0)', fontSize: 12, fontFamily: 'monospace', padding: '2px 4px' }}>
                      {(p.pwm.f_sw_class_Hz ?? p.pwm.f_sw_Hz).map((f) => (
                        <option key={f} value={f} style={{ color: '#000' }}>
                          {(f / 1000).toFixed(0)} kHz{p.pwm!.f_sw_Hz.includes(f) ? ' ·measured' : ''}
                        </option>
                      ))}
                    </select>
                    <Box component="span" sx={{ fontSize: 11, color: 'var(--text-3)' }}>bus</Box>
                    <input type="number" step={1}
                      value={String(knobs.v_bus_V ?? p.pwm.v_bus_V)}
                      onChange={(e) => { const v = parseFloat(e.target.value); if (Number.isFinite(v) && v > 0) setKnobs((s) => ({ ...s, v_bus_V: v })); }}
                      title="DC link the inverter switches against — the ripple current is proportional to it. Defaults to the pack the block was measured on."
                      style={{ width: 62, background: 'transparent', border: '1px solid var(--line)', borderRadius: 4, color: 'var(--text-0)', fontSize: 12, fontFamily: 'monospace', textAlign: 'right', padding: '1px 4px' }} />
                    <Box component="span" sx={{ fontSize: 11, color: 'var(--text-3)' }}>V</Box>
                  </>
                )}
              </Box>
              {knobs.pwm && result.pwm_fidelity && (
                <Typography sx={{ fontSize: 10.5, color: result.pwm_extrapolated ? '#fbbf24' : 'var(--text-4)', mb: 0.5 }}
                  title={result.pwm_note ?? ''}>
                  {result.pwm_extrapolated ? '⚠ ' : ''}{result.pwm_fidelity}
                </Typography>
              )}
            </>
          ) : null}

          <Button onClick={reset} size="small" disabled={!tuned}
            startIcon={<RestartAltIcon sx={{ fontSize: 16 }} />}
            title="Put every knob back to the reference design (the motor as loaded)"
            sx={{ fontSize: 11, textTransform: 'none',
                  color: tuned ? '#60a5fa' : 'var(--text-3)', mt: 1 }}>
            Reset to reference design
          </Button>
        </Box>

        {/* ── RESULT ── */}
        <Box sx={{ flex: '2 1 460px', minWidth: 360, display: 'flex', flexDirection: 'column', gap: 1.25 }}>
          {/* SEVEN ROWS — the same order as the Simulation summary card
              (user 2026-09-04: "одинаково как для simulation так и configure"):
              1 torque · power · mass · efficiency · ripple · densities
              2 total loss · iron · copper · magnet · stator/rotor heat · loss density
              3 voltages + current density (unchanged)
              4 phase section · wire coating · lead cable · R phase · R line-line
              5 Ld · Lq · ψ_PM · Lq/Ld
              6 KV · Kt · Km · Km/mass
              7 demag koef · saturation koef · total koef */}
          <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
            <MetricTile label="Torque" value={result.T_Nm} unit="N·m" d={1} base={baseRes.T_Nm} goodHi />
            <MetricTile label="Power" value={result.P_mech_W / 1000} unit="kW" d={2} base={baseRes.P_mech_W / 1000} goodHi />
            <MetricTile label="Mass" value={result.mass_kg} unit="kg" d={2} base={baseRes.mass_kg} goodHi={false} />
            <MetricTile label="Efficiency" value={result.efficiency * 100} unit="%" d={1} base={baseRes.efficiency * 100} goodHi />
            {result.pwm_on && result.pwm_ripple_pct != null ? (
              <MetricTile label="T ripple (PWM)" value={result.pwm_ripple_pct} unit="%" d={1}
                base={ref.passport.ripple0_pct ?? result.pwm_ripple_pct} goodHi={false} />
            ) : ref.passport.ripple0_pct != null && (
              <MetricTile label="T ripple (rated)" value={ref.passport.ripple0_pct} unit="%" d={1}
                base={ref.passport.ripple0_pct} goodHi={false} />
            )}
            <MetricTile label="T / mass" value={result.torque_per_mass} unit="N·m/kg" d={2} base={baseRes.torque_per_mass} goodHi />
            <MetricTile label="P / mass" value={result.power_per_mass_W_kg / 1000} unit="kW/kg" d={2} base={baseRes.power_per_mass_W_kg / 1000} goodHi />
          </Box>
          <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
            <MetricTile label="Total loss" value={result.P_loss_W} unit="W" d={0} base={baseRes.P_loss_W} goodHi={false} />
            <MetricTile label="Iron loss" value={result.P_fe_W} unit="W" d={0} base={baseRes.P_fe_W} goodHi={false} />
            <MetricTile label="Copper loss" value={result.P_cu_W} unit="W" d={0} base={baseRes.P_cu_W} goodHi={false} />
            <MetricTile label="Magnet loss" value={result.P_mag_W} unit="W" d={0} base={baseRes.P_mag_W} goodHi={false} />
            {/* ── HEAT TO REMOVE, per side — what the cooling is sized on ── */}
            <MetricTile label="Stator heat" value={result.P_loss_stator_W} unit="W" d={0}
              base={baseRes.P_loss_stator_W} goodHi={false}
              tip={'Stator iron + all copper. The scaled iron loss is split by the '
                + 'ratio the passport measured at its base point'
                + (result.loss_split_measured ? '.'
                   : ' — iron split unknown here, so the WHOLE iron loss is on the '
                     + 'stator (regenerate the passport).')} />
            <MetricTile label="Rotor heat" value={result.P_loss_rotor_W} unit="W" d={0}
              base={baseRes.P_loss_rotor_W} goodHi={false}
              absLevel={result.loss_split_measured ? undefined : 'warn'}
              tip={'Rotor iron + magnet/solid loss — it can only leave across the air '
                + 'gap or through the shaft. Stator + rotor = the Total loss tile'
                + (result.loss_split_measured ? '.'
                   : '; the rotor IRON share is unknown here and sits on the stator '
                     + 'side (regenerate the passport).')} />
            <MetricTile label="Loss density" value={result.loss_density_W_kg} unit="W/kg" d={0} base={baseRes.loss_density_W_kg} goodHi={false} />
          </Box>
          {/* ── PWM deltas — the watts the carrier ADDS, shown separately so
              the sine machine stays readable underneath them ── */}
          {result.pwm_on && (
            <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
              <MetricTile label="Δ magnet (PWM)" value={result.pwm_dP_mag_W ?? 0} unit="W" d={1}
                base={result.pwm_dP_mag_W ?? 0} />
              <MetricTile label="Δ iron (PWM)" value={result.pwm_dP_fe_W ?? 0} unit="W" d={1}
                base={result.pwm_dP_fe_W ?? 0} />
              <MetricTile label="Δ copper AC (PWM)" value={result.pwm_dP_cu_ac_W ?? 0} unit="W" d={1}
                base={result.pwm_dP_cu_ac_W ?? 0} />
              <MetricTile label="Ripple current" value={result.pwm_I_ripple_A ?? 0} unit="A" d={2}
                base={result.pwm_I_ripple_A ?? 0}
                absLevel={result.pwm_extrapolated ? 'warn' : undefined} />
              <MetricTile label="DC link ripple" value={result.pwm_I_dc_ripple_A ?? 0} unit="A p-p" d={1}
                base={result.pwm_I_dc_ripple_A ?? 0} />
            </Box>
          )}
          <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
            <MetricTile label="DC bus (min)" value={result.Vphase_peak_V * Math.sqrt(3)} unit="V" d={0} base={baseRes.Vphase_peak_V * Math.sqrt(3)} />
            <MetricTile label="V line peak" value={result.Vline_peak_V} unit="V" d={1} base={baseRes.Vline_peak_V} />
            <MetricTile label="V phase peak" value={result.Vphase_peak_V} unit="V" d={1} base={baseRes.Vphase_peak_V} />
            <MetricTile label="V line rms" value={result.Vline_rms_V} unit="V" d={1} base={baseRes.Vline_rms_V} />
            <MetricTile label="V phase rms" value={result.Vphase_rms_V} unit="V" d={1} base={baseRes.Vphase_rms_V} />
            {/* Absolute thresholds, not "vs reference": ≤12 A/mm² is a
                continuous-duty winding, ≤25 a short-peak one, above that the
                copper cooks whatever the reference did. */}
            <MetricTile label="Curr. density" value={J_A_mm2} unit="A/mm²" d={1} base={baseJ}
              absLevel={J_A_mm2 <= 12 ? 'ok' : J_A_mm2 <= 25 ? 'warn' : 'bad'} />
          </Box>
          <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
            {/* Phase copper section (user 2026-08-26) — the same quantity the
                Simulation card shows: strand area × parallel paths, so
                I_phase / A_phase is exactly the density above it. */}
            <MetricTile label="Phase section" value={A_phase_mm2} unit="mm²" d={2}
              base={baseA_phase} goodHi />
            {/* Wire coating (user 2026-08-30) — measured copper over the measured
                winding window; turns and wire height move it, so the tuner can
                say when a variant stops being windable.  Absolute thresholds:
                hand-wound rectangular wire lives at ~45-60 %. */}
            {result.slot_fill_pct != null && (
              <MetricTile label="Fill factor" value={result.slot_fill_pct} unit="%" d={1}
                base={p.slot_fill0_pct ?? result.slot_fill_pct}
                absLevel={result.slot_fill_pct <= 60 ? 'ok'
                          : result.slot_fill_pct <= 75 ? 'warn' : 'bad'} />
            )}
            {/* Nearest lead cable for that section (user 2026-08-26): gauge
                number + bare conductor diameter. */}
            {(() => {
              const c = pickCable(A_phase_mm2);
              return c ? (
                <Box sx={{ ...PANEL, p: 0.9, flex: '0 1 auto', minWidth: 108, maxWidth: 168 }}
                  title={`Catalogue silicone lead (${c.strands}): ${c.area_mm2} mm² copper — the first size at or above the phase section ${A_phase_mm2.toFixed(2)} mm². Conductor Ø${c.d_mm} mm, insulation O.D. Ø${c.od_mm}±0.1 mm (wall ${c.thk_mm} mm), ${c.r_ohm_km} Ω/km, ${c.i_rated_A} A continuous / ${c.i_max_A} A peak, ${c.roll_m} m per roll.`
                    + (c.suspect ? ` ⚠ supplier sheet: ${c.suspect}.` : '')}>
                  <Typography sx={{ ...LABEL, fontSize: 9.5 }}>Lead cable</Typography>
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
            <MetricTile label="R phase" value={result.R_ohm * 1000} unit="mΩ" d={1} base={baseRes.R_ohm * 1000}
              tip="Phase resistance at the coil temperature, end-winding included — the R the copper loss is billed from." />
            <MetricTile label="R line-line" value={result.R_ohm * 2000} unit="mΩ" d={1} base={baseRes.R_ohm * 2000}
              tip="2 × R phase — what an ohmmeter across two leads of the isolated-neutral star reads." />
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
                <MetricTile label="Lq / Ld" value={result.Lq_mH / result.Ld_mH} unit="" d={2}
                  base={(baseRes.Ld_mH ?? result.Ld_mH) > 0
                    ? (baseRes.Lq_mH ?? result.Lq_mH) / (baseRes.Ld_mH ?? result.Ld_mH)
                    : result.Lq_mH / result.Ld_mH}
                  tip="Saliency ratio of the two dq inductances." />
              )}
            </Box>
          )}
          <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
            <MetricTile label="KV (no-load)" value={result.KV_rpm_per_Vline} unit="rpm/V" d={1} base={baseRes.KV_rpm_per_Vline} />
            {result.Kt_Nm_per_A != null && (
              <MetricTile label="Kt" value={result.Kt_Nm_per_A} unit="N·m/A" d={3} base={baseRes.Kt_Nm_per_A ?? result.Kt_Nm_per_A} goodHi />
            )}
            <MetricTile label="Km" value={result.Km_Nm_sqrtW} unit="N·m/√W" d={3} base={baseRes.Km_Nm_sqrtW} goodHi />
            <MetricTile label="Km / mass" value={result.Km_per_mass} unit="N·m/(√W·kg)" d={3} base={baseRes.Km_per_mass} goodHi />
          </Box>
          {(result.demag_keep_pct != null || result.saturation_pct != null) && (
            <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
              {result.demag_keep_pct != null && (
                <MetricTile label="Demag koef" value={result.demag_keep_pct} unit="%" d={2} base={baseRes.demag_keep_pct ?? 100} goodHi />
              )}
              {result.saturation_pct != null && (
                <MetricTile label="Saturation koef" value={result.saturation_pct} unit="%" d={1} base={baseRes.saturation_pct ?? 100} goodHi />
              )}
              {result.demag_keep_pct != null && result.saturation_pct != null && (
                <MetricTile label="Total koef" value={result.demag_keep_pct * result.saturation_pct / 100} unit="%" d={1}
                  base={(baseRes.demag_keep_pct ?? 100) * (baseRes.saturation_pct ?? 100) / 100} goodHi
                  tip="Demag koef × Saturation koef — torque retained against the ideal machine (fresh magnets, linear iron)." />
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

          {/* The wire-current banner is gone (user 2026-08-26: "мы же видим
              Curr. density") — the current-density tile already says it, and
              the banner fired even when the current merely EQUALLED the cap.
              The cap still colours the current slider red. */}

          <Button onClick={addConfig} variant="contained" startIcon={<AddIcon />}
            sx={{ textTransform: 'none', fontWeight: 700, bgcolor: '#1d4ed8', '&:hover': { bgcolor: '#2563eb' }, alignSelf: 'flex-start' }}>
            Add to comparison
          </Button>
        </Box>
      </Box>

      {/* ── COMPARISON — ABOVE the geometry (user 2026-08-25): the client's
          one and only comparison view; the Compare tab is the engineer's. ── */}
      <Box sx={{ px: 2, pb: 1.5 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.75 }}>
          <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-0)' }}>Saved configurations</Typography>
          <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>({configs.length}) — green = best · red = worst</Typography>
          <Box sx={{ flex: 1 }} />
          {configs.length > 0 && (
            <Button onClick={() => setConfigs([])} size="small" sx={{ fontSize: 11, textTransform: 'none', color: '#7f1d1d' }}>Clear all</Button>
          )}
        </Box>
        {configs.length === 0 ? (
          <Alert severity="info" sx={{ fontSize: 12 }}>Tune the knobs above and press <b>Add to comparison</b> to stack configs here.</Alert>
        ) : (
          <Box sx={{ overflow: 'auto' }}>
            <Box component="table" sx={{ borderCollapse: 'collapse', width: '100%' }}>
              <Box component="thead"><Box component="tr">
                <Box component="th" sx={{ ...TH, textAlign: 'left' }}>Configuration</Box>
                {KNB_COLS.map((k) => <Box component="th" key={k.label} sx={{ ...TH, color: '#fbbf24' }}>{k.label}</Box>)}
                {RES_COLS.map((r) => <Box component="th" key={r.key} sx={{ ...TH, color: '#4ade80' }}>{r.label}{r.unit ? <Box component="span" sx={{ color: 'var(--line)', fontWeight: 400 }}> {r.unit}</Box> : null}</Box>)}
                <Box component="th" sx={{ ...TH, textAlign: 'center' }} />
                <Box component="th" sx={{ ...TH, textAlign: 'center' }}>✕</Box>
              </Box></Box>
              <Box component="tbody">
                {configs.map((c) => (
                  <Box component="tr" key={c.id} sx={{ '&:hover': { bgcolor: 'var(--panel-2)' } }}>
                    <Box component="td" sx={{ ...TD, textAlign: 'left', fontFamily: 'inherit', whiteSpace: 'nowrap' }}>
                      <Box component="span" onClick={() => loadConfig(c)} title="Apply this configuration (knobs + battery)"
                        sx={{ color: '#60a5fa', fontWeight: 600, cursor: 'pointer', '&:hover': { textDecoration: 'underline' } }}>{c.name}</Box>
                      <IconButton size="small" onClick={() => renameConfig(c)} title="Rename"
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
                        apply
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

      {/* ── GEOMETRY (left, compact) + BATTERY (right) — one row (user
          2026-08-25: "геометрию влево, батарею справа, покомпактнее"). ── */}
      <Box sx={{ px: 2, pb: 1.5, display: 'flex', gap: 1.5, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        <Box sx={{ flex: '0 1 auto', minWidth: 340 }}>
          <GeometryProjections ref0={ref} knobs={knobs} />
        </Box>
        <Box sx={{ flex: '1 1 320px', minWidth: 300 }}>
          <BatteryPanel vDc={result.Vphase_peak_V * Math.sqrt(3)} bat={battery} onChange={setBattery} />
        </Box>
      </Box>

      {/* ── BOOST CHARGING — generators only, and only when the machine has a
          pack.  A motor's Configure tab is unchanged.  UNDER the geometry
          (user 2026-09-02): the cross-section must stay in view while the
          winding knobs are turned, the charge map reads below it. ── */}
      {canCharge(p) ? (
        <Box sx={{ px: 2, pb: 1.5 }}>
          <ChargePanel p={p} knobs={knobs} poles={ref.poles} result={result}
            onPickCurrent={(I) => setKnobs((s) => ({ ...s, I_A: I }))} />
        </Box>
      ) : String(p.role ?? p.mode0 ?? '').toLowerCase() === 'generator' && !p.battery ? (
        <Box sx={{ px: 2, pb: 1.5 }}>
          <Typography sx={{ fontSize: 11, color: '#fbbf24' }}
            title="Charging is computed against the pack as a CIRCUIT — its internal resistance, capacity and charge ceiling — and those live on the family configuration. This passport was generated before the pack rode along with it; regenerate it and the charging block appears.">
            Generator — charging needs this machine's pack; regenerate the passport to pick it up
          </Typography>
        </Box>
      ) : null}

      {/* ── THERMAL (analytical estimate, same cooling inputs as Simulation) ── */}
      <Box sx={{ px: 2, pb: 1.5 }}>
        <ConfiguratorThermal
          geom={{
            statorOD_mm: ref.geo.statorOR_mm * 2,
            stackLength_mm: knobs.L_mm,
            numSlots: ref.geo.numSlots,
            slotHeight_mm: ref.fit.slotHeight_mm,
            slotWidth_mm: ref.fit.slotWidth_mm,
            insulation_mm: ref.fit.insulation_mm,
            coreThickness_mm: Math.max(0, ref.geo.statorOR_mm - ref.geo.statorIR_mm - ref.fit.slotHeight_mm),
            airGap_mm: Math.max(0, ref.geo.statorIR_mm - ref.geo.rotorOR_mm),
            magnetOD_mm: ref.geo.rotorOR_mm * 2,
          }}
          losses={{ P_cu_W: result.P_cu_W, P_fe_W: result.P_fe_W, P_mag_W: result.P_mag_W }}
        />
      </Box>

      {/* ── PERFORMANCE VS SPEED ── */}
      <Box sx={{ px: 2, pb: 1.5 }}>
        <PerformanceCharts p={p} knobs={knobs} packMin={battery.cells * battery.min} packMax={battery.cells * battery.max} />
      </Box>

      <TextPromptDialog state={askName} onClose={() => setAskName(null)} />
    </Box>
  );
};

export default ConfiguratorPanel;
