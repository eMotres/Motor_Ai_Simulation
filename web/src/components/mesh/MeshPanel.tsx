/**
 * Mesh tab — collocation-point settings for the 2D PINN solver.
 *
 * Left  : parameter sliders (n_radial, n_angular, n_angular_slots)
 *         + point-count summary table
 * Right : live SVG preview of the polar sampling grid
 *         (scaled to current geometry from /api/config)
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, Box, Button, Chip, CircularProgress, Divider,
  Paper, Slider, Tooltip, Typography,
} from '@mui/material';
import SaveIcon from '@mui/icons-material/Save';

const API = 'http://localhost:8000';

// ── types ─────────────────────────────────────────────────────────────────────
interface MeshCfg {
  n_radial:        number;
  n_angular:       number;
  n_angular_slots: number;
}

interface MotorGeo {
  stator_outer_radius: number;
  stator_inner_radius: number;
  rotor_outer_radius:  number;
  rotor_inner_radius:  number;
  num_slots:           number;
  num_poles:           number;
  shaft_radius?:       number;
}

// ── domain list (mirrors geometry_2d.py) ─────────────────────────────────────
const DOMAINS = [
  { key: 'stator_core', label: 'Stator Core', color: '#3b82f6' },
  { key: 'air_gap',     label: 'Air Gap',     color: '#94a3b8' },
  { key: 'rotor_core',  label: 'Rotor Core',  color: '#2563eb' },
  { key: 'magnet',      label: 'Magnets',     color: '#ef4444' },
  { key: 'slot',        label: 'Windings',    color: '#f59e0b' },
  { key: 'shaft',       label: 'Shaft',       color: '#64748b' },
];

// Estimate collocation points per domain (mirrors batch_size_interior in solver)
function estimatePoints(cfg: MeshCfg): Record<string, number> {
  const ring  = cfg.n_radial * cfg.n_angular;
  const slot  = cfg.n_radial * cfg.n_angular_slots;
  return {
    stator_core: ring,
    air_gap:     Math.round(ring * 0.5),
    rotor_core:  ring,
    magnet:      Math.round(slot * 1.5),
    slot:        slot,
    shaft:       Math.round(ring * 0.3),
  };
}

// ── SVG collocation preview ──────────────────────────────────────────────────
const SVG_SIZE = 420;
const CX = SVG_SIZE / 2;
const CY = SVG_SIZE / 2;

interface PreviewProps {
  cfg: MeshCfg;
  geo: MotorGeo | null;
}

const CollocationPreview: React.FC<PreviewProps> = ({ cfg, geo }) => {
  const scale = useMemo(() => {
    if (!geo) return 1;
    return (SVG_SIZE * 0.44) / geo.stator_outer_radius;
  }, [geo]);

  const r = useMemo(() => {
    if (!geo) return null;
    return {
      so: geo.stator_outer_radius * scale,
      si: geo.stator_inner_radius * scale,
      ro: geo.rotor_outer_radius  * scale,
      ri: geo.rotor_inner_radius  * scale,
      sh: (geo.shaft_radius ?? geo.rotor_inner_radius * 0.55) * scale,
    };
  }, [geo, scale]);

  // Generate radial rings between r_min and r_max with n_r layers, n_phi points each
  const ringDots = useCallback((
    r_min: number, r_max: number, n_r: number, n_phi: number, color: string, key: string
  ) => {
    const dots: React.ReactElement[] = [];
    for (let i = 0; i < n_r; i++) {
      const radius = r_min + ((r_max - r_min) / Math.max(n_r - 1, 1)) * i;
      for (let j = 0; j < n_phi; j++) {
        const phi = (2 * Math.PI / n_phi) * j;
        const x = CX + radius * Math.cos(phi);
        const y = CY + radius * Math.sin(phi);
        dots.push(
          <circle key={`${key}-${i}-${j}`} cx={x} cy={y} r={1.2}
            fill={color} opacity={0.65}/>
        );
      }
    }
    return dots;
  }, []);

  const dots = useMemo(() => {
    if (!r) return null;
    const nR = Math.max(2, Math.min(cfg.n_radial,  20));
    const nA = Math.max(8, Math.min(cfg.n_angular, 128));
    const nS = Math.max(4, Math.min(cfg.n_angular_slots, 32));
    return [
      ...ringDots(r.sh, r.ri, nR, nA, '#64748b', 'shaft'),
      ...ringDots(r.ri, r.ro, nR, nA, '#3b82f6', 'rotor'),
      ...ringDots(r.ro, r.si, nR, nS, '#f59e0b', 'gap'),
      ...ringDots(r.si, r.so, nR, nA, '#2563eb', 'stator'),
    ];
  }, [r, cfg, ringDots]);

  return (
    <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
      <svg width={SVG_SIZE} height={SVG_SIZE} style={{ background: '#060d17', borderRadius: 12 }}>
        {r && <>
          {/* domain annuli */}
          <circle cx={CX} cy={CY} r={r.so} fill="none" stroke="#1e293b" strokeWidth={1}/>
          <circle cx={CX} cy={CY} r={r.si} fill="none" stroke="#334155" strokeWidth={0.8}/>
          <circle cx={CX} cy={CY} r={r.ro} fill="none" stroke="#334155" strokeWidth={0.8}/>
          <circle cx={CX} cy={CY} r={r.ri} fill="none" stroke="#334155" strokeWidth={0.8}/>
          <circle cx={CX} cy={CY} r={r.sh} fill="none" stroke="#1e293b" strokeWidth={0.8}/>
          {/* filled regions */}
          <circle cx={CX} cy={CY} r={r.so} fill="#1e293b22"/>
          <circle cx={CX} cy={CY} r={r.si} fill="#0a1628"/>
          <circle cx={CX} cy={CY} r={r.ri} fill="#1e3a5f22"/>
          <circle cx={CX} cy={CY} r={r.sh} fill="#0f172a"/>
        </>}
        {dots}
        {/* center cross */}
        <line x1={CX - 6} y1={CY} x2={CX + 6} y2={CY} stroke="#334155" strokeWidth={0.8}/>
        <line x1={CX} y1={CY - 6} x2={CX} y2={CY + 6} stroke="#334155" strokeWidth={0.8}/>
      </svg>
    </Box>
  );
};

