// ─────────────────────────────────────────────────────────────────────────────
// Sweep study — a GRID sweep plotted as a performance map: efficiency vs
// torque/mass (N·m/kg).  Variables + ranges come from the SHARED cards above
// (the single variable interface): current_a / gamma_deg → operating points,
// geometry vars → a geometry grid.  Points are connected with a line along a
// variable you choose (current or γ).  A variable NOT added above stays fixed at
// its Simulation value.  Runs the backend grid-scan (POST /api/optimization/scan)
// and streams the points live.  Resumes a running scan on mount (reload-safe).
// ─────────────────────────────────────────────────────────────────────────────
import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Box, Typography, Button, LinearProgress, Divider, TextField,
  FormControl, InputLabel, Select, MenuItem,
} from '@mui/material';
import {
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';
import { useMotorStore } from '../../stores/motorStore';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

function useLS<T>(key: string, def: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => {
    try { const r = localStorage.getItem(`sweepStudy.${key}`); return r == null ? def : (JSON.parse(r) as T); }
    catch { return def; }
  });
  return [v, (nv: T) => { setV(nv); try { localStorage.setItem(`sweepStudy.${key}`, JSON.stringify(nv)); } catch { /* ignore */ } }];
}

const buildGrid = (lo: number, hi: number, step: number): number[] => {
  if (!(step > 0) || hi < lo) return [Math.round(lo * 1e4) / 1e4];
  const out: number[] = [];
  for (let v = lo; v <= hi + step * 1e-6; v += step) out.push(Math.round(v * 1e4) / 1e4);
  return out;
};
const readLS = (key: string, def: number): number => {
  try { return Number(JSON.parse(localStorage.getItem(key) ?? String(def))) || def; } catch { return def; }
};
const readBool = (key: string, def: boolean): boolean => {
  try { return JSON.parse(localStorage.getItem(key) ?? String(def)) === true; } catch { return def; }
};

const GCOL = ['#22c55e', '#3b82f6', '#a855f7', '#f59e0b', '#ef4444', '#14b8a6', '#ec4899', '#eab308', '#60a5fa', '#f97316'];
const OP_VARS = new Set(['current_a', 'gamma_deg']);

