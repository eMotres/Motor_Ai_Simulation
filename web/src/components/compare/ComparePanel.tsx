/**
 * ComparePanel — "Compare" tab.  Two stacked (horizontal-split) sections:
 *
 *   TOP — Library: every saved snapshot as a ROW, with the main geometry +
 *         operating parameters as columns, plus inline RENAME and DELETE.
 *         Tick the rows you want to compare.
 *   BOTTOM — Comparison: only the SELECTED rows, showing ONLY the input
 *         parameters that DIFFER between them (mesh / geometry / angle / rpm /
 *         current) next to the key FEM results.
 *
 * Simulations are ROWS (not columns) so the tables stay readable with many
 * saved runs.  Store: backend JSON at config/saved_simulations.json.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  Box, Typography, Button, Checkbox, IconButton, Tooltip, Alert,
  CircularProgress, TextField, Popover, FormControlLabel, Divider,
} from '@mui/material';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import RefreshIcon       from '@mui/icons-material/Refresh';
import EditIcon          from '@mui/icons-material/Edit';
import CheckIcon         from '@mui/icons-material/Check';
import CloseIcon         from '@mui/icons-material/Close';
import CompareArrowsIcon from '@mui/icons-material/CompareArrows';
import PlayArrowIcon     from '@mui/icons-material/PlayArrow';
import { useAuth } from '../../contexts/AuthContext';
import { useMotorStore, useUIStore } from '../../stores/motorStore';
import ViewColumnIcon    from '@mui/icons-material/ViewColumn';
import HelpTip from '../common/HelpTip';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

interface SavedSim {
  id: string;
  name: string;
  created: string;
  params: Record<string, any>;
  results: Record<string, any>;
}

// ── friendly labels + units for params (fallback = raw key) ─────────────────
const PARAM_META: Record<string, { label: string; unit?: string; d?: number }> = {
  // operating point
  I_phase_rms:        { label: 'I phase',       unit: 'A',   d: 1 },
  gamma_deg:          { label: 'γ angle',       unit: '°',   d: 1 },
  rpm:                { label: 'Speed',         unit: 'rpm', d: 0 },
  frequency_hz:       { label: 'Frequency',     unit: 'Hz',  d: 1 },
  coil_temp_c:        { label: 'Coil temp',     unit: '°C',  d: 0 },
  end_winding_factor: { label: 'k_end',         d: 2 },
  connection:         { label: 'Winding conn.' },
  steps_per_period:   { label: 'Steps/period',  d: 0 },
  field_losses:       { label: 'Field losses' },
  // mesh
  n_sectors:          { label: 'Symmetry n',    d: 0 },
  mesh_size_mm:       { label: 'Mesh size',     unit: 'mm',  d: 2 },
  min_size_mm:        { label: 'Min mesh',      unit: 'mm',  d: 2 },
  // geometry (real config keys)
  num_poles:          { label: 'Poles',         d: 0 },
  num_slots:          { label: 'Slots',         d: 0 },
  num_wires_per_slot: { label: 'Wires/slot',    d: 0 },
  stator_diameter:    { label: 'Stator Ø',      unit: 'mm',  d: 1 },
  stator_outer_radius:{ label: 'Stator OR',     unit: 'mm',  d: 2 },
  stator_inner_radius:{ label: 'Stator IR',     unit: 'mm',  d: 2 },
  rotor_outer_radius: { label: 'Rotor OR',      unit: 'mm',  d: 2 },
  rotor_inner_radius: { label: 'Rotor IR',      unit: 'mm',  d: 2 },
  air_gap:            { label: 'Air gap',       unit: 'mm',  d: 3 },
  magnet_height:      { label: 'Magnet h',      unit: 'mm',  d: 2 },
  magnet_down_height: { label: 'Magnet base',   unit: 'mm',  d: 2 },
  slot_height:        { label: 'Slot height',   unit: 'mm',  d: 2 },
  slot_width:         { label: 'Slot width',    unit: 'mm',  d: 2 },
  tooth_width:        { label: 'Tooth width',   unit: 'mm',  d: 2 },
  tooth2_width:       { label: 'Tooth2 width',  unit: 'mm',  d: 2 },
  motor_length:       { label: 'Stack length',  unit: 'mm',  d: 1 },
  shaft_height:       { label: 'Shaft h',       unit: 'mm',  d: 2 },
  core_thickness:     { label: 'Core thick.',   unit: 'mm',  d: 2 },
};

// DEFAULT columns in the TOP library table (operating + main geometry).
// The user can override which columns are shown via the column-picker; the
// choice persists in localStorage('compare.libCols').
const LIB_COLS = [
  'I_phase_rms', 'gamma_deg', 'rpm', 'n_sectors',
  'num_poles', 'num_slots', 'num_wires_per_slot',
  'stator_diameter', 'stator_outer_radius', 'rotor_outer_radius', 'air_gap',
  'magnet_height', 'slot_height', 'slot_width', 'tooth_width', 'motor_length',
];

// param groups for the column-picker menu
const OP_KEYS   = ['I_phase_rms', 'gamma_deg', 'rpm', 'frequency_hz', 'coil_temp_c',
                   'end_winding_factor', 'connection', 'steps_per_period'];
const MESH_KEYS = ['n_sectors', 'mesh_size_mm', 'min_size_mm'];
const orderIdx  = (k: string) => { const i = Object.keys(PARAM_META).indexOf(k); return i === -1 ? 1e9 : i; };

// The modelling RESULTS — ordered the way they answer "which variant is better":
// torque → power → speed → efficiency → densities → ripple → mass → current
// density → the full loss breakdown.  These lead BOTH tables (before any input
// parameter), because that is what a comparison is actually read for.
const RESULT_COLS: { key: string; label: string; unit?: string; d?: number;
                     scale?: number; better?: 'hi' | 'lo';
                     derive?: (r: Record<string, any>) => number }[] = [
  { key: 'T_em_avg_Nm',          label: 'Torque',      unit: 'N·m',    d: 3, better: 'hi' },
  { key: 'P_mech_W',             label: 'Power',       unit: 'kW',     d: 2, scale: 1e-3, better: 'hi',
    derive: r => Number(r.T_em_avg_Nm) * 2 * Math.PI * Number(r.rpm) / 60 },
  { key: 'rpm',                  label: 'Speed',       unit: 'rpm',    d: 0 },
  { key: 'efficiency',           label: 'Efficiency',  unit: '%',      d: 2, scale: 100, better: 'hi' },
  { key: 'torque_per_mass_Nm_kg',label: 'TD',          unit: 'N·m/kg', d: 2, better: 'hi' },
  { key: 'power_per_mass_W_kg',  label: 'PD',          unit: 'kW/kg',  d: 2, scale: 1e-3, better: 'hi',
    derive: r => Number(r.T_em_avg_Nm) * 2 * Math.PI * Number(r.rpm) / 60 / Number(r.mass_total_kg) },
  { key: 'T_ripple_pct',         label: 'Ripple',      unit: '%',      d: 2, better: 'lo' },
  // Mass (total = EM-active + shaft) is what TD/PD above divide by; EM-active
  // drops the shaft and is the basis an Ansys active-mass expression quotes.
  { key: 'mass_total_kg',        label: 'Mass',        unit: 'kg',     d: 3, better: 'lo' },
  { key: 'mass_active_kg',       label: 'EM-active',   unit: 'kg',     d: 3, better: 'lo' },
  { key: 'J_coil_A_per_mm2',     label: 'J coil',      unit: 'A/mm²',  d: 1, better: 'lo' },
  { key: 'P_loss_total_W',       label: 'Loss total',  unit: 'W',      d: 1, better: 'lo' },
  // Drive-side trio, kept together right after the loss total: the bus voltage a
  // design demands, the current it was solved at, and its speed constant.
  { key: 'V_line_peak_V',        label: 'V_line peak', unit: 'V',      d: 1 },
  { key: 'I_phase_rms_A',        label: 'I phase',     unit: 'A rms',  d: 1 },
  { key: 'KV_rpm_per_V_line',    label: 'KV (line)',   unit: 'rpm/V',  d: 1 },
  // …then the loss breakdown behind its total.
  { key: 'P_core_W',             label: 'Fe loss',     unit: 'W',      d: 1, better: 'lo' },
  { key: 'P_stranded_W',         label: 'Cu loss',     unit: 'W',      d: 1, better: 'lo' },
  { key: 'P_solid_W',            label: 'Magnet loss', unit: 'W',      d: 1, better: 'lo' },
  { key: 'loss_density_W_kg',    label: 'Loss dens.',  unit: 'W/kg',   d: 0, better: 'lo' },
  { key: 'V_phase_peak_V',       label: 'V_ph peak',   unit: 'V',      d: 1 },
];

function fmtNum(v: any, d = 2, scale = 1): string {
  if (v == null || v === '') return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return (n * scale).toFixed(d);
}
function valEq(a: any, b: any): boolean {
  if (typeof a === 'number' && typeof b === 'number') return Math.abs(a - b) < 1e-6;
  return String(a) === String(b);
}
/** A result column's value: derived where the column defines it, stored otherwise. */
function resultVal(r: typeof RESULT_COLS[number], s: SavedSim): number {
  const raw = r.derive ? r.derive(s.results || {}) : Number(s.results?.[r.key]);
  return Number.isFinite(raw) ? raw : NaN;
}
const paramLabel = (k: string) => PARAM_META[k]?.label ?? k;
const paramUnit  = (k: string) => PARAM_META[k]?.unit;
/** Percent hugs its label; every other unit is spaced off it. */
const unitGap = (u?: string) => (u === '%' ? '' : ' ');
function paramFmt(k: string, v: any): string {
  const m = PARAM_META[k];
  if (typeof v === 'number') return fmtNum(v, m?.d ?? 3);
  return v == null ? '—' : String(v);
}