// ─────────────────────────────────────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────────────────────────────────────
const MeshPanel: React.FC = () => {
  const [cfg,     setCfg]     = useState<MeshCfg>({ n_radial: 10, n_angular: 64, n_angular_slots: 8 });
  const [geo,     setGeo]     = useState<MotorGeo | null>(null);
  const [saving,  setSaving]  = useState(false);
  const [saved,   setSaved]   = useState(false);
  const [error,   setError]   = useState<string | null>(null);

  // Load current config + geometry on mount
  useEffect(() => {
    fetch(`${API}/api/mesh/config`)
      .then(r => r.json())
      .then(d => setCfg(d))
      .catch(() => {});

    fetch(`${API}/api/geometry/summary`)
      .then(r => r.json())
      .then(d => setGeo(d))
      .catch(() => {});
  }, []);

  const totalPoints = useMemo(() => {
    const pts = estimatePoints(cfg);
    return Object.values(pts).reduce((a, b) => a + b, 0);
  }, [cfg]);

  const pointsPerDomain = useMemo(() => estimatePoints(cfg), [cfg]);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const r = await fetch(`${API}/api/mesh/config`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cfg),
      });
      if (!r.ok) throw new Error(await r.text());
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Box sx={{ display: 'flex', height: '100%', overflow: 'hidden', bgcolor: '#060d17' }}>

      {/* ── LEFT: controls ── */}
      <Box sx={{
        width: 320, flexShrink: 0, overflowY: 'auto',
        borderRight: '1px solid #1e293b', p: 2,
        display: 'flex', flexDirection: 'column', gap: 2,
      }}>

        <Box>
          <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: '#475569',
            letterSpacing: '0.1em', textTransform: 'uppercase', mb: 0.5 }}>
            Collocation Points
          </Typography>
          <Typography sx={{ fontSize: 11, color: '#334155' }}>
            PINN samples random points inside each domain. Higher density →
            better accuracy, slower training.
          </Typography>
        </Box>

        <Divider sx={{ borderColor: '#1e293b' }}/>

        {/* n_radial */}
        <Box>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
            <Typography sx={{ fontSize: 12, color: '#94a3b8' }}>
              Radial layers
              <Tooltip title="Number of concentric circles of sample points per domain" placement="right">
                <span style={{ color: '#475569', marginLeft: 4, cursor: 'help' }}>ⓘ</span>
              </Tooltip>
            </Typography>
            <Chip label={cfg.n_radial} size="small"
              sx={{ fontSize: 11, height: 20, bgcolor: '#1e3a5f', color: '#93c5fd' }}/>
          </Box>
          <Slider
            value={cfg.n_radial} min={2} max={30} step={1}
            onChange={(_, v) => setCfg(c => ({ ...c, n_radial: v as number }))}
            sx={{ color: '#3b82f6' }}
          />
        </Box>

        {/* n_angular */}
        <Box>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
            <Typography sx={{ fontSize: 12, color: '#94a3b8' }}>
              Angular divisions
              <Tooltip title="Points around each radial ring in the main domains" placement="right">
                <span style={{ color: '#475569', marginLeft: 4, cursor: 'help' }}>ⓘ</span>
              </Tooltip>
            </Typography>
            <Chip label={cfg.n_angular} size="small"
              sx={{ fontSize: 11, height: 20, bgcolor: '#1e3a5f', color: '#93c5fd' }}/>
          </Box>
          <Slider
            value={cfg.n_angular} min={8} max={256} step={8}
            onChange={(_, v) => setCfg(c => ({ ...c, n_angular: v as number }))}
            sx={{ color: '#3b82f6' }}
          />
        </Box>

        {/* n_angular_slots */}
        <Box>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
            <Typography sx={{ fontSize: 12, color: '#94a3b8' }}>
              Slot angular divisions
              <Tooltip title="Denser angular sampling inside slots/magnets for accuracy" placement="right">
                <span style={{ color: '#475569', marginLeft: 4, cursor: 'help' }}>ⓘ</span>
              </Tooltip>
            </Typography>
            <Chip label={cfg.n_angular_slots} size="small"
              sx={{ fontSize: 11, height: 20, bgcolor: '#1e3a5f', color: '#93c5fd' }}/>
          </Box>
          <Slider
            value={cfg.n_angular_slots} min={2} max={64} step={2}
            onChange={(_, v) => setCfg(c => ({ ...c, n_angular_slots: v as number }))}
            sx={{ color: '#f59e0b' }}
          />
        </Box>

        <Divider sx={{ borderColor: '#1e293b' }}/>

        {/* Point count table */}
        <Box>
          <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: '#475569',
            letterSpacing: '0.1em', textTransform: 'uppercase', mb: 1 }}>
            Estimated Sample Points
          </Typography>

          {DOMAINS.map(d => (
            <Box key={d.key} sx={{ display: 'flex', alignItems: 'center',
              justifyContent: 'space-between', py: 0.35 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                <Box sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: d.color, flexShrink: 0 }}/>
                <Typography sx={{ fontSize: 11, color: '#64748b' }}>{d.label}</Typography>
              </Box>
              <Typography sx={{ fontSize: 11, fontWeight: 600, color: '#94a3b8', fontVariantNumeric: 'tabular-nums' }}>
                {(pointsPerDomain[d.key] ?? 0).toLocaleString()}
              </Typography>
            </Box>
          ))}

          <Divider sx={{ borderColor: '#1e293b', my: 0.75 }}/>
          <Box sx={{ display: 'flex', justifyContent: 'space-between' }}>
            <Typography sx={{ fontSize: 12, fontWeight: 700, color: '#94a3b8' }}>Total</Typography>
            <Typography sx={{ fontSize: 12, fontWeight: 700, color: '#e2e8f0', fontVariantNumeric: 'tabular-nums' }}>
              {totalPoints.toLocaleString()}
            </Typography>
          </Box>
        </Box>

        <Divider sx={{ borderColor: '#1e293b' }}/>

        <Button
          variant="contained" color="primary" fullWidth
          startIcon={saving ? <CircularProgress size={14} color="inherit"/> : <SaveIcon/>}
          onClick={handleSave}
          disabled={saving}
          sx={{ py: 1.1, fontWeight: 700, letterSpacing: 1 }}
        >
          {saving ? 'SAVING…' : saved ? 'SAVED ✓' : 'SAVE TO CONFIG'}
        </Button>

        {error && <Alert severity="error" sx={{ fontSize: 11 }}>{error}</Alert>}
      </Box>

      {/* ── RIGHT: preview ── */}
      <Box sx={{ flex: 1, display: 'flex', flexDirection: 'column', p: 3, gap: 2 }}>

        <Box>
          <Typography variant="h6" sx={{ color: '#e2e8f0', fontWeight: 700, mb: 0.5 }}>
            Collocation Sampling Grid
          </Typography>
          <Typography sx={{ fontSize: 12, color: '#475569' }}>
            Live preview of point distribution used during PINN training.
            Each coloured ring corresponds to one domain.
          </Typography>
        </Box>

        {/* Legend */}
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75 }}>
          {DOMAINS.map(d => (
            <Chip key={d.key} label={d.label} size="small" sx={{
              fontSize: 10, height: 20,
              bgcolor: `${d.color}20`, color: d.color,
              border: `1px solid ${d.color}40`,
            }}/>
          ))}
        </Box>

        {/* SVG preview */}
        <Paper sx={{ flex: 1, bgcolor: '#060d17', border: '1px solid #1e293b',
          borderRadius: 2, overflow: 'hidden', display: 'flex',
          alignItems: 'center', justifyContent: 'center' }}>
          <CollocationPreview cfg={cfg} geo={geo}/>
        </Paper>
      </Box>
    </Box>
  );
};

export default MeshPanel;
