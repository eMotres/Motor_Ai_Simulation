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
  FormControl, InputLabel, Select, MenuItem, Chip,
} from '@mui/material';
import {
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';
import { useMotorStore } from '../../stores/motorStore';
import SectionLabel from '../common/SectionLabel';
import HelpTip from '../common/HelpTip';
import { autoSaveAppliedDesign, appliedSaveLine } from '../../lib/appliedAutoSave';
import { copyTsv, downloadCsv, downloadXlsx, stampName } from '../../lib/xlsxExport';
import type { AppliedSaveResult } from '../../lib/appliedAutoSave';
import { sweepResumeNoticeText } from '../../lib/sweepResumeNotice';
import type { SweepResumeInfo } from '../../lib/sweepResumeNotice';
import { readCurrentUnit, formatCurrent } from '../../lib/sweepCurrentUnit';
import { readAllowNewLamination } from '../../lib/releasedContext';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

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
// The Simulation summary shows torque, power and voltages rescaled by the
// machine's 3-D end-effect factor k_flux while its "3D" button is on
// (SummaryTable.tsx, default on).  This panel showed the raw 2-D T·ω, so the
// two tabs disagreed by exactly k: the user scaled the current off the sweep
// to land on 500 kW and Simulation answered 487.46 = 500 × 0.9724
// (2026-09-08).  Same switch, same k, read from the last Simulation summary of
// the machine; null = show 2-D (switch off, or no passport yet).
const readApply3dK = (): number | null => {
  try {
    if (localStorage.getItem('sim.apply3d') === '0') return null;
    const s = JSON.parse(localStorage.getItem('sim.lastSummary') || 'null');
    const k = Number(s?.end3d?.k_flux);
    return Number.isFinite(k) && k > 0 && k <= 1.2 ? k : null;
  } catch { return null; }
};
// What the 3-D factor does to one design's numbers — the SAME rule as the
// Simulation summary: flux-proportional quantities × k, losses kept 2-D,
// η recomputed from the corrected shaft power.
const scale3d = (k: number | null, T2: number, P2_kW: number, Vpk2: number,
                 Vl2: number, KV2: number, td2: number, pd2: number,
                 eff2: number, ploss_W: number) => {
  if (k == null) return { T: T2, P: P2_kW, Vpk: Vpk2, Vl: Vl2, KV: KV2, td: td2, pd: pd2, eff: eff2 };
  const P = P2_kW * k;
  return {
    T: T2 * k, P, Vpk: Vpk2 * k, Vl: Vl2 * k, KV: KV2 > 0 ? KV2 / k : KV2,
    td: td2 * k, pd: pd2 * k,
    eff: (P > 0 && ploss_W > 0) ? 100 * (P * 1000) / (P * 1000 + ploss_W) : eff2,
  };
};

const GCOL = ['#22c55e', '#3b82f6', '#a855f7', '#f59e0b', '#ef4444', '#14b8a6', '#ec4899', '#eab308', '#60a5fa', '#f97316'];
const OP_VARS = new Set(['current_a', 'gamma_deg']);

// ── Excel-like sortable table of every swept design ──────────────────────────
// Leading (blue) columns = the variables that actually varied in the sweep
// (geometry overrides + operating point); the rest are FEM outputs. Every metric
// already rides on each point (the backend spreads the full eval result). Click
// any header to sort asc/desc. Losses map to the Ansys breakdown:
// core = P_fe; copper winding is split into Cu DC (I²R, INCLUDES the end-windings via
// k_end → grows with tooth_width) and Cu AC (proximity/eddy in the strands); solid =
// P_mag + P_shaft (eddy in solid conductors).
interface SweepTableCol { id: string; label: string; get: (r: any) => number; fmt: (v: number) => string; vcol?: boolean }
const SweepTable: React.FC<{ points: any[]; rpm: number; vdcFactor?: number; selectedPk?: string; onPick?: (r: any) => void }> =
  ({ points, rpm, vdcFactor, selectedPk, onPick }) => {
  const vdcF = vdcFactor || (1 / Math.sqrt(3));   // V_dc = V_peak / mod_factor (SVPWM → ×√3)
  const omega = 2 * Math.PI * (rpm || 4000) / 60;
  const k3d = readApply3dK();
  const rows = useMemo(() => (points || [])
    .filter(p => p && p.feasible && p.T_em_Nm != null)
    .map(p => {
      // P: the point's OWN solved P_mech_W (T·ω at the rpm the sweep actually ran
      // at) — the T·ω fallback uses the CURRENT sim.rpm, which drifts if the user
      // changed rpm after the sweep, so it only covers pre-P_mech_W stored results.
      const P2 = p.P_mech_W != null ? Number(p.P_mech_W) / 1000
                                    : (Number(p.T_em_Nm) || 0) * omega / 1000;   // kW, 2-D
      const mass = Number(p.mass_total_kg) || 0;
      const td2 = Number(p.torque_per_mass_Nm_kg) || 0;
      const I = Number(p.current_a) || 0, g = Number(p.gamma_deg) || 0;
      const eff2 = (Number(p.efficiency) || 0) * 100;
      const ploss = Number(p.P_loss_total_W) || 0;
      const pd2 = p.power_per_mass_W_kg != null ? Number(p.power_per_mass_W_kg) / 1000
                                               : (mass > 0 ? P2 / mass : 0);
      // 3-D view (same k and rule as the Simulation summary), 2-D kept under raw2d
      // so applyPoint pushes the solver's own numbers and lets the summary scale them.
      const s3 = scale3d(k3d, Number(p.T_em_Nm) || 0, P2, Number(p.V_peak) || 0,
                         Number(p.V_line_peak_V) || 0, Number(p.KV_rpm_per_V_line) || 0,
                         td2, pd2, eff2, ploss);
      const { T, P, eff, Vpk, td, pd } = s3;
      return {
        ov: p.overrides || {}, overrides: p.overrides || {}, I, g, x: td, y: eff,
        apply_eligible: p.apply_eligible === true,
        T, P, eff, Vpk,
        // Solved terminal numbers carried through UNDER THE BACKEND NAMES so
        // applyPoint can push them verbatim instead of re-synthesizing them from
        // sinusoid identities (which over-read V_line peak — triplens cancel).
        V_line_peak_V: s3.Vl, KV_rpm_per_V_line: s3.KV,
        P_mech_W: P * 1000, power_per_mass_W_kg: pd * 1000,
        raw2d: { T: Number(p.T_em_Nm) || 0, P_mech_W: p.P_mech_W, Vpk: Number(p.V_peak) || 0,
                 V_line_peak_V: p.V_line_peak_V, KV_rpm_per_V_line: p.KV_rpm_per_V_line,
                 td: td2, power_per_mass_W_kg: p.power_per_mass_W_kg, eff: eff2 },
        k3d,
        rpm: p.rpm,   // absent today (refine result omits it) — kept for when it lands
        ripple: Number(p.T_ripple_pct) || 0, mass, td,
        pd,
        ploss, core: Number(p.P_fe_W) || 0,
        stranded: Number(p.P_cu_W) || 0,
        strandedDc: Number(p.P_cu_dc_W) || 0, strandedAc: Number(p.P_cu_ac_W) || 0,
        solid: (Number(p.P_mag_W) || 0) + (Number(p.P_shaft_W) || 0),
        // Eddy-settle verdict (2026-09-07).  `undefined` = the point predates
        // the flag and says nothing; a point that DID say so and said False
        // has start-up values in `solid`, `ploss` and `eff` — see EDDY_NOTE.
        settled: typeof p.eddy_settled === 'boolean' ? p.eddy_settled : undefined,
        settleResid: p.eddy_settle_residual == null ? null : Number(p.eddy_settle_residual),
        // Stable unique key = (geometry, operating-point) — SAME in the chart, so
        // clicking a point highlights its row and vice-versa (no float-round drift).
        pk: `g${p.geom_id ?? 0}_o${p.op_index ?? 0}`,
      };
    }), [points, omega, k3d]);

  const cols = useMemo<SweepTableCol[]>(() => {
    const varied = (vals: any[]) => new Set(vals.map(v => Math.round(Number(v) * 1e4) / 1e4)).size > 1;
    const c: SweepTableCol[] = [];
    const keys = new Set<string>(); rows.forEach(r => Object.keys(r.ov).forEach(k => keys.add(k)));
    [...keys].sort().forEach(k => { if (varied(rows.map(r => r.ov[k]))) c.push({ id: 'ov:' + k, label: k, get: r => Number(r.ov[k]), fmt: v => String(Math.round(v * 1000) / 1000), vcol: true }); });
    if (varied(rows.map(r => r.I))) c.push({ id: 'I', label: 'I (A)', get: r => r.I, fmt: v => v.toFixed(1), vcol: true });
    if (varied(rows.map(r => r.g))) c.push({ id: 'g', label: 'γ (°)', get: r => r.g, fmt: v => v.toFixed(0), vcol: true });
    c.push(
      { id: 'T', label: 'T (N·m)', get: r => r.T, fmt: v => v.toFixed(2) },
      { id: 'P', label: 'P (kW)', get: r => r.P, fmt: v => v.toFixed(2) },
      { id: 'eff', label: 'η %', get: r => r.eff, fmt: v => v.toFixed(2) },
      { id: 'Vdc', label: 'V_dc (V)', get: r => r.Vpk / vdcF, fmt: v => v.toFixed(0) },
      { id: 'ripple', label: 'ripple %', get: r => r.ripple, fmt: v => v.toFixed(2) },
      { id: 'mass', label: 'mass (kg)', get: r => r.mass, fmt: v => v.toFixed(3) },
      { id: 'td', label: 'N·m/kg', get: r => r.td, fmt: v => v.toFixed(2) },
      { id: 'pd', label: 'kW/kg', get: r => r.pd, fmt: v => v.toFixed(3) },
      { id: 'ploss', label: 'P_loss (W)', get: r => r.ploss, fmt: v => v.toFixed(1) },
      { id: 'core', label: 'core (W)', get: r => r.core, fmt: v => v.toFixed(1) },
      { id: 'cuDc', label: 'Cu DC (W)', get: r => r.strandedDc, fmt: v => v.toFixed(1) },
      { id: 'cuAc', label: 'Cu AC (W)', get: r => r.strandedAc, fmt: v => v.toFixed(1) },
      { id: 'solid', label: 'solid (W)', get: r => r.solid, fmt: v => v.toFixed(1) },
    );
    // ── DID THE EDDY TRANSIENT SETTLE? (user's sweep, 2026-09-07) ─────────
    // Only when the points actually SAY — an older stored result carries no
    // verdict, and printing ✓ for silence would be a claim nobody made.
    // Sortable like everything else: settled sorts as −1 so one click groups
    // every flagged design together, ahead of the biggest residual.
    if (rows.some(r => r.settled !== undefined))
      c.push({ id: 'eddy', label: 'eddy', get: r => (
        r.settled === undefined ? -2 : r.settled ? -1
          : (r.settleResid == null ? 1e4 : r.settleResid * 100)),
        fmt: v => (v === -2 ? '·' : v < 0 ? '✓' : v >= 1e4 ? '✗' : `✗ ${v.toFixed(1)}%`) });
    return c;
  }, [rows, vdcF]);

  const [sortId, setSortId] = useState('ripple');
  const [dir, setDir] = useState<1 | -1>(1);
  const [copied, setCopied] = useState<string | null>(null);
  const sorted = useMemo(() => {
    const col = cols.find(c => c.id === sortId) || cols[cols.length - 1];
    return col ? [...rows].sort((a, b) => (col.get(a) - col.get(b)) * dir) : rows;
  }, [rows, cols, sortId, dir]);
  const onSort = (id: string) => { if (sortId === id) setDir(d => (d === 1 ? -1 : 1)); else { setSortId(id); setDir(1); } };

  if (!rows.length) return null;
  const th: React.CSSProperties = { position: 'sticky', top: 0, cursor: 'pointer', padding: '4px 7px', textAlign: 'right', whiteSpace: 'nowrap', borderBottom: '1px solid var(--line)', userSelect: 'none' };
  // Export (user 2026-09-03): the table as shown — same columns, current sort,
  // FULL-precision numbers (the on-screen rounding is display only).
  const exportHeader = cols.map(c => c.label);
  const exportRows = () => sorted.map(r => cols.map(c => { const v = c.get(r); return Number.isFinite(v) ? v : null; }));
  const exportName = () => stampName(`sweep_${rows.length}pts`);
  const doCopy = async () => {
    const ok = await copyTsv(exportHeader, exportRows());
    setCopied(ok ? 'copied — paste into a Google Sheet (Ctrl+V)' : 'clipboard blocked — use Excel/CSV');
    setTimeout(() => setCopied(null), 4000);
  };
  const xBtn: React.CSSProperties = { fontSize: 10, padding: '0 6px', border: '1px solid var(--line)', borderRadius: 3, cursor: 'pointer', color: 'var(--text-2)', background: 'transparent', marginLeft: 4 };
  return (
    <Box sx={{ mt: 1.5 }}>
      <Typography component="div" sx={{ fontSize: 11, color: 'var(--text-2)', mb: 0.5, display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 0.5 }}>
        <span>All swept designs ({rows.length}) — <span style={{ color: '#93c5fd' }}>variables</span> + outputs · click a column to sort</span>
        <span style={{ flex: 1 }} />
        <button type="button" style={xBtn} title="Download as .xlsx — numbers as numbers, header row frozen; opens in Excel and imports into Google Sheets (File → Import)"
          onClick={() => downloadXlsx(exportName(), exportHeader, exportRows(), 'Sweep')}>⭳ Excel</button>
        <button type="button" style={xBtn} title="Download as UTF-8 CSV (comma, dot decimal)"
          onClick={() => downloadCsv(exportName(), exportHeader, exportRows())}>⭳ CSV</button>
        <button type="button" style={xBtn} title="Copy the table to the clipboard as tab-separated text — open a Google Sheet, click A1, paste"
          onClick={() => void doCopy()}>⎘ Google Sheets</button>
        {copied && <span style={{ fontSize: 10, color: copied.startsWith('copied') ? '#34d399' : '#fbbf24' }}>{copied}</span>}
      </Typography>
      <Box sx={{ overflowX: 'auto', border: '1px solid var(--line-soft)', borderRadius: 1 }}>
        <table style={{ borderCollapse: 'collapse', fontSize: 10.5, width: '100%', fontFamily: 'var(--font-mono, monospace)' }}>
          <thead>
            <tr>{cols.map(c => (
              <th key={c.id} onClick={() => onSort(c.id)}
                style={{ ...th, background: c.vcol ? 'var(--line-accent)' : 'var(--panel-2)', color: c.vcol ? 'var(--brand)' : 'var(--text-1)', fontWeight: sortId === c.id ? 700 : 400 }}>
                {c.label}{sortId === c.id ? (dir === 1 ? ' ▲' : ' ▼') : ''}
              </th>))}</tr>
          </thead>
          <tbody>
            {sorted.map((r, i) => {
              const sel = selectedPk != null && r.pk === selectedPk;
              return (
              <tr key={i} onClick={() => onPick && onPick(r)}
                style={{ background: sel ? 'var(--line-accent)' : (i % 2 ? 'var(--panel-2)' : 'var(--line-soft)'), cursor: onPick ? 'pointer' : 'default' }}>
                {cols.map(c => (
                  <td key={c.id} style={{ padding: '3px 7px', textAlign: 'right', whiteSpace: 'nowrap', color: 'var(--text-0)', borderBottom: '1px solid var(--line-soft)' }}>
                    {c.fmt(c.get(r))}
                  </td>))}
              </tr>); })}
          </tbody>
        </table>
      </Box>
    </Box>
  );
};

// Compact numeric format: integers stay whole, floats show up to 3 sig-figs
// without trailing zeros (0.592, 5.4, 12, 3).
const _fmtVar = (v: any) =>
  typeof v === 'number' ? String(parseFloat(v.toFixed(3))) : String(v);

const SweepTooltip: React.FC<any> = ({ active, payload }) => {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  const ov = (d.overrides || {}) as Record<string, any>;
  // geometry vars only — I and γ are already in the header line above
  const ovKeys = Object.keys(ov).filter(k => k !== 'gamma_deg' && k !== 'current_a');
  return (
    <Box sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line)', borderRadius: 1, p: 1, fontSize: 11, maxWidth: 360 }}>
      <div style={{ color: '#93c5fd', fontWeight: 700 }}>I = {d.I} A · γ = {d.g}°{d.gi ? ` · geom ${d.gi}` : ''}</div>
      {/* every swept variable that changes at this point (the point's overrides) */}
      {ovKeys.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', columnGap: 10, rowGap: 1,
          marginTop: 3, paddingTop: 3, borderTop: '1px solid var(--line-soft)', fontSize: 10 }}>
          {/* The value shown IS the one the FEM solved: a design outside a
              closed-form bound (a winding that will not fit the slot) is
              rejected before the eval, never pulled back to the bound and
              solved under the requested label. */}
          {ovKeys.map(k => (
            <span key={k} style={{ color: 'var(--text-3)', whiteSpace: 'nowrap' }}>
              {k} = <b style={{ color: 'var(--text-1)' }}>{_fmtVar(ov[k])}</b>
            </span>
          ))}
        </div>
      )}
      <div style={{ color: '#a855f7', marginTop: 3 }}>η = {d.y.toFixed(2)} %</div>
      <div style={{ color: '#3b82f6' }}>{d.x.toFixed(2)} N·m/kg</div>
      <div style={{ color: 'var(--text-2)' }}>T = {d.T?.toFixed?.(1)} N·m · ripple {d.ripple?.toFixed?.(1)} %</div>
      {/* The hollow marker, named (2026-09-07). */}
      {d.settled === false && (
        <div style={{ color: '#fbbf24', marginTop: 2 }}>
          ⚠ eddy transient not settled{d.settleResid != null
            ? ` (${(Number(d.settleResid) * 100).toFixed(1)} % left)` : ''} — η and the
          magnet/shaft loss here are start-up values
        </div>
      )}
    </Box>
  );
};