// ── Excel-like sortable table of every swept design ──────────────────────────
// Leading (blue) columns = the variables that actually varied in the sweep
// (geometry overrides + operating point); the rest are FEM outputs. Every metric
// already rides on each point (the backend spreads the full eval result). Click
// any header to sort asc/desc. Losses map to the Ansys breakdown:
// core = P_fe, stranded = P_cu (winding), solid = P_mag + P_shaft (eddy in solids).
interface SweepTableCol { id: string; label: string; get: (r: any) => number; fmt: (v: number) => string; vcol?: boolean }
const SweepTable: React.FC<{ points: any[]; rpm: number; vdcFactor?: number; selectedPk?: string; onPick?: (r: any) => void }> =
  ({ points, rpm, vdcFactor, selectedPk, onPick }) => {
  const vdcF = vdcFactor || (1 / Math.sqrt(3));   // V_dc = V_peak / mod_factor (SVPWM → ×√3)
  const omega = 2 * Math.PI * (rpm || 4000) / 60;
  const rows = useMemo(() => (points || [])
    .filter(p => p && p.feasible && p.T_em_Nm != null)
    .map(p => {
      const P = (Number(p.T_em_Nm) || 0) * omega / 1000;          // kW
      const mass = Number(p.mass_total_kg) || 0;
      const td = Number(p.torque_per_mass_Nm_kg) || 0;
      const I = Number(p.current_a) || 0, g = Number(p.gamma_deg) || 0;
      const eff = (Number(p.efficiency) || 0) * 100;
      return {
        ov: p.overrides || {}, overrides: p.overrides || {}, I, g, x: td, y: eff,
        T: Number(p.T_em_Nm) || 0, P, eff, Vpk: Number(p.V_peak) || 0,
        ripple: Number(p.T_ripple_pct) || 0, mass, td,
        pd: mass > 0 ? P / mass : 0, ploss: Number(p.P_loss_total_W) || 0, core: Number(p.P_fe_W) || 0,
        stranded: Number(p.P_cu_W) || 0, solid: (Number(p.P_mag_W) || 0) + (Number(p.P_shaft_W) || 0),
        pk: `${I.toFixed(2)}_${g.toFixed(2)}_${td.toFixed(3)}`,
      };
    }), [points, omega]);

  const cols = useMemo<SweepTableCol[]>(() => {
    const varied = (vals: any[]) => new Set(vals.map(v => Math.round(Number(v) * 1e4) / 1e4)).size > 1;
    const c: SweepTableCol[] = [];
    const keys = new Set<string>(); rows.forEach(r => Object.keys(r.ov).forEach(k => keys.add(k)));
    [...keys].sort().forEach(k => { if (varied(rows.map(r => r.ov[k]))) c.push({ id: 'ov:' + k, label: k, get: r => Number(r.ov[k]), fmt: v => String(Math.round(v * 1000) / 1000), vcol: true }); });
    if (varied(rows.map(r => r.I))) c.push({ id: 'I', label: 'I (A)', get: r => r.I, fmt: v => v.toFixed(1), vcol: true });
    if (varied(rows.map(r => r.g))) c.push({ id: 'g', label: 'γ (°)', get: r => r.g, fmt: v => v.toFixed(0), vcol: true });
    c.push(
      { id: 'T', label: 'T (N·m)', get: r => r.T, fmt: v => v.toFixed(1) },
      { id: 'P', label: 'P (kW)', get: r => r.P, fmt: v => v.toFixed(2) },
      { id: 'eff', label: 'η %', get: r => r.eff, fmt: v => v.toFixed(2) },
      { id: 'Vdc', label: 'V_dc (V)', get: r => r.Vpk / vdcF, fmt: v => v.toFixed(0) },
      { id: 'ripple', label: 'ripple %', get: r => r.ripple, fmt: v => v.toFixed(2) },
      { id: 'mass', label: 'mass (kg)', get: r => r.mass, fmt: v => v.toFixed(2) },
      { id: 'td', label: 'N·m/kg', get: r => r.td, fmt: v => v.toFixed(2) },
      { id: 'pd', label: 'kW/kg', get: r => r.pd, fmt: v => v.toFixed(3) },
      { id: 'ploss', label: 'P_loss (W)', get: r => r.ploss, fmt: v => v.toFixed(0) },
      { id: 'core', label: 'core (W)', get: r => r.core, fmt: v => v.toFixed(0) },
      { id: 'stranded', label: 'stranded (W)', get: r => r.stranded, fmt: v => v.toFixed(0) },
      { id: 'solid', label: 'solid (W)', get: r => r.solid, fmt: v => v.toFixed(0) },
    );
    return c;
  }, [rows, vdcF]);

  const [sortId, setSortId] = useState('ripple');
  const [dir, setDir] = useState<1 | -1>(1);
  const sorted = useMemo(() => {
    const col = cols.find(c => c.id === sortId) || cols[cols.length - 1];
    return col ? [...rows].sort((a, b) => (col.get(a) - col.get(b)) * dir) : rows;
  }, [rows, cols, sortId, dir]);
  const onSort = (id: string) => { if (sortId === id) setDir(d => (d === 1 ? -1 : 1)); else { setSortId(id); setDir(1); } };

  if (!rows.length) return null;
  const th: React.CSSProperties = { position: 'sticky', top: 0, cursor: 'pointer', padding: '4px 7px', textAlign: 'right', whiteSpace: 'nowrap', borderBottom: '1px solid #334155', userSelect: 'none' };
  return (
    <Box sx={{ mt: 1.5 }}>
      <Typography sx={{ fontSize: 11, color: '#94a3b8', mb: 0.5 }}>
        All swept designs ({rows.length}) — <span style={{ color: '#93c5fd' }}>variables</span> + outputs · click a column to sort
      </Typography>
      <Box sx={{ maxHeight: 360, overflow: 'auto', border: '1px solid #1e293b', borderRadius: 1 }}>
        <table style={{ borderCollapse: 'collapse', fontSize: 10.5, width: '100%', fontFamily: 'var(--font-mono, monospace)' }}>
          <thead>
            <tr>{cols.map(c => (
              <th key={c.id} onClick={() => onSort(c.id)}
                style={{ ...th, background: c.vcol ? '#10243f' : '#0e1a2f', color: c.vcol ? '#93c5fd' : '#cbd5e1', fontWeight: sortId === c.id ? 700 : 400 }}>
                {c.label}{sortId === c.id ? (dir === 1 ? ' ▲' : ' ▼') : ''}
              </th>))}</tr>
          </thead>
          <tbody>
            {sorted.map((r, i) => {
              const sel = selectedPk != null && r.pk === selectedPk;
              return (
              <tr key={i} onClick={() => onPick && onPick(r)}
                style={{ background: sel ? '#1e3a5f' : (i % 2 ? '#0a1628' : '#0c1c34'), cursor: onPick ? 'pointer' : 'default' }}>
                {cols.map(c => (
                  <td key={c.id} style={{ padding: '3px 7px', textAlign: 'right', whiteSpace: 'nowrap', color: c.vcol ? (sel ? '#fff' : '#e2e8f0') : (sel ? '#e2e8f0' : '#cbd5e1'), borderBottom: '1px solid #14233c' }}>
                    {c.fmt(c.get(r))}
                  </td>))}
              </tr>); })}
          </tbody>
        </table>
      </Box>
    </Box>
  );
};