// shared table cell styling
// Result columns get the app's primary blue (same family as the buttons) —
// readable on both themes, unlike the dark-theme green accent it replaced.
const HDR_RESULT = '#2563eb';
const HDR_DIFF   = '#b45309';   // amber-700: the pale #fbbf24 vanished on white
const TH = {
  px: 1.25, py: 0.7, fontSize: 10.5, color: 'var(--text-2)', fontWeight: 700,
  textTransform: 'uppercase', letterSpacing: '0.03em', whiteSpace: 'nowrap',
  textAlign: 'right', borderBottom: '1px solid var(--line-soft)', bgcolor: 'var(--panel-2)',
  position: 'sticky', top: 0, zIndex: 2,
} as const;
const TD = {
  px: 1.25, py: 0.55, fontSize: 12, whiteSpace: 'nowrap', textAlign: 'right',
  borderBottom: '1px solid var(--app-bg)', fontFamily: 'monospace', color: 'var(--text-1)',
} as const;

const ComparePanel: React.FC = () => {
  const [sims,    setSims]    = useState<SavedSim[]>([]);
  const [sel,     setSel]     = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [err,     setErr]     = useState<string | null>(null);
  const [editId,  setEditId]  = useState<string | null>(null);
  const [editName,setEditName]= useState('');
  const [applyMsg, setApplyMsg] = useState<string | null>(null);
  const updateGeometryViaApi = useMotorStore(s => s.updateGeometryViaApi);
  const clearStlCache = useMotorStore(s => s.clearStlCache);
  const setActiveTab = useUIStore(s => s.setActiveTab);
  // column-picker for the TOP library table (persisted)
  const [visibleCols, setVisibleCols] = useState<string[]>(() => {
    try { const r = localStorage.getItem('compare.libCols'); const a = r ? JSON.parse(r) : null;
      return Array.isArray(a) && a.length ? a : LIB_COLS; } catch { return LIB_COLS; }
  });
  useEffect(() => { try { localStorage.setItem('compare.libCols', JSON.stringify(visibleCols)); } catch {} }, [visibleCols]);
  const [colAnchor, setColAnchor] = useState<HTMLElement | null>(null);
  const { user } = useAuth();

  const load = useCallback(() => {
    setLoading(true); setErr(null);
    fetch(`${API}/api/sims/saved`)
      .then(r => r.json())
      .then(d => {
        const list: SavedSim[] = d.sims || [];
        setSims(list);
        setSel(prev => {
          const ids = new Set(list.map(s => s.id));
          const kept = new Set([...prev].filter(id => ids.has(id)));
          if (kept.size === 0 && list.length) list.slice(0, Math.min(4, list.length)).forEach(s => kept.add(s.id));
          return kept;
        });
      })
      .catch(e => setErr(String(e)))
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => { load(); }, [load]);
  // A point added from the Simulation tab ("+ Compare") shows up without a manual
  // refresh, even if this panel was already mounted.
  useEffect(() => {
    const onChanged = () => load();
    window.addEventListener('compare-points-changed', onChanged);
    return () => window.removeEventListener('compare-points-changed', onChanged);
  }, [load]);

  const toggle = (id: string) =>
    setSel(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });

  // fetch() only rejects on a NETWORK failure — a 404 or 500 resolves normally.
  // Every mutation here used to go straight to .then(load), so a rejected delete
  // reloaded the identical list and the row just sat there with no error shown:
  // the button looked broken. Check the status explicitly and surface it.
  const mutate = (url: string, init: RequestInit, what: string) =>
    fetch(url, init)
      .then(async r => {
        if (!r.ok) {
          const body = await r.text().catch(() => '');
          throw new Error(`${what} failed (HTTP ${r.status})${body ? `: ${body.slice(0, 200)}` : ''}`);
        }
      })
      .then(load)
      .catch(e => setErr(String(e.message ?? e)));

  // ── Apply a saved point back into the live design ─────────────────────────
  // A Compare row already carries the full geometry it was solved with, so it
  // can be pushed back: the Geometry tab then draws that machine and Simulation
  // recomputes it. Without this the rows are a dead end — you can read a good
  // optimum but not get back to it, and after a solver change (lamination, say)
  // a stored row cannot be re-checked at all.
  //
  // Same path "Apply picked point" uses in the sweep panel: geometry via the
  // API, operating point into the sim config, then the two events that make the
  // Simulation tab drop its stale summary and re-solve.
  const applyPoint = async (s: SavedSim) => {
    const p: Record<string, any> = s.params || {};
    // geometry only — params also carries the operating point and mesh settings
    const NON_GEOM = new Set([
      'I_phase_rms', 'gamma_deg', 'rpm', 'coil_temp_c', 'end_winding_factor',
      'connection', 'steps_per_period', 'n_sectors', 'element_order',
      'mesh_size_mm', 'min_size_mm', 'gap_layers', 'frequency_hz',
    ]);
    const geo: Record<string, number> = {};
    const mats: Record<string, string> = {};
    for (const [k, v] of Object.entries(p)) {
      if (k.startsWith('mat_') && typeof v === 'string' && v) { mats[k.slice(4)] = v; continue; }
      if (!NON_GEOM.has(k) && typeof v === 'number' && Number.isFinite(v)) geo[k] = v;
    }
    if (!Object.keys(geo).length) { setErr(`"${s.name}" carries no geometry to apply`); return; }
    setErr(null); setApplyMsg(`applying "${s.name}" …`);
    try {
      await updateGeometryViaApi(geo as any);
      // MATERIALS — a design is its geometry AND what it is made of. Applying the
      // shape alone would re-check a Fe16N2 result against whatever magnet is
      // currently assigned, with nothing on screen to say so. Points saved before
      // materials were snapshotted have none; those keep the current assignment
      // and the message below says exactly that, rather than pretending.
      let matNote = '';
      const matNames = Object.keys(mats);
      if (matNames.length) {
        for (const [part, name] of Object.entries(mats)) {
          const r = await fetch(`${API}/api/materials`, {
            method: 'PATCH', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ part, material: name }),
          });
          if (!r.ok) throw new Error(`material ${part}=${name}: HTTP ${r.status}`);
        }
        matNote = ` · materials: ${mats.magnet ?? '—'}`;
      } else {
        matNote = ' · no materials stored — kept the current assignment';
      }
      const I = Number(p.I_phase_rms), g = Number(p.gamma_deg);
      if (Number.isFinite(I) || Number.isFinite(g)) {
        await fetch(`${API}/api/simulation/config`, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            ...(Number.isFinite(I) ? { max_current: I } : {}),
            ...(Number.isFinite(g) ? { phase_offset_deg: g } : {}),
          }),
        });
        window.dispatchEvent(new CustomEvent('sim-operating-point',
          { detail: { current: I, gamma: g } }));
      }
      if (Number.isFinite(Number(p.rpm))) localStorage.setItem('sim.rpm', JSON.stringify(Number(p.rpm)));
      if (Number.isFinite(Number(p.coil_temp_c))) localStorage.setItem('sim.coilTemp', JSON.stringify(Number(p.coil_temp_c)));
      // k_end belongs to the NEW geometry — re-seed it before the recompute or
      // the copper loss is scaled by the previous design's end-turn length.
      try {
        const dc = await (await fetch(`${API}/api/config`)).json();
        const k = Number(dc?.end_winding_factor);
        if (Number.isFinite(k) && k > 0) localStorage.setItem('sim.endWinding', JSON.stringify(+k.toFixed(3)));
      } catch { /* the Simulation panel re-seeds on the geometry change anyway */ }
      // GEOMETRY REBUILD — updateGeometryViaApi refreshes the parametric view
      // from the store, but the STL cache is keyed on the old parameters and
      // would keep showing the previous machine in the 3D view. Drop it so the
      // next look rebuilds. Best-effort: a stale cache is a cosmetic problem,
      // failing the whole apply over it is not.
      try { await clearStlCache(); } catch { /* cosmetic only */ }
      window.dispatchEvent(new CustomEvent('sim-design-applied'));
      window.dispatchEvent(new CustomEvent('sim-rerun'));
      setApplyMsg(`✓ "${s.name}" applied${matNote} — geometry rebuilt, recomputing in Simulation`);
      setActiveTab('simulation');
    } catch (e: any) {
      setApplyMsg(null);
      setErr(`Apply failed: ${String(e?.message ?? e)} — nothing was changed`);
    }
  };

  const del = (id: string) =>
    mutate(`${API}/api/sims/saved/${id}`, { method: 'DELETE' }, 'Delete');
  const clearAll = () => {
    if (!window.confirm('Delete ALL saved simulations?')) return;
    mutate(`${API}/api/sims/saved`, { method: 'DELETE' }, 'Delete all');
  };

  // The per-row Delete lives in the "Actions" column — the LAST one, behind every
  // result and input column, so on a wide table it is off-screen and effectively
  // does not exist. The only reachable bin was "delete all", which asks a scary
  // question and then does nothing when you cancel: ticking a row and pressing
  // the bin looked like a broken button. So the toolbar bin now acts on the
  // SELECTION when there is one, and only falls back to wiping the library when
  // nothing is ticked.
  const delSelected = async () => {
    const ids = [...sel];
    if (!ids.length) return clearAll();
    const shown = selected.slice(0, 6).map(s => `  • ${s.name}`).join('\n');
    const more = ids.length > 6 ? `\n  … and ${ids.length - 6} more` : '';
    if (!window.confirm(`Delete ${ids.length} selected simulation(s)?\n\n${shown}${more}`)) return;
    setErr(null);
    const failed: string[] = [];
    for (const id of ids) {
      try {
        const r = await fetch(`${API}/api/sims/saved/${id}`, { method: 'DELETE' });
        if (!r.ok) failed.push(`${id} (HTTP ${r.status})`);
      } catch (e) {
        failed.push(`${id} (${String(e)})`);
      }
    }
    load();
    if (failed.length) setErr(`Could not delete: ${failed.join(', ')}`);
  };
  const startRename = (s: SavedSim) => { setEditId(s.id); setEditName(s.name); };
  const commitRename = () => {
    if (!editId) return;
    const id = editId;
    // same silent-failure trap as delete — a rejected rename must not look like
    // a successful one that simply changed nothing
    setEditId(null);
    mutate(`${API}/api/sims/saved/${id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: editName }),
    }, 'Rename');
  };

  const selected = sims.filter(s => sel.has(s.id));

  // ── column-picker: all keys present in any saved sim (+ the defaults) ─────
  const availableKeys = (() => {
    const s = new Set<string>(sims.flatMap(x => Object.keys(x.params || {})));
    LIB_COLS.forEach(k => s.add(k));
    return Array.from(s).sort((a, b) => (orderIdx(a) - orderIdx(b)) || a.localeCompare(b));
  })();
  const colGroups = [
    { title: 'Operating', keys: availableKeys.filter(k => OP_KEYS.includes(k)) },
    { title: 'Mesh',      keys: availableKeys.filter(k => MESH_KEYS.includes(k)) },
    { title: 'Geometry',  keys: availableKeys.filter(k => !OP_KEYS.includes(k) && !MESH_KEYS.includes(k)) },
  ];
  // Speed and phase current now lead the row as RESULT columns (the value the run
  // was actually solved at), so drop the identical INPUT columns — otherwise every
  // row carries the same number twice.  Filtering here (rather than trimming
  // LIB_COLS) also cleans up column choices already saved in localStorage.
  const COVERED_BY_RESULTS = new Set(['rpm', 'I_phase_rms']);
  const displayCols = availableKeys.filter(k => visibleCols.includes(k) && !COVERED_BY_RESULTS.has(k));
  const toggleCol = (k: string) =>
    setVisibleCols(prev => prev.includes(k) ? prev.filter(x => x !== k) : [...prev, k]);

  // differing input params across the selected runs
  const allParamKeys = Array.from(new Set(selected.flatMap(s => Object.keys(s.params || {}))));
  const diffKeys = allParamKeys.filter(k => {
    const vals = selected.map(s => s.params?.[k]);
    return vals.some(v => !valEq(v, vals[0]));
  }).sort((a, b) => {
    const ia = Object.keys(PARAM_META).indexOf(a), ib = Object.keys(PARAM_META).indexOf(b);
    if (ia !== -1 || ib !== -1) return (ia === -1 ? 1e9 : ia) - (ib === -1 ? 1e9 : ib);
    return a.localeCompare(b);
  });

  // per-result best/worst across the selected rows (for highlight)
  const resExtent: Record<string, { min: number; max: number }> = {};
  RESULT_COLS.forEach(r => {
    const nums = selected.map(s => resultVal(r, s)).filter(Number.isFinite);
    if (nums.length) resExtent[r.key] = { min: Math.min(...nums), max: Math.max(...nums) };
  });
  // Same, but across EVERY saved row — so the library table alone already shows
  // which variant leads on each metric, without having to tick rows first.
  const libExtent: Record<string, { min: number; max: number }> = {};
  RESULT_COLS.forEach(r => {
    const nums = sims.map(s => resultVal(r, s)).filter(Number.isFinite);
    if (nums.length) libExtent[r.key] = { min: Math.min(...nums), max: Math.max(...nums) };
  });

  /** Green on the best value, red on the worst — grey when a metric has no
   *  better/worse direction (speed, voltage) or every row ties. */
  const metricColor = (r: typeof RESULT_COLS[number], raw: number,
                       ext?: { min: number; max: number }): string => {
    if (!Number.isFinite(raw) || !ext || ext.min === ext.max || !r.better) return 'var(--text-1)';
    const best  = r.better === 'hi' ? ext.max : ext.min;
    const worst = r.better === 'hi' ? ext.min : ext.max;
    if (Math.abs(raw - best)  < 1e-9) return '#4ade80';
    if (Math.abs(raw - worst) < 1e-9) return '#f87171';
    return 'var(--text-1)';
  };

  // Largest value per column across the SELECTED rows — the reference the Δ%
  // row is measured against (keys prefixed so a result and an input of the same
  // name can't collide).
  const colMax: Record<string, number> = {};
  RESULT_COLS.forEach(r => {
    const ns = selected.map(s => resultVal(r, s)).filter(Number.isFinite);
    if (ns.length) colMax[`r:${r.key}`] = Math.max(...ns);
  });
  const diffSet = new Set(diffKeys);

  /** Deviation from the column maximum, in %. 0 % = the leader. */
  const PctCell: React.FC<{ v: number; max?: number }> = ({ v, max }) => {
    const ok = Number.isFinite(v) && Number.isFinite(max as number) && Math.abs(max as number) > 1e-12;
    const d = ok ? (v / (max as number) - 1) * 100 : NaN;
    const atMax = ok && Math.abs(d) < 1e-9;
    return (
      <Box component="td" sx={{ ...TD, fontSize: 9.5,
        color: atMax ? '#4ade80' : (ok ? 'var(--text-4)' : 'var(--line)') }}>
        {ok ? `${d > -1e-9 ? '' : ''}${d.toFixed(1)}%` : '—'}
      </Box>
    );
  };

  /** One result cell, shared by the library and the comparison table. */
  const ResultCell: React.FC<{ r: typeof RESULT_COLS[number]; s: SavedSim;
                               ext?: { min: number; max: number } }> = ({ r, s, ext }) => {
    const raw = resultVal(r, s);
    const col = metricColor(r, raw, ext);
    return (
      <Box component="td" sx={{ ...TD, color: col, fontWeight: col !== 'var(--text-1)' ? 700 : 400 }}>
        {Number.isFinite(raw) ? fmtNum(raw, r.d ?? 2, r.scale ?? 1) : '—'}
      </Box>
    );
  };

  // Renders its OWN <td>, so callers must place it directly in the row — wrapping
  // it in another <td> nests a cell inside a cell (invalid HTML, and React warns).
  // `left` is where the sticky column starts: 0 in the comparison table, past the
  // tick box in the library one.
  const NameCell: React.FC<{ s: SavedSim; sticky?: boolean; left?: number }> = ({ s, sticky, left = 0 }) => (
    <Box component="td" sx={{ ...TD, textAlign: 'left', color: 'var(--text-0)', fontFamily: 'inherit',
      fontWeight: 600, ...(sticky ? { position: 'sticky', left, bgcolor: 'var(--panel-2)', zIndex: 1 } : {}) }}>
      {editId === s.id ? (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.25 }}>
          <TextField value={editName} onChange={e => setEditName(e.target.value)} size="small" autoFocus
            onKeyDown={e => { if (e.key === 'Enter') commitRename(); if (e.key === 'Escape') setEditId(null); }}
            inputProps={{ style: { fontSize: 12, padding: '2px 6px' } }} sx={{ width: 150 }} />
          <IconButton size="small" onClick={commitRename} sx={{ color: '#4ade80', p: 0.25 }}><CheckIcon sx={{ fontSize: 15 }} /></IconButton>
          <IconButton size="small" onClick={() => setEditId(null)} sx={{ color: 'var(--text-3)', p: 0.25 }}><CloseIcon sx={{ fontSize: 15 }} /></IconButton>
        </Box>
      ) : (
        // Rename lives in the sticky NAME column, not only in the far-right
        // Actions column: with the result columns in front, Actions sits past a
        // screen-width of horizontal scrolling and is effectively unreachable.
        // Double-clicking the name works too.
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5,
          '&:hover .renameBtn': { opacity: 1 } }}
          onDoubleClick={() => startRename(s)}
          title="Double-click to rename">
          {/* Apply sits FIRST and is always visible: it is the one action that
              turns a stored row back into a live design you can look at. */}
          <Tooltip title="Apply this geometry + operating point, then recompute in Simulation">
            <IconButton size="small" onClick={() => applyPoint(s)}
              sx={{ color: '#2563eb', p: 0.15, '&:hover': { color: '#1d4ed8' } }}>
              <PlayArrowIcon sx={{ fontSize: 16 }} />
            </IconButton>
          </Tooltip>
          <Box component="span" sx={{ cursor: 'text' }}>{s.name}</Box>
          <IconButton className="renameBtn" size="small" onClick={() => startRename(s)}
            sx={{ opacity: 0.25, transition: 'opacity .15s', color: 'var(--text-3)', p: 0.15,
              '&:hover': { color: '#60a5fa' } }}>
            <EditIcon sx={{ fontSize: 13 }} />
          </IconButton>
        </Box>
      )}
    </Box>
  );

  return (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', bgcolor: 'var(--panel-2)', overflow: 'hidden' }}>

      {/* ══ TOP: library of all saved simulations ══ */}
      <Box sx={{ flex: '1 1 50%', display: 'flex', flexDirection: 'column', minHeight: 0, borderBottom: '2px solid var(--line-soft)' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 2, py: 1 }}>
          <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-0)' }}>
            Saved simulations
          </Typography>
          <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>({sims.length}) — tick rows to compare below</Typography>
          {/* The backend keys this store per user (saved_sims._bucket): signed in →
              your own library, anonymous → the shared local one.  Without saying so,
              a sign-in/sign-out silently swaps the whole list and reads as data loss. */}
          <Tooltip placement="top" title={user
            ? 'Points are saved to YOUR library. Sign out and this list is replaced by the local one.'
            : 'Not signed in — points go to the shared LOCAL library on this machine. Sign in and you will see your own library instead.'}>
            <Typography sx={{ fontSize: 10.5, px: 0.75, py: 0.15, borderRadius: 0.5, cursor: 'help',
              border: '1px solid var(--line)', whiteSpace: 'nowrap',
              color: user ? '#4ade80' : '#fbbf24' }}>
              {user ? `library: ${user.email ?? String(user.uid).slice(0, 8)}` : 'library: local (not signed in)'}
            </Typography>
          </Tooltip>
          <Box sx={{ flex: 1 }} />
          <Button size="small" variant="outlined" startIcon={<ViewColumnIcon sx={{ fontSize: 16 }} />}
            onClick={e => setColAnchor(e.currentTarget)}
            sx={{ fontSize: 11, textTransform: 'none', color: 'var(--text-2)', borderColor: 'var(--line)',
              '&:hover': { borderColor: '#3b82f6', color: 'var(--text-1)' } }}>
            Columns ({displayCols.length})
          </Button>
          <Tooltip title="Reload"><IconButton size="small" onClick={load} sx={{ color: 'var(--text-3)' }}><RefreshIcon sx={{ fontSize: 17 }} /></IconButton></Tooltip>
          {sims.length > 0 && (
            <Tooltip title={sel.size ? `Delete ${sel.size} ticked row${sel.size > 1 ? 's' : ''}` : 'Delete ALL saved simulations'}>
              <IconButton size="small" onClick={delSelected}
                sx={{ color: sel.size ? '#b91c1c' : '#7f1d1d' }}>
                <DeleteOutlineIcon sx={{ fontSize: 17 }} />
              </IconButton>
            </Tooltip>
          )}
        </Box>

        {/* column-picker popover */}
        <Popover open={!!colAnchor} anchorEl={colAnchor} onClose={() => setColAnchor(null)}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
          transformOrigin={{ vertical: 'top', horizontal: 'right' }}
          PaperProps={{ sx: { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', width: 250, maxHeight: 440 } }}>
          <Box sx={{ position: 'sticky', top: 0, bgcolor: 'var(--panel-2)', zIndex: 1, px: 1.5, pt: 1.25, pb: 0.75,
            borderBottom: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', gap: 0.5 }}>
            <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-1)', flex: 1 }}>Columns</Typography>
            <Button size="small" onClick={() => setVisibleCols(availableKeys)} sx={{ fontSize: 10, minWidth: 0, textTransform: 'none', color: '#60a5fa' }}>All</Button>
            <Button size="small" onClick={() => setVisibleCols([])} sx={{ fontSize: 10, minWidth: 0, textTransform: 'none', color: 'var(--text-2)' }}>None</Button>
            <Button size="small" onClick={() => setVisibleCols(LIB_COLS)} sx={{ fontSize: 10, minWidth: 0, textTransform: 'none', color: 'var(--text-2)' }}>Reset</Button>
          </Box>
          <Box sx={{ px: 1.5, py: 1 }}>
            {colGroups.map(g => g.keys.length === 0 ? null : (
              <Box key={g.title} sx={{ mb: 1 }}>
                <Typography sx={{ fontSize: 9, fontWeight: 700, color: 'var(--text-4)',
                  textTransform: 'uppercase', letterSpacing: '0.06em', mb: 0.25 }}>{g.title}</Typography>
                {g.keys.map(k => (
                  <FormControlLabel key={k} sx={{ display: 'flex', ml: 0, height: 23 }}
                    control={<Checkbox size="small" checked={visibleCols.includes(k)} onChange={() => toggleCol(k)}
                      sx={{ p: 0.25, mr: 0.5, color: 'var(--text-4)', '&.Mui-checked': { color: '#3b82f6' } }} />}
                    label={<Typography sx={{ fontSize: 11, color: 'var(--text-1)' }}>
                      {paramLabel(k)}{paramUnit(k) ? <Box component="span" sx={{ color: 'var(--text-4)' }}> ({paramUnit(k)})</Box> : null}
                    </Typography>} />
                ))}
              </Box>
            ))}
          </Box>
        </Popover>
        {err && <Alert severity="error" sx={{ fontSize: 11, mx: 2 }}>{err}</Alert>}
        {applyMsg && <Alert severity="info" sx={{ fontSize: 11, mx: 2 }}
          onClose={() => setApplyMsg(null)}>{applyMsg}</Alert>}
        <Box sx={{ flex: 1, overflow: 'auto', px: 2, pb: 1 }}>
          {loading && <CircularProgress size={18} sx={{ color: '#3b82f6', m: 2 }} />}
          {!loading && sims.length === 0 && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, m: 2 }}>
              <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>
                No comparison points yet.
              </Typography>
              <HelpTip title="In the Simulation tab, run a solve and press + Add to Compare on the summary card to snapshot the design here." />
            </Box>
          )}
          {sims.length > 0 && (
            <Box component="table" sx={{ borderCollapse: 'collapse', width: '100%' }}>
              <Box component="thead"><Box component="tr">
                <Box component="th" sx={{ ...TH, textAlign: 'center', left: 0, zIndex: 3, position: 'sticky' }}>✓</Box>
                <Box component="th" sx={{ ...TH, textAlign: 'left', left: 36, zIndex: 3 }}>Name</Box>
                {/* RESULTS first — the numbers the choice is made on. */}
                {RESULT_COLS.map(r => (
                  <Box component="th" key={`r-${r.key}`} sx={{ ...TH, color: HDR_RESULT }}>
                    {r.label}{r.unit ? <Box component="span" sx={{ color: 'var(--text-3)', fontWeight: 500 }}>{unitGap(r.unit)}{r.unit}</Box> : null}
                  </Box>
                ))}
                {/* …then the inputs that produced them. */}
                {displayCols.map(k => (
                  <Box component="th" key={k} sx={TH}>
                    {paramLabel(k)}{paramUnit(k) ? <Box component="span" sx={{ color: 'var(--text-3)', fontWeight: 500 }}>{unitGap(paramUnit(k))}{paramUnit(k)}</Box> : null}
                  </Box>
                ))}
                <Box component="th" sx={{ ...TH, textAlign: 'left' }}>Saved</Box>
                <Box component="th" sx={{ ...TH, textAlign: 'center' }}>Actions</Box>
              </Box></Box>
              <Box component="tbody">
                {sims.map(s => (
                  <Box component="tr" key={s.id}
                    sx={{ bgcolor: sel.has(s.id) ? 'var(--panel-2)' : 'transparent', '&:hover': { bgcolor: 'var(--panel-2)' } }}>
                    <Box component="td" sx={{ ...TD, textAlign: 'center', position: 'sticky', left: 0,
                      bgcolor: sel.has(s.id) ? 'var(--panel-2)' : 'var(--panel-2)', zIndex: 1 }}>
                      <Checkbox size="small" checked={sel.has(s.id)} onChange={() => toggle(s.id)}
                        sx={{ p: 0.25, color: 'var(--text-4)', '&.Mui-checked': { color: '#3b82f6' } }} />
                    </Box>
                    <NameCell s={s} sticky left={36} />
                    {RESULT_COLS.map(r => (
                      <ResultCell key={`r-${r.key}`} r={r} s={s} ext={libExtent[r.key]} />
                    ))}
                    {displayCols.map(k => (
                      <Box component="td" key={k} sx={TD}>{paramFmt(k, s.params?.[k])}</Box>
                    ))}
                    <Box component="td" sx={{ ...TD, textAlign: 'left', color: 'var(--text-4)', fontSize: 10 }}>{s.created}</Box>
                    <Box component="td" sx={{ ...TD, textAlign: 'center' }}>
                      <Tooltip title="Rename"><IconButton size="small" onClick={() => startRename(s)} sx={{ color: 'var(--text-3)', p: 0.25, '&:hover': { color: '#60a5fa' } }}><EditIcon sx={{ fontSize: 15 }} /></IconButton></Tooltip>
                      <Tooltip title="Delete"><IconButton size="small" onClick={() => del(s.id)} sx={{ color: 'var(--text-3)', p: 0.25, '&:hover': { color: '#f87171' } }}><DeleteOutlineIcon sx={{ fontSize: 15 }} /></IconButton></Tooltip>
                    </Box>
                  </Box>
                ))}
              </Box>
            </Box>
          )}
        </Box>
      </Box>

      {/* ══ BOTTOM: comparison of selected — only differing params ══ */}
      <Box sx={{ flex: '1 1 50%', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 2, py: 1 }}>
          <CompareArrowsIcon sx={{ color: '#60a5fa', fontSize: 18 }} />
          <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-0)' }}>Comparison</Typography>
          <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
            {selected.length} selected · {diffKeys.length} differing input{diffKeys.length === 1 ? '' : 's'}
          </Typography>
          <HelpTip title="Amber = a differing input. Green = best, red = worst across the selected columns. The Δ% row is each value's deviation from the column maximum." />
        </Box>
        <Box sx={{ flex: 1, overflow: 'auto', px: 2, pb: 2 }}>
          {selected.length < 2 ? (
            <Alert severity="info" sx={{ fontSize: 12 }}>Tick at least two rows above to compare them.</Alert>
          ) : (
            <Box component="table" sx={{ borderCollapse: 'collapse', width: '100%' }}>
              {/* SAME column layout as the library above — results first, then the
                  inputs — so the eye doesn't have to re-learn the table halfway down
                  the screen.  Inputs that DIFFER across the selection are amber. */}
              <Box component="thead"><Box component="tr">
                <Box component="th" sx={{ ...TH, textAlign: 'left', left: 0, zIndex: 3, position: 'sticky' }}>Simulation</Box>
                {RESULT_COLS.map(r => (
                  <Box component="th" key={`r-${r.key}`} sx={{ ...TH, color: HDR_RESULT }}>
                    {r.label}{r.unit ? <Box component="span" sx={{ color: 'var(--text-3)', fontWeight: 500 }}>{unitGap(r.unit)}{r.unit}</Box> : null}
                  </Box>
                ))}
                {displayCols.map(k => (
                  <Box component="th" key={k} sx={{ ...TH, color: diffSet.has(k) ? HDR_DIFF : 'var(--text-2)' }}>
                    {paramLabel(k)}{paramUnit(k) ? <Box component="span" sx={{ color: 'var(--text-3)', fontWeight: 500 }}>{unitGap(paramUnit(k))}{paramUnit(k)}</Box> : null}
                  </Box>
                ))}
              </Box></Box>
              <Box component="tbody">
                {selected.map(s => [
                  <Box component="tr" key={s.id} sx={{ '&:hover': { bgcolor: 'var(--panel-2)' } }}>
                    <NameCell s={s} sticky />
                    {RESULT_COLS.map(r => (
                      <ResultCell key={`r-${r.key}`} r={r} s={s} ext={resExtent[r.key]} />
                    ))}
                    {displayCols.map(k => (
                      <Box component="td" key={k} sx={{ ...TD, color: diffSet.has(k) ? '#fbbf24' : 'var(--text-1)' }}>
                        {paramFmt(k, s.params?.[k])}
                      </Box>
                    ))}
                  </Box>,
                  // Δ% against the largest value in each column: 0.0 % marks the
                  // leader, everything else reads as "how far behind".
                  <Box component="tr" key={`${s.id}-pct`}>
                    <Box component="td" sx={{ ...TD, textAlign: 'right', position: 'sticky', left: 0,
                      bgcolor: 'var(--panel-2)', zIndex: 1, color: 'var(--text-4)', fontSize: 9.5 }}>
                      Δ% from max
                    </Box>
                    {RESULT_COLS.map(r => (
                      <PctCell key={`p-${r.key}`} v={resultVal(r, s)} max={colMax[`r:${r.key}`]} />
                    ))}
                    {displayCols.map(k => (
                      <PctCell key={`p-${k}`} v={Number(s.params?.[k])} max={colMax[`p:${k}`]} />
                    ))}
                  </Box>,
                ])}
                {diffKeys.length === 0 && (
                  <Box component="tr"><Box component="td" {...({ colSpan: 1 + RESULT_COLS.length + displayCols.length } as any)}
                    sx={{ ...TD, textAlign: 'left', color: 'var(--text-3)' }}>
                    Selected runs have identical inputs — only the results differ.
                  </Box></Box>
                )}
              </Box>
            </Box>
          )}
        </Box>
      </Box>
    </Box>
  );
};

export default ComparePanel;
