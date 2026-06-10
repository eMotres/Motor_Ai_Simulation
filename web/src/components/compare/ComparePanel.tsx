/**
 * ComparePanel — "Compare" tab.
 *
 * Lists every saved simulation snapshot (saved from the Simulation tab via the
 * "Save simulation" card).  The user ticks two or more to compare; the table
 * then shows ONLY the input parameters that DIFFER between the selected runs
 * (geometry / currents / angles / mesh), next to the key FEM results.
 * Snapshots can be deleted individually or all at once.
 *
 * Store: backend JSON at config/saved_simulations.json via /api/sims/saved.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  Box, Typography, Button, Checkbox, IconButton, Paper, Tooltip, Alert,
  CircularProgress,
} from '@mui/material';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import RefreshIcon       from '@mui/icons-material/Refresh';
import CompareArrowsIcon from '@mui/icons-material/CompareArrows';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

interface SavedSim {
  id: string;
  name: string;
  created: string;
  params: Record<string, any>;
  results: Record<string, any>;
}

// ── friendly labels + units for INPUT params (fallback = raw key) ───────────
const PARAM_META: Record<string, { label: string; unit?: string; d?: number }> = {
  I_phase_rms:        { label: 'I phase RMS',     unit: 'A',  d: 1 },
  gamma_deg:          { label: 'γ (load angle)',  unit: '°',  d: 1 },
  rpm:                { label: 'Speed',           unit: 'rpm', d: 0 },
  frequency_hz:       { label: 'Frequency',       unit: 'Hz', d: 1 },
  n_sectors:          { label: 'Symmetry n_sect', d: 0 },
  steps_per_period:   { label: 'Steps/period',    d: 0 },
  coil_temp_c:        { label: 'Coil temp',       unit: '°C', d: 0 },
  end_winding_factor: { label: 'k_end',           d: 2 },
  connection:         { label: 'Winding conn.' },
  mesh_size_mm:       { label: 'Mesh size',       unit: 'mm', d: 2 },
  min_size_mm:        { label: 'Min mesh size',   unit: 'mm', d: 2 },
  num_poles:          { label: 'Poles',           d: 0 },
  num_slots:          { label: 'Slots',           d: 0 },
  num_wires_per_slot: { label: 'Wires/slot',      d: 0 },
  stator_od_mm:       { label: 'Stator OD',       unit: 'mm', d: 2 },
  stator_id_mm:       { label: 'Stator ID',       unit: 'mm', d: 2 },
  rotor_od_mm:        { label: 'Rotor OD',        unit: 'mm', d: 2 },
  airgap_mm:          { label: 'Air gap',         unit: 'mm', d: 3 },
  stack_length_mm:    { label: 'Stack length',    unit: 'mm', d: 2 },
  magnet_thickness_mm:{ label: 'Magnet thick.',   unit: 'mm', d: 2 },
  slot_depth_mm:      { label: 'Slot depth',      unit: 'mm', d: 2 },
};

// ── result rows shown for every selected run (ordered) ──────────────────────
const RESULT_ROWS: { key: string; label: string; unit?: string; d?: number;
                     scale?: number; accent?: 'green' | 'blue' | 'amber' }[] = [
  { key: 'T_em_avg_Nm',          label: 'Torque T_avg',     unit: 'N·m',     d: 2, accent: 'blue' },
  { key: 'T_ripple_pct',         label: 'Torque ripple',    unit: '%',       d: 1, accent: 'amber' },
  { key: 'P_mech_W',             label: 'Mech power',       unit: 'kW',      d: 2, scale: 1e-3, accent: 'blue' },
  { key: 'efficiency',           label: 'Efficiency η',     unit: '%',       d: 2, scale: 100, accent: 'green' },
  { key: 'P_loss_total_W',       label: 'Total loss',       unit: 'W',       d: 0, accent: 'amber' },
  { key: 'P_core_W',             label: '· Core (Fe)',      unit: 'W',       d: 0 },
  { key: 'P_stranded_W',         label: '· Copper',         unit: 'W',       d: 0 },
  { key: 'P_solid_W',            label: '· Magnet eddy',    unit: 'W',       d: 0 },
  { key: 'V_phase_peak_V',       label: 'V_phase peak',     unit: 'V',       d: 1 },
  { key: 'V_line_rms_V',         label: 'V_line RMS',       unit: 'V',       d: 1 },
  { key: 'KV_rpm_per_V_line',    label: 'KV (line)',        unit: 'rpm/V',   d: 1 },
  { key: 'mass_total_kg',        label: 'Active mass',      unit: 'kg',      d: 2 },
  { key: 'torque_per_mass_Nm_kg',label: 'Torque density',   unit: 'N·m/kg',  d: 2 },
  { key: 'power_per_mass_W_kg',  label: 'Power density',    unit: 'kW/kg',   d: 2, scale: 1e-3 },
];

const ACCENT = { green: '#4ade80', blue: '#60a5fa', amber: '#fbbf24', default: '#e2e8f0' };

function fmtNum(v: any, d = 2, scale = 1): string {
  if (v == null || v === '') return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return (n * scale).toFixed(d);
}

// equality with float tolerance (params come from clean state but geometry can carry noise)
function valEq(a: any, b: any): boolean {
  if (typeof a === 'number' && typeof b === 'number') return Math.abs(a - b) < 1e-6;
  return String(a) === String(b);
}

const ComparePanel: React.FC = () => {
  const [sims,    setSims]    = useState<SavedSim[]>([]);
  const [sel,     setSel]     = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [err,     setErr]     = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true); setErr(null);
    fetch(`${API}/api/sims/saved`)
      .then(r => r.json())
      .then(d => {
        const list: SavedSim[] = d.sims || [];
        setSims(list);
        // keep selection valid; default-select up to first 3 on first load
        setSel(prev => {
          const ids = new Set(list.map(s => s.id));
          const kept = new Set([...prev].filter(id => ids.has(id)));
          if (kept.size === 0 && list.length) list.slice(0, 3).forEach(s => kept.add(s.id));
          return kept;
        });
      })
      .catch(e => setErr(String(e)))
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => { load(); }, [load]);

  const toggle = (id: string) =>
    setSel(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const del = (id: string) => {
    fetch(`${API}/api/sims/saved/${id}`, { method: 'DELETE' })
      .then(() => load())
      .catch(e => setErr(String(e)));
  };
  const clearAll = () => {
    if (!window.confirm('Delete ALL saved simulations?')) return;
    fetch(`${API}/api/sims/saved`, { method: 'DELETE' })
      .then(() => load())
      .catch(e => setErr(String(e)));
  };

  const selected = sims.filter(s => sel.has(s.id));

  // ── differing input params across the selected runs ──────────────────────
  const allParamKeys = Array.from(
    new Set(selected.flatMap(s => Object.keys(s.params || {})))
  );
  const diffKeys = allParamKeys.filter(k => {
    const vals = selected.map(s => s.params?.[k]);
    return vals.some(v => !valEq(v, vals[0]));
  }).sort((a, b) => {
    // known params first (in PARAM_META declaration order), then alpha
    const ia = Object.keys(PARAM_META).indexOf(a);
    const ib = Object.keys(PARAM_META).indexOf(b);
    if (ia !== -1 || ib !== -1) return (ia === -1 ? 1e9 : ia) - (ib === -1 ? 1e9 : ib);
    return a.localeCompare(b);
  });

  const paramLabel = (k: string) => PARAM_META[k]?.label ?? k;
  const paramFmt = (k: string, v: any) => {
    const m = PARAM_META[k];
    if (typeof v === 'number' && m) return fmtNum(v, m.d ?? 2);
    if (typeof v === 'number') return fmtNum(v, 3);
    return v == null ? '—' : String(v);
  };

  const colW = 150;

  return (
    <Box sx={{ height: '100%', display: 'flex', bgcolor: '#060d17', overflow: 'hidden' }}>
      {/* ── LEFT: saved-sim list ── */}
      <Box sx={{ width: 300, flexShrink: 0, borderRight: '1px solid #1e293b',
        p: 2, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 1 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Typography sx={{ fontSize: '0.7rem', fontWeight: 700, color: '#475569',
            letterSpacing: '0.1em', textTransform: 'uppercase' }}>
            Saved simulations ({sims.length})
          </Typography>
          <Box>
            <Tooltip title="Reload list">
              <IconButton size="small" onClick={load} sx={{ color: '#64748b' }}>
                <RefreshIcon sx={{ fontSize: 16 }} />
              </IconButton>
            </Tooltip>
            {sims.length > 0 && (
              <Tooltip title="Delete all">
                <IconButton size="small" onClick={clearAll} sx={{ color: '#7f1d1d' }}>
                  <DeleteOutlineIcon sx={{ fontSize: 16 }} />
                </IconButton>
              </Tooltip>
            )}
          </Box>
        </Box>

        {err && <Alert severity="error" sx={{ fontSize: 11 }}>{err}</Alert>}
        {loading && <CircularProgress size={18} sx={{ color: '#3b82f6', alignSelf: 'center', my: 2 }} />}

        {!loading && sims.length === 0 && (
          <Typography sx={{ fontSize: 11, color: '#64748b', mt: 2 }}>
            No saved simulations yet. Run a simulation in the <b>Simulation</b> tab,
            then press <b>Save simulation</b> to snapshot it here for comparison.
          </Typography>
        )}

        {sims.map(s => (
          <Paper key={s.id} onClick={() => toggle(s.id)} sx={{
            display: 'flex', alignItems: 'center', gap: 0.5, p: 1, cursor: 'pointer',
            bgcolor: sel.has(s.id) ? '#0f2a4a' : '#0a1628',
            border: `1px solid ${sel.has(s.id) ? '#3b82f6' : '#1e293b'}`, borderRadius: 1 }}>
            <Checkbox size="small" checked={sel.has(s.id)} sx={{ p: 0.25,
              color: '#475569', '&.Mui-checked': { color: '#3b82f6' } }} />
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography sx={{ fontSize: 12, fontWeight: 600, color: '#e2e8f0',
                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {s.name}
              </Typography>
              <Typography sx={{ fontSize: 9, color: '#475569' }}>{s.created}</Typography>
            </Box>
            <Tooltip title="Delete">
              <IconButton size="small" onClick={e => { e.stopPropagation(); del(s.id); }}
                sx={{ color: '#64748b', '&:hover': { color: '#f87171' } }}>
                <DeleteOutlineIcon sx={{ fontSize: 15 }} />
              </IconButton>
            </Tooltip>
          </Paper>
        ))}
      </Box>

      {/* ── RIGHT: comparison table ── */}
      <Box sx={{ flex: 1, overflow: 'auto', p: 3 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
          <CompareArrowsIcon sx={{ color: '#60a5fa' }} />
          <Typography sx={{ fontSize: 16, fontWeight: 700, color: '#e2e8f0' }}>
            Compare simulations
          </Typography>
          <Typography sx={{ fontSize: 11, color: '#64748b' }}>
            {selected.length} selected · showing only parameters that differ
          </Typography>
        </Box>

        {selected.length < 2 ? (
          <Alert severity="info" sx={{ fontSize: 12 }}>
            Select at least two saved simulations on the left to compare them.
          </Alert>
        ) : (
          <Paper sx={{ bgcolor: '#0a1628', border: '1px solid #1e293b', borderRadius: 2,
            overflow: 'hidden', display: 'inline-block', minWidth: '100%' }}>
            <Box component="table" sx={{ borderCollapse: 'collapse', width: '100%',
              '& td, & th': { borderBottom: '1px solid #0f172a', px: 1.5, py: 0.9,
                textAlign: 'right', fontSize: 12, whiteSpace: 'nowrap' },
              '& th:first-of-type, & td:first-of-type': { textAlign: 'left',
                position: 'sticky', left: 0, bgcolor: '#0a1628', zIndex: 1 } }}>
              {/* header: run names */}
              <Box component="thead">
                <Box component="tr">
                  <Box component="th" sx={{ color: '#475569', fontWeight: 700 }}>Parameter / Result</Box>
                  {selected.map(s => (
                    <Box component="th" key={s.id} sx={{ color: '#e2e8f0', fontWeight: 700,
                      minWidth: colW, maxWidth: colW, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {s.name}
                    </Box>
                  ))}
                </Box>
              </Box>
              <Box component="tbody">
                {/* ── differing inputs ── */}
                <Box component="tr">
                  <Box component="td" {...({ colSpan: selected.length + 1 } as any)}
                    sx={{ bgcolor: '#0b1730 !important', color: '#60a5fa !important',
                      fontWeight: 700, fontSize: '10px !important', textTransform: 'uppercase',
                      letterSpacing: '0.08em', textAlign: 'left !important' }}>
                    Differing inputs ({diffKeys.length})
                  </Box>
                </Box>
                {diffKeys.length === 0 && (
                  <Box component="tr">
                    <Box component="td" sx={{ color: '#64748b' }}>—</Box>
                    {selected.map(s => (
                      <Box component="td" key={s.id} sx={{ color: '#475569' }}>identical inputs</Box>
                    ))}
                  </Box>
                )}
                {diffKeys.map(k => {
                  const u = PARAM_META[k]?.unit;
                  return (
                    <Box component="tr" key={k}>
                      <Box component="td" sx={{ color: '#94a3b8' }}>
                        {paramLabel(k)}{u ? <span style={{ color: '#475569' }}> ({u})</span> : null}
                      </Box>
                      {selected.map(s => (
                        <Box component="td" key={s.id} sx={{ color: '#fbbf24', fontFamily: 'monospace' }}>
                          {paramFmt(k, s.params?.[k])}
                        </Box>
                      ))}
                    </Box>
                  );
                })}

                {/* ── results ── */}
                <Box component="tr">
                  <Box component="td" {...({ colSpan: selected.length + 1 } as any)}
                    sx={{ bgcolor: '#0b1730 !important', color: '#4ade80 !important',
                      fontWeight: 700, fontSize: '10px !important', textTransform: 'uppercase',
                      letterSpacing: '0.08em', textAlign: 'left !important' }}>
                    FEM results
                  </Box>
                </Box>
                {RESULT_ROWS.map(row => {
                  // best/worst highlight: find min/max across selected for this metric
                  const nums = selected.map(s => Number(s.results?.[row.key]))
                    .filter(n => Number.isFinite(n));
                  const max = nums.length ? Math.max(...nums) : null;
                  const min = nums.length ? Math.min(...nums) : null;
                  return (
                    <Box component="tr" key={row.key}>
                      <Box component="td" sx={{ color: '#94a3b8' }}>
                        {row.label}{row.unit ? <span style={{ color: '#475569' }}> ({row.unit})</span> : null}
                      </Box>
                      {selected.map(s => {
                        const raw = Number(s.results?.[row.key]);
                        const has = Number.isFinite(raw);
                        const isMax = has && max != null && Math.abs(raw - max) < 1e-9 && min !== max;
                        const isMin = has && min != null && Math.abs(raw - min) < 1e-9 && min !== max;
                        const col = isMax ? ACCENT.green : isMin ? '#f87171'
                                  : (row.accent ? ACCENT[row.accent] : ACCENT.default);
                        return (
                          <Box component="td" key={s.id}
                            sx={{ color: col, fontFamily: 'monospace',
                              fontWeight: (isMax || isMin) ? 700 : 400 }}>
                            {has ? fmtNum(raw, row.d ?? 2, row.scale ?? 1) : '—'}
                          </Box>
                        );
                      })}
                    </Box>
                  );
                })}
              </Box>
            </Box>
          </Paper>
        )}
        <Typography sx={{ fontSize: 10, color: '#334155', mt: 1.5 }}>
          Green = highest value in the row, red = lowest (per metric).  Inputs shown
          are only those that differ between the selected runs.
        </Typography>
      </Box>
    </Box>
  );
};

export default ComparePanel;