const SweepTooltip: React.FC<any> = ({ active, payload }) => {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <Box sx={{ bgcolor: '#0a1628', border: '1px solid #334155', borderRadius: 1, p: 1, fontSize: 11 }}>
      <div style={{ color: '#93c5fd', fontWeight: 700 }}>I = {d.I} A · γ = {d.g}°{d.gi ? ` · geom ${d.gi}` : ''}</div>
      <div style={{ color: '#a855f7' }}>η = {d.y.toFixed(2)} %</div>
      <div style={{ color: '#3b82f6' }}>{d.x.toFixed(2)} N·m/kg</div>
      <div style={{ color: '#94a3b8' }}>T = {d.T?.toFixed?.(1)} N·m · ripple {d.ripple?.toFixed?.(1)} %</div>
    </Box>
  );
};

const SweepStudyPanel: React.FC = () => {
  const { sweepConfig, updateGeometryViaApi } = useMotorStore();
  const steps = readLS('sim.stepsPP', 24);   // single source: Simulation "Steps per electrical period"
  const [connectBy, setConnectBy] = useLS<'current_a' | 'gamma_deg'>('connectBy', 'current_a');
  const [selected, setSelected] = useState<any>(null);   // hand-picked best point
  const [applyMsg, setApplyMsg] = useState<string | null>(null);
  const [zoom, setZoom] = useState<{ x: [number, number]; y: [number, number] } | null>(null);   // mouse-wheel zoom
  const chartBoxRef = useRef<HTMLDivElement>(null);

  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number; cached: number } | null>(null);
  const [result, setResult] = useState<any>(null);
  const [sentOps, setSentOps] = useState<any[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const stopRef = useRef(false);

  // Variables come from the shared cards above (single interface).
  const active = Object.entries(sweepConfig.variations).filter(([, v]: any) => v.mode !== 'fixed') as [string, any][];
  const varOf = (name: string) => active.find(([n]) => n === name)?.[1];
  const cV = varOf('current_a');
  const gV = varOf('gamma_deg');
  const geomVars = active.filter(([n]) => !OP_VARS.has(n));

  const currents = cV ? buildGrid(Number(cV.min), Number(cV.max), Number(cV.step)) : [readLS('sim.current', 85)];
  const gammas   = gV ? buildGrid(Number(gV.min), Number(gV.max), Number(gV.step)) : [readLS('sim.gamma', 0)];
  const nGeom = geomVars.reduce((p, [, v]) => p * buildGrid(Number(v.min), Number(v.max), Number(v.step)).length, 1);
  const nPts = currents.length * gammas.length * nGeom;

  // Persist the result (partial OR final) to localStorage so the chart survives a
  // web reload even if the backend never finished / was restarted (client-side copy).
  const saveResult = (r: any) => {
    setResult(r);
    try { localStorage.setItem('sweepStudy.lastResult', JSON.stringify(r)); } catch { /* quota */ }
  };

  // shared poll loop — used by both run() and the resume-on-mount effect.
  const poll = async () => {
    while (!stopRef.current) {
      await new Promise(r => setTimeout(r, 1500));
      let st: any;
      try { st = await (await fetch(`${API}/api/optimization/scan/progress`)).json(); } catch { continue; }
      setProgress({ done: st.done ?? 0, total: st.total ?? 1, cached: st.cached ?? 0 });
      if (Array.isArray(st.points) && st.points.length) saveResult({ points: st.points });   // live points
      if (!st.running) { if (st.result) saveResult(st.result); if (st.error) setErr(st.error); break; }
    }
  };

  // On mount (page reload): resume a running scan live, or restore the LAST
  // completed sweep so the chart survives a reload (the backend keeps it in
  // memory + persisted to .last_scan.json, loaded on startup).
  useEffect(() => {
    // 1) instant: restore the last chart from localStorage (survives ANY reload).
    try { const c = localStorage.getItem('sweepStudy.lastResult'); if (c) setResult(JSON.parse(c)); } catch { /* ignore */ }
    // 2) reconcile with the backend: resume a running scan live, or adopt its
    //    persisted result (.last_scan.json, loaded on startup).
    (async () => {
      try {
        const st = await (await fetch(`${API}/api/optimization/scan/progress`)).json();
        if (st.running) {
          stopRef.current = false; setRunning(true);
          if (Array.isArray(st.points)) saveResult({ points: st.points });
          setProgress({ done: st.done ?? 0, total: st.total ?? 1, cached: st.cached ?? 0 });
          await poll(); setRunning(false);
        } else if (st.result && Array.isArray(st.result.points)) {
          saveResult(st.result);
        }
      } catch { /* ignore */ }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const run = async () => {
    setErr(null); setResult(null); setRunning(true); setProgress(null); stopRef.current = false;
    const rpm = readLS('sim.rpm', 4000);
    const ops: any[] = [];
    for (const g of gammas) for (const I of currents) ops.push({ current_a: I, gamma_deg: g, rpm });
    setSentOps(ops);
    const scanVars = geomVars.map(([name, v]) => ({ name, min: Number(v.min), max: Number(v.max), step: Number(v.step), mode: 'sweep' }));
    try {
      const res = await fetch(`${API}/api/optimization/scan`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          variables: scanVars, operating_points: ops, steps_per_period: steps,
          ripple_max_pct: 100, max_geometries: Math.max(1, nGeom),
          mesh_size_mm: readLS('mesh.meshSize', 4), min_size_mm: readLS('mesh.minSize', 0.3),
          pole_copy: readBool('mesh.poleCopy', false), torque_filter: readBool('sim.torqueFilter', true),
          n_sectors: Math.max(1, Math.round(readLS('mesh.nSectors', 1))),   // single source: Mesh tab (same as Simulation)
          gap_layers: readLS('mesh.gapLayers', 2),   // single source: Mesh tab — drives ripple/eddy; must match Simulation
          end_winding_factor: readLS('sim.endWinding', 0),   // single source: Simulation — drives copper loss/eff (same value Sim sends)
          rotor_eddy: readBool('sim.fieldLosses', true),   // single source: Simulation — field vs slab magnet eddy (drives eff)
          run_id: `sweep_${nPts}`,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${(await res.text()).slice(0, 200)}`);
      await poll();
    } catch (e: any) { setErr(String(e?.message ?? e)); }
    finally { setRunning(false); }
  };

  const cancel = async () => {
    stopRef.current = true;
    try { await fetch(`${API}/api/optimization/scan/cancel`, { method: 'POST' }); } catch { /* ignore */ }
  };

  // Wipe the displayed result + its localStorage copy (e.g. a stale sweep from
  // before the d-axis recalibration).  The backend's last-scan is cleared
  // separately; a fresh Run overwrites this anyway.
  const clearResult = () => {
    setResult(null); setSelected(null); setProgress(null); setZoom(null);
    try { localStorage.removeItem('sweepStudy.lastResult'); } catch { /* ignore */ }
  };

  // Group feasible points and join them along the chosen variable (current or γ).
  const series = useMemo(() => {
    const pts: any[] = result?.points || [];
    const byCurrent = connectBy !== 'gamma_deg';   // connect along current (group per γ) or along γ (group per current)
    const groups = new Map<string, { label: string; rows: any[] }>();
    for (const p of pts) {
      if (!p.feasible || p.torque_per_mass_Nm_kg == null) continue;
      const gam = typeof p.gamma_deg === 'number' ? p.gamma_deg : (sentOps[p.op_index]?.gamma_deg ?? 0);
      const cur = p.current_a;
      const gi = p.geom_id ?? 0;
      const key = byCurrent ? `${gi}|g${gam}` : `${gi}|i${cur}`;
      const label = byCurrent ? `γ=${gam}°${gi ? ` g${gi}` : ''}` : `I=${cur}A${gi ? ` g${gi}` : ''}`;
      const row = { x: p.torque_per_mass_Nm_kg, y: (p.efficiency ?? 0) * 100,
                    I: cur, g: gam, gi, ripple: p.T_ripple_pct, T: p.T_em_Nm,
                    overrides: p.overrides || {}, _c: byCurrent ? cur : gam,
                    // Full per-design metrics so applying this point shows the
                    // sweep's already-computed numbers in Simulation — no re-run.
                    eff: (p.efficiency ?? 0) * 100, mass: p.mass_total_kg,
                    td: p.torque_per_mass_Nm_kg, Vpk: p.V_peak,
                    ploss: p.P_loss_total_W, core: p.P_fe_W,
                    stranded: p.P_cu_W, solid: (p.P_mag_W ?? 0) + (p.P_shaft_W ?? 0),
                    pk: `${Number(cur).toFixed(2)}_${Number(gam).toFixed(2)}_${Number(p.torque_per_mass_Nm_kg).toFixed(3)}` };
      if (!groups.has(key)) groups.set(key, { label, rows: [] });
      groups.get(key)!.rows.push(row);
    }
    return [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0]))
      .map(([key, g]) => ({ key, label: g.label, rows: g.rows.sort((a, b) => a._c - b._c) }));
  }, [result, sentOps, connectBy]);

  const nFeasible = series.reduce((s, c) => s + c.rows.length, 0);

  // Build failures: some geometries can't be meshed (e.g. teeth too wide for the
  // slot → TopologyException).  Those points come back feasible:false and are
  // dropped from the chart/table — so surface them explicitly, and pinpoint which
  // variable VALUES always fail, so the user knows what to avoid / narrow.
  const buildFails = useMemo(() => {
    const pts: any[] = result?.points || [];
    const failed = pts.filter(p => p && p.feasible === false);
    if (!failed.length) return null;
    const keys = new Set<string>();
    pts.forEach(p => Object.keys(p.overrides || {}).forEach(k => keys.add(k)));
    const culprits: string[] = [];
    keys.forEach(k => {
      const byVal = new Map<number, { tot: number; bad: number }>();
      pts.forEach(p => {
        const raw = (p.overrides || {})[k];
        if (raw == null) return;
        const val = Math.round(Number(raw) * 1e4) / 1e4;
        const e = byVal.get(val) || { tot: 0, bad: 0 };
        e.tot++; if (p.feasible === false) e.bad++;
        byVal.set(val, e);
      });
      const badVals = [...byVal.entries()].filter(([, e]) => e.tot > 0 && e.bad === e.tot).map(([v]) => v).sort((a, b) => a - b);
      const someGood = [...byVal.values()].some(e => e.bad < e.tot);
      if (badVals.length && someGood) culprits.push(`${k} = ${badVals.map(v => Math.round(v * 1000) / 1000).join(', ')}`);
    });
    const reason = String(failed[0]?.error || '').split(':')[0].trim();
    return { n: failed.length, total: pts.length, culprits, reason };
  }, [result]);

  // Data extent (with a little padding) — the un-zoomed view + the basis for
  // mapping the cursor to data coords during wheel-zoom.
  const extent = useMemo(() => {
    const xs: number[] = [], ys: number[] = [];
    series.forEach(s => s.rows.forEach((r: any) => { xs.push(r.x); ys.push(r.y); }));
    if (!xs.length) return null;
    const pad = (a: number, b: number): [number, number] => { const d = (b - a) || 1; return [a - d * 0.05, b + d * 0.05]; };
    return { x: pad(Math.min(...xs), Math.max(...xs)), y: pad(Math.min(...ys), Math.max(...ys)) };
  }, [series]);

  // Keep a zoom window INSIDE the data extent: stops repeated zoom-out from blowing
  // the axis up to ~1e8, and snaps back to the full view once zoomed all the way out.
  const clampDom = (d: [number, number], ext: [number, number]): [number, number] => {
    let [a, b] = d;
    const er = ext[1] - ext[0];
    if (!(b > a) || b - a >= er) return [ext[0], ext[1]];   // invalid / zoomed past full → full view
    if (a < ext[0]) { b += ext[0] - a; a = ext[0]; }        // shift window back inside
    if (b > ext[1]) { a -= b - ext[1]; b = ext[1]; }
    return [Math.max(a, ext[0]), Math.min(b, ext[1])];
  };

  // Mouse-wheel zoom centred on the cursor (scroll up = zoom in).  Attached as a
  // NON-passive native listener so preventDefault stops the page from scrolling.
  useEffect(() => {
    const el = chartBoxRef.current;
    if (!el || !extent) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const box = el.getBoundingClientRect();
      const padL = 54, padR = 24, padT = 8, padB = 40;   // ≈ plot area inside axes/margins
      const fx = Math.min(1, Math.max(0, (e.clientX - box.left - padL) / Math.max(1, box.width - padL - padR)));
      const fy = Math.min(1, Math.max(0, (e.clientY - box.top - padT) / Math.max(1, box.height - padT - padB)));
      const f = e.deltaY < 0 ? 0.85 : 1 / 0.85;
      setZoom(prev => {
        const cur = prev ?? extent;
        const cx = cur.x[0] + fx * (cur.x[1] - cur.x[0]);
        const cy = cur.y[1] - fy * (cur.y[1] - cur.y[0]);   // y pixels grow downward
        return {
          x: clampDom([cx - (cx - cur.x[0]) * f, cx + (cur.x[1] - cx) * f], extent.x),
          y: clampDom([cy - (cy - cur.y[0]) * f, cy + (cur.y[1] - cy) * f], extent.y),
        };
      });
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [extent]);

  // Hand-pick a point on the chart → apply its design: geometry (overrides) +
  // operating point (current/γ) → config + Simulation (same as "apply best").
  const applyPoint = async (p: any) => {
    if (!p) return;
    setApplyMsg('applying…');
    try {
      if (p.overrides && Object.keys(p.overrides).length) await updateGeometryViaApi(p.overrides);
      await fetch(`${API}/api/simulation/config`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ max_current: p.I, phase_offset_deg: p.g }),
      });
      window.dispatchEvent(new CustomEvent('sim-operating-point', { detail: { current: p.I, gamma: p.g } }));
      // The sweep already computed this design's full FEM result — push those
      // exact numbers into the Simulation summary so it shows them immediately,
      // without a re-run (the user picks on these very numbers).
      try {
        const rpm = readLS('sim.rpm', 4000);
        const omega = 2 * Math.PI * rpm / 60;
        const T = Number(p.T) || 0, mass = Number(p.mass) || 0;
        const Vpk = Number(p.Vpk) || 0, Vrms = Vpk / Math.SQRT2;
        const Vlpk = Vpk * Math.sqrt(3), Vlrms = Vlpk / Math.SQRT2;
        const Pmech = T * omega, ploss = Number(p.ploss) || 0;
        const summary = {
          rpm, I_phase_rms_A: Number(p.I) || 0, gamma_deg: Number(p.g) || 0,
          T_em_avg_Nm: T, T_ripple_pct: Number(p.ripple) || 0, P_mech_W: Pmech,
          V_phase_peak_V: Vpk, V_phase_rms_V: Vrms, V_line_peak_V: Vlpk, V_line_rms_V: Vlrms,
          KV_rpm_per_V_phase: Vrms > 0 ? rpm / Vrms : 0, KV_rpm_per_V_line: Vlrms > 0 ? rpm / Vlrms : 0,
          P_loss_total_W: ploss, P_core_W: Number(p.core) || 0,
          P_stranded_W: Number(p.stranded) || 0, P_solid_W: Number(p.solid) || 0,
          efficiency: (Number(p.eff) || 0) / 100, mass_total_kg: mass, mass_components: [],
          torque_per_mass_Nm_kg: Number(p.td) || (mass > 0 ? T / mass : 0),
          power_per_mass_W_kg: mass > 0 ? Pmech / mass : 0,
          loss_density_W_kg: mass > 0 ? ploss / mass : 0,
        };
        window.dispatchEvent(new CustomEvent('sim-apply-summary', { detail: { summary } }));
      } catch { /* summary is best-effort — geometry + operating point already applied */ }
      const ovStr = Object.entries(p.overrides || {}).map(([k, v]) => `${k}=${v}`).join(', ');
      setApplyMsg(`✓ saved${ovStr ? ': ' + ovStr : ' (base geometry)'} · I=${p.I} A · γ=${p.g}° — numbers shown in Simulation (Run there only for waveforms)`);
    } catch (e: any) { setApplyMsg('✗ save FAILED (' + String(e?.message ?? e) + ') — nothing was changed; try again'); }
  };

  const VarLine: React.FC<{ on: boolean; label: string; v: any; fixedVal: number }> = ({ on, label, v, fixedVal }) => (
    <Typography sx={{ fontSize: 11, color: on ? '#cbd5e1' : '#475569', mb: 0.2 }}>
      • <strong>{label}</strong>: {on
        ? <>{v.min} … {v.max} step {v.step} <span style={{ color: '#64748b' }}>({buildGrid(Number(v.min), Number(v.max), Number(v.step)).length} pts)</span></>
        : <>{fixedVal} <span style={{ color: '#475569' }}>(fixed — add it to the variables above to sweep)</span></>}
    </Typography>
  );

  return (
    <Box sx={{ mt: 2 }}>
      <Typography sx={{ fontSize: '0.7rem', fontWeight: 700, color: '#475569',
        letterSpacing: '0.1em', textTransform: 'uppercase', mb: 0.5 }}>
        Sweep study — efficiency vs torque/mass
      </Typography>
      <Typography sx={{ fontSize: 11, color: '#64748b', mb: 1.25 }}>
        Sweeps the <strong>variables you added above</strong> (current/γ → operating points,
        geometry → a grid). Each point is a real full-disk transient (~1 min each — keep grids small).
        rpm &amp; mesh come from the Simulation / Mesh tabs.
      </Typography>

      <Box sx={{ bgcolor: '#0a1628', border: '1px solid #1e293b', borderRadius: 1, p: 1, mb: 1.25 }}>
        <VarLine on={!!cV} label="Phase current" v={cV} fixedVal={readLS('sim.current', 85)} />
        <VarLine on={!!gV} label="Load angle γ" v={gV} fixedVal={readLS('sim.gamma', 0)} />
        {geomVars.map(([name, v]) => (
          <Typography key={name} sx={{ fontSize: 11, color: '#cbd5e1', mb: 0.2 }}>
            • <strong>{name}</strong>: {v.min} … {v.max} step {v.step}
          </Typography>
        ))}
      </Box>

      <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', mb: 1 }}>
        <TextField label="steps/period (Simulation)" size="small" value={steps} disabled
          inputProps={{ style: { fontSize: 12, padding: '5px 8px' } }} sx={{ width: 170 }} />
        <FormControl size="small" sx={{ minWidth: 150 }}>
          <InputLabel sx={{ fontSize: 12 }}>connect along</InputLabel>
          <Select label="connect along" value={connectBy} onChange={e => setConnectBy(e.target.value as any)}
            sx={{ fontSize: 12 }}>
            <MenuItem value="current_a" sx={{ fontSize: 12 }}>current (curve per γ)</MenuItem>
            <MenuItem value="gamma_deg" sx={{ fontSize: 12 }}>γ (curve per current)</MenuItem>
          </Select>
        </FormControl>
        <Typography sx={{ fontSize: 11, color: nPts > 40 ? '#fca5a5' : '#64748b', flex: 1 }}>
          <strong>{nPts}</strong> pt{nPts === 1 ? '' : 's'}{nPts > 40 ? ' — large, slow' : ''}
        </Typography>
      </Box>

      <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', mb: 1 }}>
        {!running ? (
          <Button size="small" variant="contained" onClick={run} disabled={nPts < 1}
            sx={{ textTransform: 'none', fontWeight: 700 }}>
            Run sweep ({nPts})
          </Button>
        ) : (
          <Button size="small" variant="outlined" color="warning" onClick={cancel} sx={{ textTransform: 'none' }}>
            Stop
          </Button>
        )}
        {!running && result && (
          <Button size="small" variant="text" onClick={clearResult}
            sx={{ textTransform: 'none', fontSize: 11, color: '#94a3b8', minWidth: 0 }}>
            Clear result
          </Button>
        )}
        {progress && running && (
          <Typography sx={{ fontSize: 11, color: '#64748b' }}>
            {progress.done}/{progress.total}{progress.cached ? ` · ${progress.cached} reused` : ''}</Typography>
        )}
        {err && <Typography sx={{ fontSize: 11, color: '#fca5a5' }}>✗ {err}</Typography>}
      </Box>
      {running && <LinearProgress variant={progress ? 'determinate' : 'indeterminate'}
        value={progress ? (100 * progress.done) / Math.max(1, progress.total) : 0}
        sx={{ mb: 1.5, height: 4, borderRadius: 2 }} />}

      {(running || series.length > 0) && (
        <>
          <Divider sx={{ borderColor: '#1e293b', my: 1 }} />
          <Typography sx={{ fontSize: 11, color: '#64748b', mb: 0.5 }}>
            {running
              ? `computing… ${nFeasible} point${nFeasible === 1 ? '' : 's'} so far`
              : `${nFeasible} feasible point${nFeasible === 1 ? '' : 's'} · ${series.length} curve${series.length === 1 ? '' : 's'} · click a point to pick & save it`}
          </Typography>
          {buildFails && (
            <Typography sx={{ fontSize: 11, color: '#fbbf24', mb: 0.5, lineHeight: 1.4 }}>
              ⚠ {buildFails.n} of {buildFails.total} design{buildFails.n === 1 ? '' : 's'} couldn’t be built
              {buildFails.reason ? ` (${buildFails.reason})` : ' (invalid geometry)'} — skipped.
              {buildFails.culprits.length
                ? ` Always fails at: ${buildFails.culprits.join('; ')} — narrow that range.`
                : ''}
            </Typography>
          )}
          <Box ref={chartBoxRef} onDoubleClick={() => setZoom(null)} sx={{ height: 340 }}>
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ top: 8, right: 24, left: 8, bottom: 24 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis type="number" dataKey="x" name="Nm/kg" tick={{ fontSize: 10 }}
                  domain={extent ? (zoom ? clampDom(zoom.x, extent.x) : extent.x) : ['auto', 'auto']} allowDataOverflow={!!zoom}
                  tickFormatter={(v: number) => v.toFixed(2)}
                  label={{ value: 'Torque / mass (N·m/kg)', position: 'insideBottom', offset: -12, fontSize: 11, fill: '#64748b' }} />
                <YAxis type="number" dataKey="y" name="Eff %" tick={{ fontSize: 10 }} width={52}
                  domain={extent ? (zoom ? clampDom(zoom.y, extent.y) : extent.y) : ['auto', 'auto']} allowDataOverflow={!!zoom}
                  tickFormatter={(v: number) => v.toFixed(2)}
                  label={{ value: 'Efficiency (%)', angle: -90, position: 'insideLeft', fontSize: 11, fill: '#64748b' }} />
                <Tooltip content={<SweepTooltip />} />
                {/* Legend removed — the table below lists I/γ per design, and
                    selecting a row highlights its point (and vice-versa). */}
                {series.map((s, i) => (
                  <Scatter key={s.key} name={s.label} data={s.rows}
                    fill={GCOL[i % GCOL.length]}
                    line={{ stroke: GCOL[i % GCOL.length], strokeWidth: 1.5 }}
                    lineJointType="monotoneX" isAnimationActive={false} cursor="pointer"
                    shape={(p: any) => {
                      const sel = selected && p.payload?.pk === (selected as any).pk;
                      return <circle cx={p.cx} cy={p.cy} r={sel ? 5.5 : 2.4} fill={sel ? '#fbbf24' : GCOL[i % GCOL.length]}
                        stroke={sel ? '#fff' : 'none'} strokeWidth={sel ? 1.5 : 0} />;
                    }}
                    onClick={(d: any) => { setSelected(d?.payload ?? d); setApplyMsg(null); }} />
                ))}
              </ScatterChart>
            </ResponsiveContainer>
          </Box>
          {selected && (
            <Box sx={{ mt: 1, p: 1, bgcolor: '#0a1628', border: '1px solid #334155', borderRadius: 1 }}>
              <Typography sx={{ fontSize: 11, color: '#cbd5e1' }}>
                Picked: <strong>I = {selected.I} A · γ = {selected.g}°</strong> → η {selected.y?.toFixed?.(2)} % · {selected.x?.toFixed?.(2)} N·m/kg · T {selected.T?.toFixed?.(1)} N·m · ripple {selected.ripple?.toFixed?.(1)} %
              </Typography>
              <Typography sx={{ fontSize: 10.5, color: '#93c5fd', mt: 0.25, wordBreak: 'break-word' }}>
                geometry: {Object.keys(selected.overrides || {}).length
                  ? Object.entries(selected.overrides).map(([k, v]) => `${k}=${v}`).join(' · ')
                  : '(base — no swept variables)'}
              </Typography>
              <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', mt: 0.5 }}>
                <Button size="small" variant="contained" onClick={() => applyPoint(selected)}
                  sx={{ textTransform: 'none', fontSize: 11 }}>
                  Apply &amp; save this design
                </Button>
                {applyMsg && <Typography sx={{ fontSize: 11,
                  color: applyMsg.startsWith('✓') ? '#4ade80' : applyMsg.startsWith('✗') ? '#fca5a5' : '#64748b' }}>
                  {applyMsg}</Typography>}
              </Box>
            </Box>
          )}
          <SweepTable points={result?.points || []} rpm={readLS('sim.rpm', 4000)}
            vdcFactor={(({ svpwm: 1 / Math.sqrt(3), sine: 0.5, sixstep: 2 / Math.PI } as Record<string, number>)[sweepConfig.modulation ?? 'svpwm']) || (1 / Math.sqrt(3))}
            selectedPk={(selected as any)?.pk} onPick={(r) => { setSelected(r); setApplyMsg(null); }} />
        </>
      )}
    </Box>
  );
};

export default SweepStudyPanel;
