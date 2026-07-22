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
import ViewColumnIcon    from '@mui/icons-material/ViewColumn';

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

// result columns in the BOTTOM comparison table
const RESULT_COLS: { key: string; label: string; unit?: string; d?: number;
                     scale?: number; better?: 'hi' | 'lo' }[] = [
  { key: 'T_em_avg_Nm',          label: 'T_avg',          unit: 'N·m',    d: 2, better: 'hi' },
  { key: 'T_ripple_pct',         label: 'Ripple',         unit: '%',      d: 1, better: 'lo' },
  { key: 'efficiency',           label: 'η',              unit: '%',      d: 2, scale: 100, better: 'hi' },
  { key: 'P_mech_W',             label: 'P mech',         unit: 'kW',     d: 2, scale: 1e-3, better: 'hi' },
  { key: 'P_loss_total_W',       label: 'Total loss',     unit: 'W',      d: 0, better: 'lo' },
  { key: 'P_core_W',             label: 'Fe loss',        unit: 'W',      d: 0, better: 'lo' },
  { key: 'P_stranded_W',         label: 'Cu loss',        unit: 'W',      d: 0, better: 'lo' },
  { key: 'P_solid_W',            label: 'Magnet loss',    unit: 'W',      d: 0, better: 'lo' },
  { key: 'V_phase_peak_V',       label: 'V_ph peak',      unit: 'V',      d: 1 },
  { key: 'KV_rpm_per_V_line',    label: 'KV line',        unit: 'rpm/V',  d: 1 },
  { key: 'mass_total_kg',        label: 'Mass',           unit: 'kg',     d: 2, better: 'lo' },
  { key: 'torque_per_mass_Nm_kg',label: 'T density',      unit: 'N·m/kg', d: 2, better: 'hi' },
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
const paramLabel = (k: string) => PARAM_META[k]?.label ?? k;
const paramUnit  = (k: string) => PARAM_META[k]?.unit;
function paramFmt(k: string, v: any): string {
  const m = PARAM_META[k];
  if (typeof v === 'number') return fmtNum(v, m?.d ?? 3);
  return v == null ? '—' : String(v);
}

// shared table cell styling
const TH = {
  px: 1.25, py: 0.7, fontSize: 10, color: 'var(--text-3)', fontWeight: 700,
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
  // column-picker for the TOP library table (persisted)
  const [visibleCols, setVisibleCols] = useState<string[]>(() => {
    try { const r = localStorage.getItem('compare.libCols'); const a = r ? JSON.parse(r) : null;
      return Array.isArray(a) && a.length ? a : LIB_COLS; } catch { return LIB_COLS; }
  });
  useEffect(() => { try { localStorage.setItem('compare.libCols', JSON.stringify(visibleCols)); } catch {} }, [visibleCols]);
  const [colAnchor, setColAnchor] = useState<HTMLElement | null>(null);

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

  const toggle = (id: string) =>
    setSel(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const del = (id: string) =>
    fetch(`${API}/api/sims/saved/${id}`, { method: 'DELETE' }).then(load).catch(e => setErr(String(e)));
  const clearAll = () => {
    if (!window.confirm('Delete ALL saved simulations?')) return;
    fetch(`${API}/api/sims/saved`, { method: 'DELETE' }).then(load).catch(e => setErr(String(e)));
  };
  const startRename = (s: SavedSim) => { setEditId(s.id); setEditName(s.name); };
  const commitRename = () => {
    if (!editId) return;
    const id = editId;
    fetch(`${API}/api/sims/saved/${id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: editName }),
    }).then(() => { setEditId(null); load(); }).catch(e => setErr(String(e)));
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
  const displayCols = availableKeys.filter(k => visibleCols.includes(k));
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
    const nums = selected.map(s => Number(s.results?.[r.key])).filter(Number.isFinite);
    if (nums.length) resExtent[r.key] = { min: Math.min(...nums), max: Math.max(...nums) };
  });

  const NameCell: React.FC<{ s: SavedSim; sticky?: boolean }> = ({ s, sticky }) => (
    <Box component="td" sx={{ ...TD, textAlign: 'left', color: 'var(--text-0)', fontFamily: 'inherit',
      fontWeight: 600, ...(sticky ? { position: 'sticky', left: 0, bgcolor: 'var(--panel-2)', zIndex: 1 } : {}) }}>
      {editId === s.id ? (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.25 }}>
          <TextField value={editName} onChange={e => setEditName(e.target.value)} size="small" autoFocus
            onKeyDown={e => { if (e.key === 'Enter') commitRename(); if (e.key === 'Escape') setEditId(null); }}
            inputProps={{ style: { fontSize: 12, padding: '2px 6px' } }} sx={{ width: 150 }} />
          <IconButton size="small" onClick={commitRename} sx={{ color: '#4ade80', p: 0.25 }}><CheckIcon sx={{ fontSize: 15 }} /></IconButton>
          <IconButton size="small" onClick={() => setEditId(null)} sx={{ color: 'var(--text-3)', p: 0.25 }}><CloseIcon sx={{ fontSize: 15 }} /></IconButton>
        </Box>
      ) : s.name}
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
          <Box sx={{ flex: 1 }} />
          <Button size="small" variant="outlined" startIcon={<ViewColumnIcon sx={{ fontSize: 16 }} />}
            onClick={e => setColAnchor(e.currentTarget)}
            sx={{ fontSize: 11, textTransform: 'none', color: 'var(--text-2)', borderColor: 'var(--line)',
              '&:hover': { borderColor: '#3b82f6', color: 'var(--text-1)' } }}>
            Columns ({displayCols.length})
          </Button>
          <Tooltip title="Reload"><IconButton size="small" onClick={load} sx={{ color: 'var(--text-3)' }}><RefreshIcon sx={{ fontSize: 17 }} /></IconButton></Tooltip>
          {sims.length > 0 && (
            <Tooltip title="Delete all"><IconButton size="small" onClick={clearAll} sx={{ color: '#7f1d1d' }}><DeleteOutlineIcon sx={{ fontSize: 17 }} /></IconButton></Tooltip>
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
        <Box sx={{ flex: 1, overflow: 'auto', px: 2, pb: 1 }}>
          {loading && <CircularProgress size={18} sx={{ color: '#3b82f6', m: 2 }} />}
          {!loading && sims.length === 0 && (
            <Typography sx={{ fontSize: 12, color: 'var(--text-3)', m: 2 }}>
              No saved simulations yet. In the <b>Simulation</b> tab, run a solve and press
              <b> Save simulation</b> to snapshot it here.
            </Typography>
          )}
          {sims.length > 0 && (
            <Box component="table" sx={{ borderCollapse: 'collapse', width: '100%' }}>
              <Box component="thead"><Box component="tr">
                <Box component="th" sx={{ ...TH, textAlign: 'center', left: 0, zIndex: 3, position: 'sticky' }}>✓</Box>
                <Box component="th" sx={{ ...TH, textAlign: 'left', left: 36, zIndex: 3 }}>Name</Box>
                {displayCols.map(k => (
                  <Box component="th" key={k} sx={TH}>
                    {paramLabel(k)}{paramUnit(k) ? <Box component="span" sx={{ color: 'var(--line)', fontWeight: 400 }}> {paramUnit(k)}</Box> : null}
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
                    <Box component="td" sx={{ ...TD, p: 0, position: 'sticky', left: 36,
                      bgcolor: sel.has(s.id) ? 'var(--panel-2)' : 'var(--panel-2)', zIndex: 1 }}>
                      <Box sx={{ px: 1.25, py: 0.55 }}><NameCell s={s} /></Box>
                    </Box>
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
            {selected.length} selected · {diffKeys.length} differing input{diffKeys.length === 1 ? '' : 's'} · green = best / red = worst per result
          </Typography>
        </Box>
        <Box sx={{ flex: 1, overflow: 'auto', px: 2, pb: 2 }}>
          {selected.length < 2 ? (
            <Alert severity="info" sx={{ fontSize: 12 }}>Tick at least two rows above to compare them.</Alert>
          ) : (
            <Box component="table" sx={{ borderCollapse: 'collapse', width: '100%' }}>
              <Box component="thead"><Box component="tr">
                <Box component="th" sx={{ ...TH, textAlign: 'left', left: 0, zIndex: 3, position: 'sticky' }}>Simulation</Box>
                {diffKeys.map(k => (
                  <Box component="th" key={k} sx={{ ...TH, color: '#fbbf24' }}>
                    {paramLabel(k)}{paramUnit(k) ? <Box component="span" sx={{ color: 'var(--line)', fontWeight: 400 }}> {paramUnit(k)}</Box> : null}
                  </Box>
                ))}
                {RESULT_COLS.map(r => (
                  <Box component="th" key={r.key} sx={{ ...TH, color: '#4ade80' }}>
                    {r.label}{r.unit ? <Box component="span" sx={{ color: 'var(--line)', fontWeight: 400 }}> {r.unit}</Box> : null}
                  </Box>
                ))}
              </Box></Box>
              <Box component="tbody">
                {selected.map(s => (
                  <Box component="tr" key={s.id} sx={{ '&:hover': { bgcolor: 'var(--panel-2)' } }}>
                    <NameCell s={s} sticky />
                    {diffKeys.map(k => (
                      <Box component="td" key={k} sx={{ ...TD, color: '#fbbf24' }}>{paramFmt(k, s.params?.[k])}</Box>
                    ))}
                    {RESULT_COLS.map(r => {
                      const raw = Number(s.results?.[r.key]);
                      const has = Number.isFinite(raw);
                      const ext = resExtent[r.key];
                      let col = 'var(--text-1)';
                      if (has && ext && ext.min !== ext.max && r.better) {
                        const best = r.better === 'hi' ? ext.max : ext.min;
                        const worst = r.better === 'hi' ? ext.min : ext.max;
                        if (Math.abs(raw - best) < 1e-9) col = '#4ade80';
                        else if (Math.abs(raw - worst) < 1e-9) col = '#f87171';
                      }
                      return (
                        <Box component="td" key={r.key} sx={{ ...TD, color: col,
                          fontWeight: col !== 'var(--text-1)' ? 700 : 400 }}>
                          {has ? fmtNum(raw, r.d ?? 2, r.scale ?? 1) : '—'}
                        </Box>
                      );
                    })}
                  </Box>
                ))}
                {diffKeys.length === 0 && (
                  <Box component="tr"><Box component="td" {...({ colSpan: 1 + RESULT_COLS.length } as any)}
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
