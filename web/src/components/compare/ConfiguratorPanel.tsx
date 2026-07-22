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
  IconButton, Select, MenuItem, Alert, LinearProgress,
} from '@mui/material';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import AddIcon from '@mui/icons-material/Add';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import BoltIcon from '@mui/icons-material/Bolt';
import {
  scaleMotor, maxCurrent, type Passport, type Knobs, type ScaledResult,
} from '../../lib/motorScaling';
import {
  REFERENCE_PASSPORTS, windingConnections, connLabel, fetchCatalogReferences, type ReferenceMotor,
} from '../../lib/referencePassports';
import GeometryProjections from './GeometryProjections';
import BatteryPanel, { type Battery, defaultBattery } from './BatteryPanel';
import { useAuth } from '../../contexts/AuthContext';
import PerformanceCharts from './PerformanceCharts';
import ConfiguratorThermal from './ConfiguratorThermal';

const baseKnobs = (p: Passport): Knobs => ({
  N: p.N0, L_mm: p.L0_mm, wireH_mm: p.wireH0_mm, nP: p.nP0, I_A: p.I0_A, rpm: p.rpm0,
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
}> = ({ label, unit, value, base, min, max, step, d = 1, onChange, onRangeChange, warn }) => {
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
}> = ({ label, value, unit, d = 1, base, goodHi }) => {
  const delta = pctDelta(value, base);
  const show = Math.abs(delta) >= 0.5;
  const good = goodHi === undefined ? null : goodHi ? delta > 0 : delta < 0;
  const dColor = good === null ? 'var(--text-2)' : good ? '#4ade80' : '#f87171';
  return (
    <Box sx={{ ...PANEL, p: 1.25, flex: '1 1 0', minWidth: 108 }}>
      <Typography sx={LABEL}>{label}</Typography>
      <Typography sx={{ fontSize: 20, fontWeight: 800, color: 'var(--text-0)', fontFamily: 'monospace', lineHeight: 1.25 }}>
        {fmt(value, d)}<Box component="span" sx={{ fontSize: 12, color: 'var(--text-3)', ml: 0.5 }}>{unit}</Box>
      </Typography>
      {show && (
        <Typography sx={{ fontSize: 11, color: dColor, fontFamily: 'monospace' }}>
          {delta > 0 ? '+' : ''}{fmt(delta, 1)}% vs ref
        </Typography>
      )}
    </Box>
  );
};

const ConfiguratorPanel: React.FC = () => {
  const { isAdmin } = useAuth();   // editing the slider ranges is admin-only
  // FEM-characterised catalog motors (fetched) come first; the built-in
  // REFERENCE_PASSPORTS stay as a seed/fallback.
  const [catalogRefs, setCatalogRefs] = useState<ReferenceMotor[]>([]);
  useEffect(() => { fetchCatalogReferences().then((rs) => { if (rs.length) setCatalogRefs(rs); }).catch(() => {}); }, []);
  const allRefs = useMemo(() => [...catalogRefs, ...REFERENCE_PASSPORTS], [catalogRefs]);
  const [refId, setRefId] = useState<string>(() => {
    try { const r = localStorage.getItem(REFID_LS); if (r) return r; } catch { /* ignore */ }
    return REFERENCE_PASSPORTS[0]?.id ?? '';
  });
  useEffect(() => { try { localStorage.setItem(REFID_LS, refId); } catch { /* ignore */ } }, [refId]);
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
  // user-editable slider ranges (persisted, per parameter)
  const [ranges, setRanges] = useState<Record<KnobKey, KRange>>(() => {
    try { const r = localStorage.getItem(RANGES_LS); if (r) return { ...DEFAULT_RANGES, ...JSON.parse(r) }; } catch { /* ignore */ }
    return DEFAULT_RANGES;
  });
  useEffect(() => { try { localStorage.setItem(RANGES_LS, JSON.stringify(ranges)); } catch { /* ignore */ } }, [ranges]);
  const setRange = (k: KnobKey) => (min: number, max: number) => {
    setRanges((s) => ({ ...s, [k]: { min, max } }));
    setKnobs((s) => ({ ...s, [k]: Math.min(max, Math.max(min, (s as Record<string, number>)[k])) }));
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
    setKnobs(baseKnobs(ref.passport));
  }, [refId]); // eslint-disable-line react-hooks/exhaustive-deps

  // battery the user runs the motor from (persisted; snapshotted into each saved config)
  const [battery, setBattery] = useState<Battery>(() => {
    try { const r = localStorage.getItem('configurator.battery.v1'); if (r) { const b = JSON.parse(r); if (b?.type) return b; } } catch { /* ignore */ }
    return defaultBattery();
  });
  useEffect(() => { try { localStorage.setItem('configurator.battery.v1', JSON.stringify(battery)); } catch { /* ignore */ } }, [battery]);

  const [configs, setConfigs] = useState<SavedConfig[]>(() => {
    try { const r = localStorage.getItem(LS_KEY); const a = r ? JSON.parse(r) : null; return Array.isArray(a) ? a : []; }
    catch { return []; }
  });
  useEffect(() => { try { localStorage.setItem(LS_KEY, JSON.stringify(configs)); } catch { /* ignore */ } }, [configs]);

  const result  = useMemo(() => scaleMotor(p, knobs), [p, knobs]);
  const baseRes = useMemo(() => scaleMotor(p, baseKnobs(p)), [p]);
  const iMax    = useMemo(() => maxCurrent(p, knobs), [p, knobs]);
  // Current density in the conductor (A/mm², RMS) = coil current / wire area;
  // coil current = phase current / parallel paths, wire area = width × height.
  const J_A_mm2 = (knobs.I_A / Math.max(1, knobs.nP)) / Math.max(1e-6, ref.fit.wireWidth_mm * knobs.wireH_mm);
  const baseJ   = (p.I0_A / Math.max(1, p.nP0)) / Math.max(1e-6, ref.fit.wireWidth_mm * p.wireH0_mm);
  // winding connections are derived from THIS reference's slot count (C = slots/6)
  const conns = useMemo(() => windingConnections(ref.geo.numSlots), [ref.geo.numSlots]);
  useEffect(() => {
    if (conns.length && !conns.some((c) => c.nP === knobs.nP)) setKnobs((s) => ({ ...s, nP: conns[0].nP }));
  }, [conns]); // eslint-disable-line react-hooks/exhaustive-deps
  const overCurr = knobs.I_A > iMax + 1e-6;
  // Hard slot-fit limiter — mirrors the backend constraint
  // (geometry_constraints._wire_height_max): N rows of (wire_height + radial
  // spacing) must fit between the two insulation layers.  Each slider's max is
  // derived from the OTHER knob's current value, so the winding can never
  // overflow the slot (which would push coils across the air gap).
  const availStack_mm  = ref.fit.slotHeight_mm - 2 * ref.fit.insulation_mm;
  const rowPitch_mm    = knobs.wireH_mm + ref.fit.wireSpacingY_mm;   // one wire row + its radial gap
  const stackHeight_mm = knobs.N * rowPitch_mm;
  const stackFrac = stackHeight_mm / availStack_mm;
  const overFit   = stackHeight_mm > availStack_mm + 1e-9;
  const turnsMax  = Math.max(3, Math.min(30, Math.floor(availStack_mm / rowPitch_mm)));
  const wireMax   = Math.max(0.3, Math.min(2.5, Math.floor((availStack_mm / knobs.N - ref.fit.wireSpacingY_mm) / 0.1) * 0.1));
  const atLimit   = knobs.N >= turnsMax || knobs.wireH_mm >= wireMax - 1e-9;

  const set = (k: keyof Knobs) => (v: number) => setKnobs((s) => ({ ...s, [k]: v }));
  const reset = () => setKnobs(baseKnobs(p));

  const addConfig = () => {
    const n = configs.filter((c) => c.refId === refId).length + 1;
    const name = `${connLabel(knobs.nP, ref.geo.numSlots)} · ${knobs.N}t · ${fmt(knobs.L_mm, 0)}mm · ${fmt(knobs.wireH_mm, 2)}mm (#${n})`;
    const id = `cfg_${Math.random().toString(36).slice(2, 9)}`;
    setConfigs((cs) => [...cs, { id, name, refId, knobs: { ...knobs }, result, iMax, battery: { ...battery } }]);
  };
  const delConfig = (id: string) => setConfigs((cs) => cs.filter((c) => c.id !== id));
  // load a saved config back as the current design — knobs + battery (+ reference)
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

  const fitColor = overFit ? '#f87171' : atLimit ? '#fbbf24' : '#4ade80';

  return (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', bgcolor: 'var(--panel-2)', overflow: 'auto' }}>
      {/* header: reference picker */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, px: 2, py: 1.25, borderBottom: '1px solid var(--line-soft)' }}>
        <BoltIcon sx={{ color: '#60a5fa', fontSize: 20 }} />
        <Typography sx={{ fontSize: 14, fontWeight: 800, color: 'var(--text-0)' }}>Configurator</Typography>
        <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>instant — no simulation</Typography>
        <Box sx={{ flex: 1 }} />
        <Typography sx={LABEL}>Reference</Typography>
        <Select value={refId} onChange={(e) => setRefId(e.target.value)} size="small"
          sx={{ minWidth: 260, fontSize: 13, color: 'var(--text-0)', bgcolor: 'var(--panel-2)', '& .MuiOutlinedInput-notchedOutline': { borderColor: 'var(--line)' } }}>
          {allRefs.map((r) => (
            <MenuItem key={r.id} value={r.id} sx={{ fontSize: 13 }}>{r.name}</MenuItem>
          ))}
        </Select>
      </Box>

      <Box sx={{ display: 'flex', gap: 2, p: 2, flexWrap: 'wrap' }}>
        {/* ── KNOBS ── */}
        <Box sx={{ ...PANEL, p: 2, flex: '1 1 360px', minWidth: 320 }}>
          <Typography sx={{ fontSize: 12, fontWeight: 800, color: 'var(--text-1)', mb: 0.25 }}>{ref.name}</Typography>
          <Typography sx={{ fontSize: 11, color: 'var(--text-3)', mb: 1.5 }}>{ref.subtitle}</Typography>

          <Typography sx={{ ...LABEL, color: 'var(--text-4)', mb: 0.75 }}>Build</Typography>
          <KnobSlider label="Stack length" unit="mm" value={knobs.L_mm} base={p.L0_mm} min={ranges.L_mm.min} max={ranges.L_mm.max} step={1} d={0} onChange={set('L_mm')} onRangeChange={isAdmin ? setRange('L_mm') : undefined} />
          <KnobSlider label="Turns / slot" value={knobs.N} base={p.N0} min={ranges.N.min} max={ranges.N.max} step={1} d={0} onChange={set('N')} onRangeChange={isAdmin ? setRange('N') : undefined} warn={atLimit} />
          <KnobSlider label="Wire thickness" unit="mm" value={knobs.wireH_mm} base={p.wireH0_mm} min={ranges.wireH_mm.min} max={ranges.wireH_mm.max} step={0.1} d={1} onChange={set('wireH_mm')} onRangeChange={isAdmin ? setRange('wireH_mm') : undefined} warn={atLimit} />

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
          <KnobSlider label="Phase current" unit="A" value={knobs.I_A} base={p.I0_A} min={ranges.I_A.min} max={ranges.I_A.max} step={1} d={0} onChange={set('I_A')} onRangeChange={isAdmin ? setRange('I_A') : undefined} warn={overCurr} />
          <KnobSlider label="Speed" unit="rpm" value={knobs.rpm} base={p.rpm0} min={ranges.rpm.min} max={ranges.rpm.max} step={50} d={0} onChange={set('rpm')} onRangeChange={isAdmin ? setRange('rpm') : undefined} />

          <Button onClick={reset} size="small" startIcon={<RestartAltIcon sx={{ fontSize: 16 }} />}
            sx={{ fontSize: 11, textTransform: 'none', color: 'var(--text-2)', mt: 1 }}>Reset to reference</Button>
        </Box>

        {/* ── RESULT ── */}
        <Box sx={{ flex: '2 1 460px', minWidth: 360, display: 'flex', flexDirection: 'column', gap: 1.25 }}>
          <Box sx={{ display: 'flex', gap: 1.25, flexWrap: 'wrap' }}>
            <MetricTile label="Torque" value={result.T_Nm} unit="N·m" d={1} base={baseRes.T_Nm} goodHi />
            <MetricTile label="Power" value={result.P_mech_W / 1000} unit="kW" d={2} base={baseRes.P_mech_W / 1000} goodHi />
            <MetricTile label="DC bus (min)" value={result.Vphase_peak_V * Math.sqrt(3)} unit="V" d={0} base={baseRes.Vphase_peak_V * Math.sqrt(3)} />
            <MetricTile label="Efficiency" value={result.efficiency * 100} unit="%" d={1} base={baseRes.efficiency * 100} goodHi />
          </Box>
          <Box sx={{ display: 'flex', gap: 1.25, flexWrap: 'wrap' }}>
            <MetricTile label="Curr. density" value={J_A_mm2} unit="A/mm²" d={1} base={baseJ} goodHi={false} />
            <MetricTile label="Total loss" value={result.P_loss_W} unit="W" d={0} base={baseRes.P_loss_W} goodHi={false} />
            <MetricTile label="Mass" value={result.mass_kg} unit="kg" d={2} base={baseRes.mass_kg} goodHi={false} />
            <MetricTile label="T / mass" value={result.torque_per_mass} unit="N·m/kg" d={2} base={baseRes.torque_per_mass} goodHi />
            <MetricTile label="Resistance" value={result.R_ohm * 1000} unit="mΩ" d={1} base={baseRes.R_ohm * 1000} />
          </Box>

          {/* slot-fit gauge */}
          <Box sx={{ ...PANEL, p: 1.25 }}>
            <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, mb: 0.5 }}>
              <Typography sx={{ ...LABEL, flex: 1 }}>Slot fill (winding stack)</Typography>
              <Typography sx={{ fontSize: 12, color: 'var(--text-3)', fontFamily: 'monospace' }}>
                {fmt(stackHeight_mm, 1)} / {fmt(availStack_mm, 1)} mm
              </Typography>
              <Typography sx={{ fontSize: 13, fontWeight: 700, color: fitColor, fontFamily: 'monospace' }}>{fmt(stackFrac * 100, 0)}%</Typography>
            </Box>
            <LinearProgress variant="determinate" value={Math.min(100, stackFrac * 100)}
              sx={{ height: 8, borderRadius: 1, bgcolor: 'var(--app-bg)', '& .MuiLinearProgress-bar': { bgcolor: fitColor } }} />
            <Typography sx={{ fontSize: 11, color: atLimit ? '#fbbf24' : 'var(--text-3)', mt: 0.5 }}>
              {atLimit
                ? `At slot limit — turns × wire capped so the winding fits the slot.`
                : `${knobs.N} rows × (${fmt(knobs.wireH_mm, 2)} + ${fmt(ref.fit.wireSpacingY_mm, 2)} gap) mm; usable = ${fmt(ref.fit.slotHeight_mm, 1)} − 2×${fmt(ref.fit.insulation_mm, 2)} insulation = ${fmt(availStack_mm, 1)} mm.`}
            </Typography>
          </Box>

          {/* wire current cap */}
          <Alert severity={overCurr ? 'error' : 'info'} icon={false}
            sx={{ py: 0.25, fontSize: 12, bgcolor: overCurr ? '#3f1d1d' : 'var(--panel-2)', color: overCurr ? '#fecaca' : 'var(--text-2)', border: '1px solid', borderColor: overCurr ? '#7f1d1d' : 'var(--panel)' }}>
            Wire current limit ≈ <b>{fmt(iMax, 0)} A</b> (scales with wire thickness × parallel paths).
            {overCurr && ` Current ${fmt(knobs.I_A, 0)} A exceeds it — thicken wire or use more parallel paths.`}
          </Alert>

          <Button onClick={addConfig} variant="contained" startIcon={<AddIcon />}
            sx={{ textTransform: 'none', fontWeight: 700, bgcolor: '#1d4ed8', '&:hover': { bgcolor: '#2563eb' }, alignSelf: 'flex-start' }}>
            Add to comparison
          </Button>
        </Box>
      </Box>

      {/* ── GEOMETRY PROJECTIONS (before battery: see geometry change as you tune) ── */}
      <Box sx={{ px: 2, pb: 1.5 }}>
        <GeometryProjections ref0={ref} knobs={knobs} />
      </Box>

      {/* ── BATTERY & VOLTAGE MATCH ── */}
      <Box sx={{ px: 2, pb: 1.5 }}>
        <BatteryPanel vDc={result.Vphase_peak_V * Math.sqrt(3)} bat={battery} onChange={setBattery} />
      </Box>

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

      {/* ── COMPARISON ── */}
      <Box sx={{ px: 2, pb: 2 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.75 }}>
          <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-0)' }}>Saved configurations</Typography>
          <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>({configs.length}) — green = best · red = worst · click a row to load</Typography>
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
                <Box component="th" sx={{ ...TH, textAlign: 'center' }}>✕</Box>
              </Box></Box>
              <Box component="tbody">
                {configs.map((c) => (
                  <Box component="tr" key={c.id} sx={{ '&:hover': { bgcolor: 'var(--panel-2)' } }}>
                    <Box component="td" onClick={() => loadConfig(c)} title="Load this configuration as the current design (knobs + battery)"
                      sx={{ ...TD, textAlign: 'left', color: '#60a5fa', fontFamily: 'inherit', fontWeight: 600, cursor: 'pointer', '&:hover': { textDecoration: 'underline' } }}>{c.name}</Box>
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
    </Box>
  );
};

export default ConfiguratorPanel;