const SweepStudyPanel: React.FC = () => {
  const { sweepConfig, updateGeometryViaApi } = useMotorStore();
  const steps = readLS('sim.stepsPP', 24);   // single source: Simulation "Steps per electrical period"
  const [connectBy, setConnectBy] = useLS<'current_a' | 'gamma_deg'>('connectBy', 'current_a');
  const [withBaseline, setWithBaseline] = useLS<boolean>('withBaseline', false);
  const [selected, setSelected] = useState<any>(null);   // hand-picked best point
  const [applyMsg, setApplyMsg] = useState<string | null>(null);
  // Where the applied point was ARCHIVED (its own motor) — or why it was not.
  const [saveRes, setSaveRes] = useState<AppliedSaveResult | null>(null);
  const [zoom, setZoom] = useState<{ x: [number, number]; y: [number, number] } | null>(null);   // mouse-wheel zoom
  const [showAll, setShowAll] = useState(false);   // include the outliers the robust view clips

  // (The "reset zoom on new series" effect lives BELOW `series` — referencing it
  //  from a dep array up here hit the const TDZ and crashed the panel on open.)
  const chartBoxRef = useRef<HTMLDivElement>(null);

  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number; cached: number;
    ts_start?: number; workers?: number; s_per_eval?: number } | null>(null);
  // A ticking clock so the elapsed line moves even while the backend number
  // (done/total) stands still — a sweep point is MINUTES of silent subprocess
  // work, and a frozen line reads as a hang.
  const [nowS, setNowS] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const id = setInterval(() => setNowS(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, []);
  // Client-side liveness, independent of what the backend stamps: when the run
  // started as WE observed it, and when done/total last moved.  From those two
  // clocks the panel derives a per-point stopwatch, a measured points/second
  // rate and a bar that creeps INSIDE a point — so the screen visibly lives
  // even when the server number stands still for minutes.
  const liveRef = useRef({ t0: 0, lastDone: -1, lastDoneT: 0 });
  // Owner 2026-09-19: "нужно, чтобы автоматом это было видно после сбоя" — a
  // sweep the backend resumed after an API restart (sweep_journal.py /
  // sweep_resume.py) says so on screen without the user doing anything.  The
  // backend stamps `resumed_from_restart` on the SAME progress payload this
  // panel already polls, and keeps stamping it for as long as the resumed
  // sweep is running — a page reload re-adopts it below, in the mount effect,
  // exactly like it re-adopts `done`/`total`.
  const [resumeInfo, setResumeInfo] = useState<SweepResumeInfo | null>(null);
  // Fire the cross-tab notice ONCE per resume (keyed by its `at` stamp), not
  // on every 1.5 s poll — the rail (lib/runNotice.ts) is meant for an event,
  // not a heartbeat.
  const notifiedResumeAtRef = useRef<string | null>(null);
  const adoptProgress = (st: any) => {
    const r = st.resumed_from_restart;
    if (r && typeof r === 'object' && r.at) {
      const info: SweepResumeInfo = { at: String(r.at), done_before: Number(r.done_before) || 0,
                                      total: Number(r.total) || 0 };
      setResumeInfo(info);
      if (notifiedResumeAtRef.current !== info.at) {
        notifiedResumeAtRef.current = info.at;
        try {
          window.dispatchEvent(new CustomEvent('sim:run-notice',
            { detail: { message: sweepResumeNoticeText(info) } }));
        } catch { /* not fatal — the panel's own line still shows it */ }
      }
    } else {
      setResumeInfo(null);
    }
    const now = Date.now() / 1000;
    const lv = liveRef.current;
    if (!lv.t0) {
      // Re-entering the tab mid-run (user 2026-09-06: "вышел из sweep, зашёл
      // обратно и опять индикатор начал с начала — уже не первый раз"): the
      // in-flight points did NOT start now.  Anchor the current-point clock to
      // when they really started — the run start for the first batch, the
      // backend's measured cadence for later ones — so the bar and the ETA
      // continue instead of restarting from zero on every mount.
      const t0 = Number(st.ts_start) || now;
      const done = st.done ?? 0;
      const w = Math.max(1, Number(st.workers) || 1);
      const per = Number(st.s_per_eval) || 0;
      const batchStart = done > 0 && per > 0 ? t0 + Math.floor(done / w) * per : t0;
      lv.t0 = t0; lv.lastDone = done; lv.lastDoneT = Math.min(now, Math.max(t0, batchStart));
    }
    if ((st.done ?? 0) !== lv.lastDone) { lv.lastDone = st.done ?? 0; lv.lastDoneT = now; }
    setProgress({ done: st.done ?? 0, total: st.total ?? 1, cached: st.cached ?? 0,
                  ts_start: st.ts_start, workers: st.workers, s_per_eval: st.s_per_eval });
  };
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

  // The run this panel is waiting for.  ONE endpoint answers both "is my run
  // done?" and "here is a result", and those were only the same thing by luck:
  // the backend restores the last completed sweep at startup with running=false,
  // so the first poll after pressing Run — fired before the worker flips the
  // flag, or after a restart — read a FOREIGN result as its own.  A sweep of
  // magnet_height/rotor_hole came back showing a table of slot_height points.
  // Every result now names its run; anything else is not an answer to us.
  const runIdRef = useRef<string>('');

  /** Does a stored result belong to the machine that is loaded RIGHT NOW?
   *
   *  User 2026-09-10: "опять косяк, я запускал sweep одних параметров, а в
   *  результате получил старый sweep от другого мотора".  `.last_scan.json` is
   *  reloaded into the backend's scan state on every restart, and this panel
   *  adopted it on mount as its chart — variable cards and all — with nothing
   *  saying it was computed on a different motor.  The result now carries the
   *  fingerprint of the machine it was solved on (the SAME one the eval cache
   *  is keyed by), and `machine_now` says what is loaded; a mismatch is a
   *  foreign chart and is not shown.
   *
   *  Unstamped (a result from before this existed) is treated as foreign: a
   *  chart nobody can attribute is worth less than an empty one. */
  const sameMachine = (res: any, now: any): boolean => {
    const a = res?.machine?.fingerprint;
    const b = now?.fingerprint;
    return !!a && !!b && String(a) === String(b);
  };
  const machineLabel = (m: any): string => {
    const parts = [m?.die, m?.config, m?.duty].filter(Boolean);
    return parts.length ? parts.join(' / ') : 'another machine';
  };

  // shared poll loop — used by both run() and the resume-on-mount effect.
  const poll = async (myRunId?: string) => {
    const want = myRunId ?? runIdRef.current;
    let waitedForStart = 0;
    while (!stopRef.current) {
      await new Promise(r => setTimeout(r, 1500));
      let st: any;
      try { st = await (await fetch(`${API}/api/optimization/scan/progress`)).json(); } catch { continue; }
      const mine = !want || String(st.run_id ?? '') === want;
      if (mine) adoptProgress(st);
      if (mine && Array.isArray(st.points) && st.points.length) saveResult({ points: st.points });
      if (st.running) { waitedForStart = 0; continue; }
      if (st.error && mine) { setErr(st.error); break; }
      const res = st.result;
      const resMine = res && (!want || String(res.run_id ?? '') === want);
      if (resMine && Array.isArray(res.points)) {
        if (!sameMachine(res, st.machine_now)) {
          setErr(`the result on the backend was computed on ${machineLabel(res.machine)}`
               + ' — not this machine, so it is not shown.');
          break;
        }
        saveResult(res); break;
      }
      // Not running, and what is on the server is somebody else's run (or the
      // one restored from disk).  Do NOT display it — wait for ours to appear.
      if (++waitedForStart > 20) {
        setErr('the sweep did not start on the backend — nothing was computed, '
             + 'and the chart still shows the previous run');
        break;
      }
    }
  };

  // The VARIABLE CARDS of the last sweep come back with its result (user
  // 2026-09-04: after a crash the chart and the table were restored, but the
  // cards showed only the default γ / current — the four geometry variables of
  // the run were gone, and the next "Run sweep" would have swept nothing).
  // Only when no geometry variable is selected: a set the user has already
  // built here is never overwritten.  Ranges = the run's min/max/step;
  // γ and current follow the run's operating points.
  const [restoredNote, setRestoredNote] = useState<string | null>(null);
  const restoreVarsFromResult = (res: any) => {
    try {
      const vars = Array.isArray(res?.variables) ? res.variables : [];
      if (!vars.length) return;
      const cur = useMotorStore.getState().sweepConfig.variations as Record<string, any>;
      // Restore unless the store already carries EXACTLY the run's variable set
      // (then its ranges may be the user's edits and are kept).  The old rule —
      // "skip when ANY geometry variable is active" — let a freshly created
      // store's default variable (Cut Width 3…6 from the schema defaults) block
      // the restore of the real run (user 2026-09-07: "опять параметры
      // переменных оптимизации не восстановились после загрузки").
      const activeGeo = new Set(Object.entries(cur)
        .filter(([k, v]: any) => v?.mode !== 'fixed' && !OP_VARS.has(k)).map(([k]) => k));
      const runGeo = new Set(vars.map((v: any) => v?.name).filter((k: any) => k && !OP_VARS.has(k)));
      const sameSet = activeGeo.size === runGeo.size && [...activeGeo].every((k) => runGeo.has(k));
      if (sameSet) return;
      const next: Record<string, any> = { ...cur };
      // a default variable the run never named must not survive next to the run's
      for (const k of activeGeo) if (!runGeo.has(k)) next[k] = { ...next[k], mode: 'fixed' };
      let n = 0;
      for (const v of vars) {
        if (!v?.name || OP_VARS.has(v.name)) continue;
        const prev = next[v.name] || {};
        const lo = Number(v.min), hi = Number(v.max);
        if (!Number.isFinite(lo) || !Number.isFinite(hi)) continue;
        const step = Number(v.step) > 0 ? Number(v.step)
          : (Number(prev.step) > 0 ? Number(prev.step) : Math.max((hi - lo) / 4, 1e-3));
        next[v.name] = { ...prev, mode: 'optimize', min: lo, max: hi, step };
        n++;
      }
      const ops = Array.isArray(res?.operating_points) ? res.operating_points : [];
      for (const [key, field] of [['gamma_deg', 'gamma_deg'], ['current_a', 'current_a']] as const) {
        const vals: number[] = ops.map((o: any) => Number(o?.[field])).filter((x: number) => Number.isFinite(x));
        if (!vals.length) continue;
        const lo = Math.min(...vals), hi = Math.max(...vals);
        const uniq: number[] = [...new Set(vals.map((x: number) => Math.round(x * 1e4) / 1e4))].sort((a: number, b: number) => a - b);
        const prev = next[key] || {};
        const step = uniq.length > 1 ? Math.round((uniq[1] - uniq[0]) * 1e4) / 1e4
          : (Number(prev.step) > 0 ? Number(prev.step) : 1);
        next[key] = { ...prev, mode: hi > lo ? 'optimize' : 'fixed', min: lo, max: hi, step };
      }
      if (n > 0) {
        useMotorStore.getState().setVariations(next);
        setRestoredNote(`${n} sweep variable${n > 1 ? 's' : ''} restored from the last run`);
      }
    } catch { /* a restore is a convenience, never a blocker */ }
  };

  // On mount (page reload): resume a running scan live, or restore the LAST
  // completed sweep so the chart survives a reload (the backend keeps it in
  // memory + persisted to .last_scan.json, loaded on startup).
  useEffect(() => {
    // 1) instant: restore the last chart from localStorage (survives ANY reload).
    // (the backend's `machine_now` arrives in step 2 and re-checks this — a
    //  cached chart from another motor is dropped there.)
    try {
      const c = localStorage.getItem('sweepStudy.lastResult');
      if (c) { const r = JSON.parse(c); setResult(r); restoreVarsFromResult(r); }
    } catch { /* ignore */ }
    // 2) reconcile with the backend: resume a running scan live, or adopt its
    //    persisted result (.last_scan.json, loaded on startup).
    (async () => {
      try {
        const st = await (await fetch(`${API}/api/optimization/scan/progress`)).json();
        if (st.running) {
          // Adopt the RUNNING run's id: on a page reload this panel did not
          // start it, but it is the one whose points belong on this chart.
          runIdRef.current = String(st.run_id ?? '');
          stopRef.current = false; setRunning(true);
          if (Array.isArray(st.points)) saveResult({ points: st.points });
          adoptProgress(st);
          await poll(runIdRef.current); setRunning(false);
        } else if (st.result && Array.isArray(st.result.points)) {
          // History, not the answer to anything this session asked for — and
          // only ever THIS machine's history (see `sameMachine`).
          if (sameMachine(st.result, st.machine_now)) {
            runIdRef.current = String(st.result.run_id ?? '');
            saveResult(st.result);
            restoreVarsFromResult(st.result);
          } else {
            setResult(null);
            try { localStorage.removeItem('sweepStudy.lastResult'); } catch { /* ignore */ }
            setErr(`the stored sweep was computed on ${machineLabel(st.result.machine)}`
                 + ' — not this machine, so it is not shown. Run the sweep to get one.');
          }
        }
      } catch { /* ignore */ }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const run = async () => {
    setErr(null); setResult(null); setRunning(true); setProgress(null); stopRef.current = false;
    // A user-started run is never a resume — the backend clears its own
    // `resumed_from_restart` on a fresh /scan POST, and the panel must not go
    // on showing last run's resume notice over this one.
    setResumeInfo(null); notifiedResumeAtRef.current = null;
    // fresh liveness clocks: this run starts NOW as far as the user can see
    liveRef.current = { t0: Date.now() / 1000, lastDone: 0, lastDoneT: Date.now() / 1000 };
    // UNIQUE per launch.  It used to be `sweep_${nPts}`, which two runs of the
    // same size share — so even a stamped result could not tell them apart.
    const myRunId = `sweep_${nPts}_${Date.now()}`;
    runIdRef.current = myRunId;
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
          // the un-varied current motor as an extra reference point — one more
          // full eval, off by default (user 2026-09-04: always stopped at 22/23)
          with_baseline: withBaseline,
          mesh_size_mm: readLS('mesh.meshSize', 4), min_size_mm: readLS('mesh.minSize', 0.3),
          pole_copy: readBool('mesh.poleCopy', false), torque_filter: readBool('sim.torqueFilter', false),
          n_sectors: Math.max(1, Math.round(readLS('mesh.nSectors', 1))),   // single source: Mesh tab (same as Simulation)
          gap_layers: readLS('mesh.gapLayers', 2),   // single source: Mesh tab — drives ripple/eddy; must match Simulation
          structured_gap: readBool('mesh.structuredGap', false) || readBool('mesh.ironTemplate', true),   // single source: Mesh tab "Structured" — belt gap mesh (honest ripple, ¼ == full disk)
          airgap_macro: readBool('mesh.harmonicGap', false),   // Mesh tab "Harmonic gap" — step-independent RAW ripple (full + sectors)
          // P2 — the only basis. The sweep must rank designs on the SAME basis
          // the Simulation tab reports, or the winner it picks is not the
          // design you then look at.
          element_order: 2,
          iron_template: readBool('mesh.ironTemplate', true), // Mesh tab "Template iron" — deterministic iron mesh
          geo_mesh: readBool('mesh.geoMesh', true),   // Mesh tab "Geometry-driven mesh" — SAME build as Simulation (cell-tiled iron)
          end_winding_factor: readLS('sim.endWinding', 0),   // sent for parity, but a sweep RECOMPUTES k_end per-point from each candidate's geometry (backend refine_proc forces auto) — you can't pin one k_end across changing tooth_width/slot geometry
          rotor_eddy: readBool('sim.fieldLosses', true),   // single source: Simulation — field vs slab magnet eddy (drives eff)
          // Irreversible demagnetisation — single source: Simulation. It de-rates
          // Br, so a sweep without it ranks machines the Simulation tab is not
          // showing. Costs a whole extra period of frames per point.
          demag: readBool('sim.demag', false),
          run_id: myRunId,
          // Consent to vary a DIE-DEFINING key under an active die (the
          // Optimize header's checkbox); without it the backend answers 422
          // with the reason instead of starting a run whose apply would
          // release the die context (2026-09-20).
          allow_new_lamination: readAllowNewLamination(),
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${(await res.text()).slice(0, 400)}`);
      await poll(myRunId);
    } catch (e: any) { setErr(String(e?.message ?? e)); }
    finally { setRunning(false); }
  };

  const cancel = async () => {
    stopRef.current = true;
    try { await fetch(`${API}/api/optimization/scan/cancel`, { method: 'POST' }); } catch { /* ignore */ }
  };

  // Wipe the displayed result + its localStorage copy AND the backend FEM eval
  // cache, so the next "Run sweep" recomputes every point from scratch.  Without
  // the cache wipe, an identical grid (same variables + mesh + operating points)
  // returns the cached points instantly — which reads as "Run took the old values
  // and didn't recompute".  Clear result = explicit "start fresh".
  const clearResult = async () => {
    setResult(null); setSelected(null); setProgress(null); setZoom(null);
    try { localStorage.removeItem('sweepStudy.lastResult'); } catch { /* ignore */ }
    try { await fetch(`${API}/api/optimization/scan/clear_cache`, { method: 'POST' }); }
    catch { /* ignore — display is already cleared */ }
  };

  // Group feasible points and join them along the chosen variable (current or γ).
  const k3dChart = readApply3dK();
  const series = useMemo(() => {
    const pts: any[] = result?.points || [];
    const byCurrent = connectBy !== 'gamma_deg';   // connect along current (group per γ) or along γ (group per current)
    const groups = new Map<string, { label: string; rows: any[] }>();
    const omegaNow = 2 * Math.PI * readLS('sim.rpm', 4000) / 60;
    for (const p of pts) {
      if (!p.feasible || p.torque_per_mass_Nm_kg == null) continue;
      const gam = typeof p.gamma_deg === 'number' ? p.gamma_deg : (sentOps[p.op_index]?.gamma_deg ?? 0);
      const cur = p.current_a;
      const gi = p.geom_id ?? 0;
      const key = byCurrent ? `${gi}|g${gam}` : `${gi}|i${cur}`;
      const label = byCurrent ? `γ=${gam}°${gi ? ` g${gi}` : ''}` : `I=${cur}A${gi ? ` g${gi}` : ''}`;
      // 3-D view of the point (same k and rule as the table and the Simulation
      // summary); the solver's 2-D numbers stay under raw2d for applyPoint.
      const P2 = p.P_mech_W != null ? Number(p.P_mech_W) / 1000
                                    : (Number(p.T_em_Nm) || 0) * omegaNow / 1000;
      const massN = Number(p.mass_total_kg) || 0;
      const pd2 = p.power_per_mass_W_kg != null ? Number(p.power_per_mass_W_kg) / 1000
                                               : (massN > 0 ? P2 / massN : 0);
      const s3 = scale3d(k3dChart, Number(p.T_em_Nm) || 0, P2, Number(p.V_peak) || 0,
                         Number(p.V_line_peak_V) || 0, Number(p.KV_rpm_per_V_line) || 0,
                         Number(p.torque_per_mass_Nm_kg) || 0, pd2,
                         (p.efficiency ?? 0) * 100, Number(p.P_loss_total_W) || 0);
      const row = { x: s3.td, y: s3.eff,
                     I: cur, g: gam, gi, ripple: p.T_ripple_pct, T: s3.T,
                     apply_eligible: p.apply_eligible === true,
                    overrides: p.overrides || {}, _c: byCurrent ? cur : gam,
                    // Full per-design metrics so applying this point shows the
                    // sweep's already-computed numbers in Simulation — no re-run.
                    // Terminal values ride under their backend names so applyPoint
                    // pushes the SOLVED line peak / KV / P_mech, not sinusoid guesses.
                    eff: s3.eff, mass: p.mass_total_kg,
                    td: s3.td, Vpk: s3.Vpk,
                    V_line_peak_V: s3.Vl, KV_rpm_per_V_line: s3.KV,
                    P_mech_W: s3.P * 1000, power_per_mass_W_kg: s3.pd * 1000,
                    raw2d: { T: p.T_em_Nm, P_mech_W: p.P_mech_W, Vpk: p.V_peak,
                             V_line_peak_V: p.V_line_peak_V, KV_rpm_per_V_line: p.KV_rpm_per_V_line,
                             td: p.torque_per_mass_Nm_kg, power_per_mass_W_kg: p.power_per_mass_W_kg,
                             eff: (p.efficiency ?? 0) * 100 },
                    k3d: k3dChart,
                    rpm: p.rpm,   // absent today (refine result omits it) — future-proof
                    ploss: p.P_loss_total_W, core: p.P_fe_W,
                    stranded: p.P_cu_W, solid: (p.P_mag_W ?? 0) + (p.P_shaft_W ?? 0),
                    // Eddy-settle verdict — the marker is drawn HOLLOW when the
                    // point's own solve says its σ·∂A/∂t transient was still
                    // running (2026-09-07), because η on the Y axis and the loss
                    // it comes from are then start-up values.
                    settled: typeof p.eddy_settled === 'boolean' ? p.eddy_settled : undefined,
                    settleResid: p.eddy_settle_residual ?? null,
                    // SAME stable key as the table row (geometry, operating-point)
                    // so point↔row selection cross-highlights reliably.
                    pk: `g${gi}_o${p.op_index ?? 0}` };
      if (!groups.has(key)) groups.set(key, { label, rows: [] });
      groups.get(key)!.rows.push(row);
    }
    return [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0]))
      .map(([key, g]) => ({ key, label: g.label, rows: g.rows.sort((a, b) => a._c - b._c) }));
  }, [result, sentOps, connectBy, k3dChart]);

  // A stale zoom window survives into the NEXT result set and shows a sliver
  // of the new data ("а где график?" — the curves ran off the clipped view).
  // New series → full view, always.
  useEffect(() => { setZoom(null); setShowAll(false); }, [series]);

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

  // ── UNSETTLED EDDY TRANSIENT (user's 90-point sweep, 2026-09-07) ─────────
  // Every point of that sweep spent 57 warm-up frames and nothing in the result
  // said whether they were ENOUGH.  They were not on the designs whose shaft
  // tube sits closest to the field (a shorter magnet pushes rotor_inner_radius
  // outward): shaft eddy loss read 504 W at gap 2.6 mm, 3558 W at 3.1 mm and
  // 6652 W at 1.6 mm with the magnet fixed at 24 mm — start-up transients, not
  // physics, and they drove η and P_loss on this very chart.  They were read as
  // demagnetisation.  So: counted, flagged, and drawn hollow.
  const unsettled = useMemo(() => {
    const pts: any[] = result?.points || [];
    const bad = pts.filter(p => p && p.feasible && p.eddy_settled === false);
    if (!bad.length) return null;
    const rs = bad.map(p => Number(p.eddy_settle_residual)).filter(v => Number.isFinite(v));
    const said = pts.filter(p => p && p.feasible && typeof p.eddy_settled === 'boolean').length;
    return { n: bad.length, total: said || pts.length,
             worst: rs.length ? Math.max(...rs) * 100 : null,
             capped: bad.filter(p => p.eddy_capped).length };
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

  // ONE runaway point (a design that lands at 0.2 N·m/kg while the rest sit
  // around 5) sets the axis for everybody: the other 20 collapse into a dot in
  // the corner and the sweep is unreadable.  So the DEFAULT view is the robust
  // range (Tukey, q1/q3 ± 1.5·IQR) and the outliers are clipped, not deleted —
  // the chip below says how many are outside and puts the full view one click
  // away.  Only used when it actually changes something: an extent that is not
  // at least 1.5x wider than the robust box is shown whole.
  const robust = useMemo(() => {
    const xs: number[] = [], ys: number[] = [];
    series.forEach(s => s.rows.forEach((r: any) => { xs.push(r.x); ys.push(r.y); }));
    if (xs.length < 5 || !extent) return null;
    const box = (v: number[]): [number, number] => {
      const s = [...v].sort((a, b) => a - b);
      const q = (f: number) => s[Math.min(s.length - 1, Math.max(0, Math.round(f * (s.length - 1))))];
      const q1 = q(0.25), q3 = q(0.75), iqr = q3 - q1;
      const lo = Math.max(s[0], q1 - 1.5 * iqr), hi = Math.min(s[s.length - 1], q3 + 1.5 * iqr);
      const d = (hi - lo) || Math.abs(hi) * 0.05 || 1;
      return [lo - d * 0.05, hi + d * 0.05];
    };
    const bx = box(xs), by = box(ys);
    const tighter = (b: [number, number], e: [number, number]) => (e[1] - e[0]) > 1.5 * (b[1] - b[0]);
    if (!tighter(bx, extent.x) && !tighter(by, extent.y)) return null;   // no runaway — show everything
    return { x: tighter(bx, extent.x) ? bx : extent.x, y: tighter(by, extent.y) ? by : extent.y };
  }, [series, extent]);

  // The window the axes actually use: an explicit wheel-zoom wins, else the
  // robust view, else the full extent.
  const view = zoom ?? ((!showAll && robust) ? robust : extent);
  // The wheel listener is attached once per extent; this keeps it reading the
  // window currently on screen without re-attaching on every zoom step.
  const viewRef = useRef(view);
  viewRef.current = view;
  const nOutside = useMemo(() => {
    if (!view || (!robust && !zoom)) return 0;
    let n = 0;
    series.forEach(s => s.rows.forEach((r: any) => {
      if (r.x < view.x[0] || r.x > view.x[1] || r.y < view.y[0] || r.y > view.y[1]) n++;
    }));
    return n;
  }, [series, view, robust, zoom]);

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
      // A PLAIN wheel over the PLOT AREA zooms (Ctrl+wheel too) — requiring Ctrl
      // read as "the zoom is broken", which is worse than the problem the gate
      // was for.  Outside the plot rectangle (axis labels, the margins) the
      // wheel still scrolls the page, and a zoomed view always shows the
      // "zoomed — reset view" chip; double-click resets as well.
      const box = el.getBoundingClientRect();
      const padL = 54, padR = 24, padT = 8, padB = 40;   // ≈ plot area inside axes/margins
      const px = e.clientX - box.left, py = e.clientY - box.top;
      if (px < padL || px > box.width - padR || py < padT || py > box.height - padB) return;
      e.preventDefault();
      const fx = Math.min(1, Math.max(0, (px - padL) / Math.max(1, box.width - padL - padR)));
      const fy = Math.min(1, Math.max(0, (py - padT) / Math.max(1, box.height - padT - padB)));
      const f = e.deltaY < 0 ? 0.85 : 1 / 0.85;
      setZoom(prev => {
        // Zoom out from whatever is on screen — the robust view, not the full
        // extent, or the first notch would jump back to the outlier's scale.
        const cur = prev ?? viewRef.current ?? extent;
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
    // Scan results use the 3× screening policy. A missing stamp (old saved
    // result) also fails closed; never mutate live geometry or archive it.
    if (p.apply_eligible !== true) {
      setApplyMsg('This sweep point is preliminary (3× raw sampling). A standard 6× verification is required before Apply.');
      return;
    }
    setApplyMsg('applying…'); setSaveRes(null);
    try {
      // updateGeometryViaApi resolves normally even on a 422/423/500 refusal
      // (it never throws for those — see lib/geometryApplyOutcome.ts) so the
      // ONLY way to know the picked geometry actually landed is to read what
      // it returns.  Root cause of the 2026-09-19 report: a locked die/
      // configuration refused tooth_width/magnet_fill_up (423) and this
      // function still said "✓ applied" for the whole design — only the
      // operating point (the PATCH below, which no lock touches) had moved.
      let geomOutcome: { ok: boolean; refused: { field: string; reason: string }[] } | null = null;
      if (p.overrides && Object.keys(p.overrides).length) geomOutcome = await updateGeometryViaApi(p.overrides);
      await fetch(`${API}/api/simulation/config`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ max_current: p.I, phase_offset_deg: p.g }),
      });
      window.dispatchEvent(new CustomEvent('sim-operating-point', { detail: { current: p.I, gamma: p.g } }));
      // The sweep already computed this design's full FEM result — push those
      // exact numbers into the Simulation summary so it shows them immediately,
      // without a re-run (the user picks on these very numbers).
      try {
        // Scan points don't stamp their rpm yet (the refine result omits it), so
        // the CURRENT sim.rpm stands in — correct unless the user changed rpm
        // after launching the sweep.  `p.rpm` wins the moment the backend adds it.
        const rpm = Number(p.rpm) || readLS('sim.rpm', 4000);
        const omega = 2 * Math.PI * rpm / 60;
        // The SOLVER's 2-D numbers (raw2d) go into the summary; the Simulation
        // summary applies the machine's 3-D factor itself when its button is on,
        // so the pushed row lands on screen with exactly the numbers the sweep
        // table showed — carried once, never twice.
        const r2 = p.raw2d || p;
        const T = Number(r2.T) || 0, mass = Number(p.mass) || 0;
        const Vpk = Number(r2.Vpk) || 0;
        // SOLVED terminal values first, synthesis only as fallback.  Vpk·√3
        // over-reads the line-line peak: the triplen harmonics of the phase
        // waveform cancel line-to-line, so the measured max|Va−Vb| the backend
        // reports is LESS than √3× the phase peak.  The /√2 rms values are
        // sinusoid approximations (the refine result carries peaks only).
        const Vlpk = Number(r2.V_line_peak_V) || Vpk * Math.sqrt(3);
        const Vrms = Vpk / Math.SQRT2, Vlrms = Vlpk / Math.SQRT2;
        const Pmech = Number(r2.P_mech_W) || T * omega;
        const ploss = Number(p.ploss) || 0;
        const summary = {
          ...(p.k3d != null ? { end3d: { k_flux: p.k3d, source: 'machine passport, carried from the sweep view' } } : {}),
          rpm, I_phase_rms_A: Number(p.I) || 0, gamma_deg: Number(p.g) || 0,
          T_em_avg_Nm: T, T_ripple_pct: Number(p.ripple) || 0, P_mech_W: Pmech,
          V_phase_peak_V: Vpk, V_phase_rms_V: Vrms, V_line_peak_V: Vlpk, V_line_rms_V: Vlrms,
          // KV = rpm / V_PEAK — the max/max convention the Simulation tile and the
          // user's Ansys table use (see refine_proc / simulation.py, 2026-08-04);
          // dividing by rms read ~√2 high and contradicted a by-hand check.
          KV_rpm_per_V_phase: Vpk > 1 ? rpm / Vpk : 0,
          KV_rpm_per_V_line: Number(r2.KV_rpm_per_V_line) || (Vlpk > 1 ? rpm / Vlpk : 0),
          P_loss_total_W: ploss, P_core_W: Number(p.core) || 0,
          P_stranded_W: Number(p.stranded) || 0, P_solid_W: Number(p.solid) || 0,
          efficiency: (Number(r2.eff) || 0) / 100, mass_total_kg: mass, mass_components: [],
          torque_per_mass_Nm_kg: Number(r2.td) || (mass > 0 ? T / mass : 0),
          power_per_mass_W_kg: Number(r2.power_per_mass_W_kg) || (mass > 0 ? Pmech / mass : 0),
          loss_density_W_kg: mass > 0 ? ploss / mass : 0,
        };
        window.dispatchEvent(new CustomEvent('sim-apply-summary', { detail: { summary } }));
      } catch { /* summary is best-effort — geometry + operating point already applied */ }
      // The applied geometry moved k_end; refresh it BEFORE the recompute so the
      // copper loss isn't scaled by the previous design's end-turn length.
      try {
        const dc = await (await fetch(`${API}/api/config`)).json();
        const k = Number(dc?.end_winding_factor);
        if (Number.isFinite(k) && k > 0) localStorage.setItem('sim.endWinding', JSON.stringify(+k.toFixed(3)));
      } catch { /* non-fatal — the Simulation panel re-seeds on the geometry change */ }
      // NO recompute.  This point is already solved — the summary pushed above IS
      // its FEM result, at the settings it was computed with.  Re-solving it cost
      // minutes and then overwrote those numbers with a run at the Simulation
      // tab's own settings; press Run there when you want waveforms, fields or an
      // independent check, and the panel will report the delta.
      try {
        window.dispatchEvent(new CustomEvent('sim-design-applied'));
      } catch { /* SSR/no-window */ }
      const ovStr = Object.entries(p.overrides || {}).map(([k, v]) => `${k}=${v}`).join(', ');
      // Same unit the card above prints ("now: … A peak (from Simulation)") —
      // the raw stored Arms value here used to read "I = 49.4975 A" right next
      // to a card reading "70 A peak" for the identical operating point.
      const iShown = formatCurrent(Number(p.I) || 0, readCurrentUnit());
      if (geomOutcome && !geomOutcome.ok) {
        const names = geomOutcome.refused.map(r => r.field).join(', ') || 'the swept geometry';
        setApplyMsg(`⚠ operating point applied (I=${iShown} · γ=${p.g}°) — geometry NOT applied: `
          + `${names} refused by the active die/configuration lock. Unlock it in the Motors `
          + `catalog (admin) or load another configuration, then apply again.`);
      } else {
        setApplyMsg(`✓ applied${ovStr ? ': ' + ovStr : ' (base geometry)'} · I=${iShown} · γ=${p.g}° `
          + `— its own FEM numbers are shown in Simulation; Run there only for waveforms or a re-check`);
      }
      // ARCHIVE IT — a picked sweep design is applied into the editor and would
      // otherwise live only there until someone remembered to save it.  New
      // motor, never the source one; the failure (if any) is shown, not swallowed.
      setSaveRes(await autoSaveAppliedDesign({
        mode: 'sweep',
        runId: String(result?.run_id ?? ''),
        operatingPoint: { current_a: Number(p.I), gamma_deg: Number(p.g),
                          rpm: readLS('sim.rpm', 4000) },
        metrics: { T_avg_Nm: p.T, T_ripple_pct: p.ripple, efficiency: p.eff,
                   torque_per_mass: p.td, mass_total_kg: p.mass },
        overrides: p.overrides || {},
      }));
    } catch (e: any) { setApplyMsg('✗ apply FAILED (' + String(e?.message ?? e) + ') — nothing was changed; try again'); }
  };

  // kU converts STORED values (Arms for current) into the DISPLAY unit — the
  // range card above speaks peak by default, and this summary line used to
  // print the raw stored Arms next to it (a 30…35 peak range read as
  // "21.213203435596423 … 24.74873…"), which looked like a different sweep.
  const VarLine: React.FC<{ on: boolean; label: string; v: any; fixedVal: number;
                            kU?: number; unit?: string }> =
      ({ on, label, v, fixedVal, kU = 1, unit = '' }) => {
    const f = (x: any) => Number((Number(x) * kU).toFixed(3));
    return (
      <Typography sx={{ fontSize: 11, color: on ? 'var(--text-1)' : 'var(--text-4)', mb: 0.2 }}>
        • <strong>{label}</strong>: {on
          ? <>{f(v.min)} … {f(v.max)} step {f(v.step)}{unit} <span style={{ color: 'var(--text-3)' }}>({buildGrid(Number(v.min), Number(v.max), Number(v.step)).length} pts)</span></>
          : <>{f(fixedVal)}{unit} <span style={{ color: 'var(--text-4)' }}>(fixed — add it to the variables above to sweep)</span></>}
      </Typography>
    );
  };
  // same unit choice the range card persists ('arms' only when explicitly picked)
  const iPeak = (() => { try { return localStorage.getItem('sweep.iUnit') !== 'arms'; } catch { return true; } })();

  return (
    <Box sx={{ mt: 2 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 1.25 }}>
        <SectionLabel>Sweep study — efficiency vs torque/mass</SectionLabel>
        <HelpTip title="Sweeps the variables you added above (current/γ → operating points, geometry → a grid). Each point is a real transient built exactly like Simulation — sector, gap mesh and the Structured (belt) toggle all come from the Mesh tab. For honest ripple use Structured; ¼ sector then matches the full disk and is ~4× faster." />
      </Box>

      <Box sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1, p: 1, mb: 1.25 }}>
        <VarLine on={!!cV} label="Phase current" v={cV} fixedVal={readLS('sim.current', 85)}
                 kU={iPeak ? Math.SQRT2 : 1} unit={iPeak ? ' A peak' : ' Arms'} />
        <VarLine on={!!gV} label="Load angle γ" v={gV} fixedVal={readLS('sim.gamma', 0)} />
        {geomVars.map(([name, v]) => (
          <Typography key={name} sx={{ fontSize: 11, color: 'var(--text-1)', mb: 0.2 }}>
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
        <Typography sx={{ fontSize: 11, color: nPts > 40 ? '#fca5a5' : 'var(--text-3)', flex: 1 }}>
          <strong>{nPts}</strong> pt{nPts === 1 ? '' : 's'}{nPts > 40 ? ' — large, slow' : ''}
          {/* SAY which step count the sweep will solve at.  It is read from the
              Electromagnetic tab's key at Run time, and that key can be rewritten
              behind the tab's back (an optimizer Apply pins the run's 48 steps into
              it; a second window does not see it).  2026-09-07: the tab said 36,
              the sweep ran 48 — cold, 3× longer — and nothing on screen said so. */}
          <span title={`Steps per electrical period, taken from the Electromagnetic tab (sim.stepsPP). A sweep at a different step count than the last Electromagnetic run cannot reuse its warm state: every point solves cold (demag pre-pass + eddy warm-up, ~3x longer). Change it on the Electromagnetic tab.`}
            style={{ marginLeft: 8, color: 'var(--text-3)', cursor: 'help' }}>
            · {steps} steps/period
          </span>
          {/* The 3-D end-effect view, same switch and factor as the Simulation
              summary ("3D" button) — so a current scaled off this chart lands on
              the same shaft power over there (2026-09-08: 500 → 487.46 kW). */}
          {k3dChart != null && (
            <span title={`Torque, power, voltages and the densities are multiplied by the machine's 3-D end-effect factor k_flux = ${k3dChart.toFixed(4)}, exactly as the Simulation summary does while its "3D" button is on (losses stay 2-D, η is recomputed). Switch the button off in Simulation to see the raw 2-D solver numbers here too.`}
              style={{ marginLeft: 8, color: 'var(--text-3)', cursor: 'help' }}>
              · 3D ×{k3dChart.toFixed(3)}
            </span>
          )}
          {' '}
          <label title="Also solve the current motor un-varied at operating point 0 as a reference point on the chart — one more full FEM eval, run last. Off: the sweep ends with its grid."
            style={{ cursor: 'pointer', color: 'var(--text-3)', whiteSpace: 'nowrap' }}>
            <input type="checkbox" checked={withBaseline} onChange={e => setWithBaseline(e.target.checked)}
              style={{ verticalAlign: 'middle', marginLeft: 6 }} /> baseline point (preliminary 3×)
          </label>
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
            sx={{ textTransform: 'none', fontSize: 11, color: 'var(--text-2)', minWidth: 0 }}>
            Clear result
          </Button>
        )}
        {restoredNote && !running && (
          <Typography component="span" sx={{ fontSize: 11, color: '#34d399' }}
            title="The variable cards above were re-added from the last sweep's own record (min / max / step and its γ / current points) because none were selected after the reload.">
            {restoredNote}
          </Typography>
        )}
        {running && (() => {
          // All clocks below tick every second from CLIENT time — a sweep point
          // is minutes of silent subprocess FEM, and a frozen line reads as a
          // hang.  Backend stamps (ts_start/workers/s_per_eval) only sharpen
          // the estimates when the API provides them.
          const lv = liveRef.current;
          const done = progress?.done ?? 0;
          const total = Math.max(1, progress?.total ?? 1);
          const w = Math.max(1, progress?.workers ?? 1);
          const t0 = (progress?.ts_start || lv.t0) || nowS;
          const el = Math.max(0, nowS - t0);                       // whole-run stopwatch
          const sinceLast = Math.max(0, nowS - (lv.lastDoneT || nowS)); // current-point stopwatch
          // points per second: measured from THIS run once a point landed,
          // else from the backend's measured s/point rate.  Cache-reused points
          // arrive for free at t=0 — counting them would fake a blazing rate
          // and a bogus ETA, so only points actually SOLVED here count.
          const solved = Math.max(0, done - (progress?.cached ?? 0));
          const rate = solved > 0 && el > 1 ? solved / el
                     : progress?.s_per_eval ? w / progress.s_per_eval : 0;
          const left = rate > 0 ? Math.max(0, (total - done) / rate - sinceLast) : null;
          const mmss = (t: number) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;
          return (
            <>
              <Box sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: '#3b82f6', flex: 'none',
                animation: 'sweep-pulse 1.4s ease-in-out infinite',
                '@keyframes sweep-pulse': {
                  '0%, 100%': { opacity: 0.25, transform: 'scale(0.75)' },
                  '50%': { opacity: 1, transform: 'scale(1.2)' },
                } }} />
              <Typography sx={{ fontSize: 11, color: 'var(--text-3)', fontVariantNumeric: 'tabular-nums' }}>
                {done}/{total}
                {progress?.cached ? ` · ${progress.cached} reused` : ''}
                {` · solving ${mmss(el)}`}
                {done > 0 && sinceLast >= 1 && ` · this point ${mmss(sinceLast)}`}
                {progress?.workers ? ` · ${progress.workers} workers` : ''}
                {rate > 0 && ` · ~${Math.round(w / rate)} s/point`}
                {left != null && left > 0 && ` · ~${mmss(left)} left`}
              </Typography>
            </>
          );
        })()}
        {err && <Typography sx={{ fontSize: 11, color: '#fca5a5' }}>✗ {err}</Typography>}
      </Box>
      {running && (() => {
        const lv = liveRef.current;
        const done = progress?.done ?? 0;
        const total = Math.max(1, progress?.total ?? 1);
        const w = Math.max(1, progress?.workers ?? 1);
        const t0 = (progress?.ts_start || lv.t0) || nowS;
        const el = Math.max(0, nowS - t0);
        const sinceLast = Math.max(0, nowS - (lv.lastDoneT || nowS));
        const solved = Math.max(0, done - (progress?.cached ?? 0));
        const rate = solved > 0 && el > 1 ? solved / el
                   : progress?.s_per_eval ? w / progress.s_per_eval : 0;
        if (!progress || rate <= 0) {
          return <LinearProgress variant="indeterminate" sx={{ mb: 1.5, height: 4, borderRadius: 2 }} />;
        }
        // The bar CREEPS inside a point: estimated in-flight work since the last
        // landed point, saturating at 90% of the remaining gap so it can never
        // claim a point that has not actually finished.  The buffer marks the
        // points currently on workers; MUI animates the region beyond it.
        const creep = Math.min((total - done) * 0.9, sinceLast * rate * 0.9);
        const pct = Math.min(99.5, (100 * (done + creep)) / total);
        const buf = Math.min(100, (100 * Math.min(total, done + w)) / total);
        return <LinearProgress variant="buffer" value={pct} valueBuffer={buf}
          sx={{ mb: 1.5, height: 4, borderRadius: 2 }} />;
      })()}

      {/* One short line, kept up for as long as the resumed sweep is running
          (a page reload re-adopts it — see adoptProgress above) — gone once
          the sweep finishes or the user starts a fresh one. */}
      {running && resumeInfo && (
        <Typography component="div" sx={{ fontSize: 11, color: '#60a5fa', mb: 1,
          display: 'flex', alignItems: 'center', gap: 0.5 }}>
          {sweepResumeNoticeText(resumeInfo)}
          <HelpTip title="The API restarted while this sweep was running. The points already computed were kept and reused from cache; the rest are being solved now." />
        </Typography>
      )}

      {/* …or when every point failed: a sweep whose points all timed out plotted
          NOTHING and said nothing — the user asked "where is the sweep result?"
          and the answer (10 of 10 timed out) was on screen nowhere. */}
      {(running || series.length > 0 || (result?.points?.length ?? 0) > 0) && (
        <>
          <Divider sx={{ borderColor: 'var(--panel)', my: 1 }} />
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
            <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
              {running
                ? `computing… ${nFeasible} point${nFeasible === 1 ? '' : 's'} so far`
                : nFeasible === 0
                  ? `no point survived — ${result?.points?.length ?? 0} evaluated, none usable (see below)`
                  : `${nFeasible} feasible point${nFeasible === 1 ? '' : 's'} · ${series.length} curve${series.length === 1 ? '' : 's'} · click a point to pick & save it`}
            </Typography>
            {/* A zoomed view MUST say so and offer the way back — double-click
                was the only reset and nothing on screen mentioned it. */}
            {zoom && (
              <Chip size="small" onClick={() => setZoom(null)} onDelete={() => setZoom(null)}
                label="zoomed — reset view"
                sx={{ height: 20, fontSize: 10, color: '#f59e0b',
                      border: '1px solid rgba(245,158,11,0.6)', bgcolor: 'transparent' }} />
            )}
            {/* Clipped points are never silently dropped: say how many and let
                one click widen the axes to include them. */}
            {!zoom && nOutside > 0 && (
              <Chip size="small" onClick={() => setShowAll(true)}
                label={`${nOutside} outlier${nOutside === 1 ? '' : 's'} off view — show all`}
                title="The axes follow the bulk of the points (q1/q3 ± 1.5·IQR); click to include the outliers"
                sx={{ height: 20, fontSize: 10, color: '#f59e0b', cursor: 'pointer',
                      border: '1px solid rgba(245,158,11,0.6)', bgcolor: 'transparent' }} />
            )}
            {!zoom && showAll && robust && (
              <Chip size="small" onClick={() => setShowAll(false)}
                label="all points — fit to bulk"
                sx={{ height: 20, fontSize: 10, color: 'var(--text-3)', cursor: 'pointer',
                      border: '1px solid var(--panel)', bgcolor: 'transparent' }} />
            )}
          </Box>
          {buildFails && (
            <Typography sx={{ fontSize: 11, color: '#fbbf24', mb: 0.5, lineHeight: 1.4 }}>
              ⚠ {buildFails.n} of {buildFails.total} design{buildFails.n === 1 ? '' : 's'}{' '}
              {/^timeout/i.test(buildFails.reason)
                ? 'ran out of time — the eval budget was reached before the solve finished. '
                  + 'These settings (steps/period, demag, coupled eddy, mesh size) cost far more '
                  + 'per point than a plain sweep; lower them or expect long runs.'
                : /non-physical/i.test(buildFails.reason)
                  // The geometry built and solved fine — the RESULT was rejected
                  // (η = 0, absurd Nm/kg …). Blaming the build sent the user
                  // hunting a geometry problem when the operating point was the
                  // culprit (seen live: a generator sweep at γ≈0, i.e. the
                  // zero-torque angle — every point honestly measured η = 0).
                  ? `solved but the result was rejected (${buildFails.reason}) — `
                    + 'skipped. The geometry is fine; check the operating point '
                    + '(γ near the zero-torque angle, current far off the machine) '
                    + 'or the mode (γ counts from the q-axis in BOTH modes — '
                    + 'generator adds its 180° internally).'
                  : `couldn’t be built${buildFails.reason ? ` (${buildFails.reason})` : ' (invalid geometry)'} — skipped.`}
              {buildFails.culprits.length
                ? ` Always fails at: ${buildFails.culprits.join('; ')} — narrow that range.`
                : ''}
            </Typography>
          )}
          {/* ONE LINE + tooltip (project rule: no text walls).  Hollow markers
              on the chart, ✗ in the table's `eddy` column, the number here. */}
          {unsettled && (
            <Typography sx={{ fontSize: 11, color: '#fbbf24', mb: 0.5, lineHeight: 1.4,
                              display: 'flex', alignItems: 'center', gap: 0.5 }}>
              <span>
                ⚠ {unsettled.n} point{unsettled.n === 1 ? '' : 's'} did not settle the eddy
                transient (shaft/magnet eddy losses and efficiency of those points are
                start-up values){unsettled.worst != null
                  ? ` — worst residual ${unsettled.worst.toFixed(1)} %` : ''} — shown hollow.
              </span>
              <HelpTip title={`The coupled σ·∂A/∂t solve marches discarded frames at θ<0 until `
                + `the solid-conductor loss stops decaying (tolerance 2 % of the settled level, `
                + `Aitken-extrapolated over 6th-harmonic ripple blocks). `
                + `${unsettled.capped} of these ${unsettled.n} ended at the hard cap — one whole `
                + `electrical period, the longest single extension the march is allowed — with `
                + `the transient still running, so their magnet/shaft eddy watts, P_loss and η `
                + `are over-read by roughly the residual — that is where the decaying current `
                + `sits; mass is untouched, torque and ripple only through the field it perturbs. `
                + `Fewer of them: raise steps/period, or re-run the neighbourhood so each point `
                + `starts from a closer settled state.`} />
            </Typography>
          )}
          <Box ref={chartBoxRef} onDoubleClick={() => setZoom(null)} sx={{ height: 340 }}>
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ top: 8, right: 24, left: 8, bottom: 24 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--panel)" />
                <XAxis type="number" dataKey="x" name="Nm/kg" tick={{ fontSize: 10 }}
                  domain={view ? (zoom && extent ? clampDom(zoom.x, extent.x) : view.x) : ['auto', 'auto']}
                  allowDataOverflow={!!zoom || view !== extent}
                  tickFormatter={(v: number) => v.toFixed(2)}
                  label={{ value: 'Torque / mass (N·m/kg)', position: 'insideBottom', offset: -12, fontSize: 11, fill: 'var(--text-3)' }} />
                <YAxis type="number" dataKey="y" name="Eff %" tick={{ fontSize: 10 }} width={52}
                  domain={view ? (zoom && extent ? clampDom(zoom.y, extent.y) : view.y) : ['auto', 'auto']}
                  allowDataOverflow={!!zoom || view !== extent}
                  tickFormatter={(v: number) => v.toFixed(2)}
                  label={{ value: 'Efficiency (%)', angle: -90, position: 'insideLeft', fontSize: 11, fill: 'var(--text-3)' }} />
                <Tooltip content={<SweepTooltip />} />
                {/* Legend removed — the table below lists I/γ per design, and
                    selecting a row highlights its point (and vice-versa). */}
                {series.map((s, i) => (
                  // key includes the current selection so Recharts re-renders the
                  // custom point shapes when a TABLE ROW is picked (external state
                  // change) — otherwise the highlighted point only updates on a
                  // direct chart click.
                  <Scatter key={`${s.key}|${(selected as any)?.pk ?? ''}`} name={s.label} data={s.rows}
                    fill={GCOL[i % GCOL.length]}
                    line={{ stroke: GCOL[i % GCOL.length], strokeWidth: 1.5 }}
                    lineJointType="monotoneX" isAnimationActive={false} cursor="pointer"
                    shape={(p: any) => {
                      const sel = selected && p.payload?.pk === (selected as any).pk;
                      // HOLLOW = the point's eddy transient never settled
                      // (2026-09-07): its η — the Y axis — is a start-up value.
                      // Selection still wins on colour, so a picked unsettled
                      // point is an amber ring, not a filled dot.
                      const bad = p.payload?.settled === false;
                      const col = sel ? '#fbbf24' : GCOL[i % GCOL.length];
                      // `fill="none"` would make the ring click-through: SVG
                      // hit-tests painted area only, so a hollow point could
                      // be picked by landing on its 1.4 px stroke and nowhere
                      // else ("полые точки не могу выделить", 2026-09-07).  A
                      // transparent fill is still painted, so the whole disc
                      // takes the click.
                      return <circle cx={p.cx} cy={p.cy} r={sel ? 5.5 : 3.5}
                        fill={bad ? 'transparent' : col}
                        pointerEvents="all"
                        stroke={bad ? col : (sel ? '#fff' : 'none')}
                        strokeWidth={bad ? 1.4 : (sel ? 1.5 : 0)} />;
                    }}
                    onClick={(d: any) => { setSelected(d?.payload ?? d); setApplyMsg(null); }} />
                ))}
              </ScatterChart>
            </ResponsiveContainer>
          </Box>
          {selected && (
            <Box sx={{ mt: 1, p: 1, bgcolor: 'var(--panel-2)', border: '1px solid var(--line)', borderRadius: 1 }}>
              <Typography sx={{ fontSize: 11, color: 'var(--text-1)' }}>
                {/* Same unit + label the range card above prints ("now: … A peak
                    (from Simulation)") — the stored value is Arms, this line used
                    to print it raw ("I = 49.4975 A") next to a card reading
                    "70 A peak" for the SAME point (2026-09-19). */}
                Picked: <strong>I = {formatCurrent(Number(selected.I) || 0, readCurrentUnit())} · γ = {selected.g}°</strong> → η {selected.y?.toFixed?.(2)} % · {selected.x?.toFixed?.(2)} N·m/kg · T {selected.T?.toFixed?.(2)} N·m · ripple {selected.ripple?.toFixed?.(1)} %
              </Typography>
              <Typography sx={{ fontSize: 10.5, color: '#93c5fd', mt: 0.25, wordBreak: 'break-word' }}>
                geometry: {Object.keys(selected.overrides || {}).length
                  ? Object.entries(selected.overrides).map(([k, v]) => `${k}=${v}`).join(' · ')
                  : '(base — no swept variables)'}
              </Typography>
              <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', mt: 0.5 }}>
                <span title={selected.apply_eligible === true
                  ? "Apply the standard-validated point and archive it as a new motor."
                  : "Preliminary sweep: standard 6× verification is required before Apply or archive."}>
                  <Button size="small" variant="outlined" color="success"
                    disabled={selected.apply_eligible !== true}
                    onClick={() => applyPoint(selected)}>
                    ⤵ Apply to geometry
                  </Button>
                </span>
                {selected.apply_eligible !== true && <Typography sx={{ fontSize: 11, color: '#fbbf24' }}>
                  Preliminary 3× result; verify at standard 6× before applying.
                </Typography>}
                {applyMsg && <Typography sx={{ fontSize: 11,
                  color: applyMsg.startsWith('✓') ? '#4ade80' : applyMsg.startsWith('✗') ? '#fca5a5' : 'var(--text-3)' }}>
                  {applyMsg}</Typography>}
              </Box>
              {/* Where it was archived — an applied point is saved as its own
                  motor so a restart cannot take it. */}
              {saveRes && (
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mt: 0.4 }}>
                  <Typography sx={{ fontSize: 11, fontWeight: saveRes.ok ? 400 : 700,
                                    color: saveRes.ok ? '#4ade80' : '#fca5a5' }}>
                    {appliedSaveLine(saveRes)}
                  </Typography>
                  <HelpTip title={saveRes.ok
                    ? 'Applying a picked point archives it as a NEW motor in the Motors tab, with '
                      + 'the sweep, operating point and metrics that produced it in its description. '
                      + 'The motor you are editing is not touched.'
                    : 'The point IS applied, but it was NOT archived — a backend restart would lose '
                      + 'it. Save it by hand (Motors → Save as new motor) or fix the reason shown.'} />
                </Box>
              )}
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
