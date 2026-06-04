/**
 * ParetoResults — for every sampled geometry, two points (at currents I1 & I2)
 * joined by a segment (the design's load line).  X = efficiency, Y = torque
 * density (both maximized → the front is the up-right envelope).  Designs whose
 * cogging ripple exceeds the threshold are excluded from the front (drawn faint).
 */
import React from 'react';
import {
  Box, Typography, Table, TableBody, TableCell, TableHead, TableRow,
  Button, Chip, Tooltip, TextField, CircularProgress,
} from '@mui/material';
import {
  ScatterChart, Scatter, XAxis, YAxis, ZAxis, CartesianGrid,
  Tooltip as RcTooltip, ResponsiveContainer, Legend, ReferenceLine,
} from 'recharts';
import type { OptimizationResult, OptDesignPoint } from '../../types/motor';
import { useMotorStore } from '../../stores/motorStore';

// current_a / rpm are reported on their own line; every other override key is a
// swept design variable (geometry params AND the load angle γ) → shown below.
const OPERATING_KEYS = new Set(['current_a', 'rpm']);
const fmt = (v: number, n = 2) => (Number.isFinite(v) ? v.toFixed(n) : '–');

type VarMeta = Record<string, { label: string; unit: string }>;

interface Pt { x: number; y: number; d: OptDesignPoint; }

const ParetoTooltip: React.FC<any> = ({ active, payload, varMeta = {} as VarMeta }) => {
  if (!active || !payload?.length) return null;
  const d: OptDesignPoint = payload[0].payload.d;
  if (!d) return null;
  const ov = Object.entries(d.overrides || {}).filter(([k]) => !OPERATING_KEYS.has(k));
  return (
    <Box sx={{ bgcolor: '#0f172a', border: '1px solid #1e293b', p: 1, fontSize: 11, color: '#cbd5e1', borderRadius: 1 }}>
      {d.fem && <div style={{ color: '#22c55e', fontWeight: 700 }}>● FEM transient</div>}
      <div><b>η = {(d.efficiency * 100).toFixed(2)} %</b> · T/mass = {fmt(d.torque_per_mass_Nm_kg)} N·m/kg</div>
      <div>I = {fmt(d.current_a, 0)} A · T = {fmt(d.T_em_Nm)} N·m · mass = {fmt(d.mass_total_kg)} kg</div>
      <div style={{ color: d.eligible ? '#34d399' : '#f87171' }}>
        ripple ≈ {fmt(d.T_ripple_pct, 1)} % {d.eligible ? '' : '(over limit)'}
      </div>
      <div style={{ color: '#94a3b8' }}>loss {fmt(d.P_loss_total_W, 0)} W (Cu {fmt(d.P_cu_W, 0)}/Fe {fmt(d.P_fe_W, 0)}/Mg {fmt(d.P_mag_W, 0)})</div>
      {ov.length > 0 && (
        <div style={{ marginTop: 5, borderTop: '1px solid #1e293b', paddingTop: 4 }}>
          <div style={{ color: '#64748b', fontSize: 10, letterSpacing: 0.5 }}>SWEEP VARIABLES</div>
          {ov.map(([k, v]) => {
            const m = (varMeta as VarMeta)[k] ?? { label: k, unit: '' };
            return (
              <div key={k} style={{ color: '#fbbf24' }}>
                {m.label} = <b>{(v as number).toFixed(2)}</b>{m.unit ? ` ${m.unit}` : ''}
              </div>
            );
          })}
        </div>
      )}
    </Box>
  );
};

// Keys that are NOT geometry parameters (so they must not be pushed to the
// geometry API when applying a design).
const NON_GEOM_KEYS = new Set(['gamma_deg', 'current_a', 'rpm']);

const ParetoResults: React.FC<{ result: OptimizationResult }> = ({ result }) => {
  const updateGeometryViaApi = useMotorStore(s => s.updateGeometryViaApi);
  const updateOperatingPoint = useMotorStore(s => s.updateOperatingPoint);
  const parameterSchema = useMotorStore(s => s.parameterSchema);

  // Display label + unit for each swept variable: geometry params from the
  // schema, plus the load angle γ (not a geometry param, so add it explicitly).
  const varMeta: VarMeta = React.useMemo(() => {
    const m: VarMeta = { gamma_deg: { label: 'γ load angle', unit: '°' } };
    parameterSchema.forEach(p => { m[p.name] = { label: p.label, unit: p.unit }; });
    return m;
  }, [parameterSchema]);
  const refineFront = useMotorStore(s => s.refineFront);
  const refineRunning = useMotorStore(s => s.refineRunning);
  const refineProgress = useMotorStore(s => s.refineProgress);
  const refineResults = useMotorStore(s => s.refineResults);
  const refineError = useMotorStore(s => s.refineError);
  const saveCurrentResult = useMotorStore(s => s.saveCurrentResult);
  const [steps, setSteps] = React.useState(40);
  const [saveName, setSaveName] = React.useState('');
  const [saved, setSaved] = React.useState(false);

  const pts = result.points;
  const frontSet = new Set(result.pareto_indices);

  // Two clouds (both currents, so the axes span the whole space → segments stay
  // on-chart): eligible (passed the ripple gate) vs filtered (failed it).
  const eligIdx: number[] = [];
  const filtIdx: number[] = [];
  pts.forEach((d, i) => {
    if (!d.feasible || frontSet.has(i)) return;
    (d.eligible ? eligIdx : filtIdx).push(i);
  });
  // Axes: X = torque density (N·m/kg), Y = efficiency (%).
  const downsample = (idx: number[], max: number) => {
    const s = Math.max(1, Math.ceil(idx.length / max));
    return idx.filter((_, k) => k % s === 0)
      .map(i => ({ x: pts[i].torque_per_mass_Nm_kg, y: pts[i].efficiency * 100, d: pts[i] }));
  };
  const cloud: Pt[] = downsample(eligIdx, 400);       // eligible
  const filtered: Pt[] = downsample(filtIdx, 400);    // failed ripple gate

  const front: Pt[] = result.pareto_indices
    .map(i => ({ x: pts[i].torque_per_mass_Nm_kg, y: pts[i].efficiency * 100, d: pts[i] }))
    .sort((a, b) => a.x - b.x);

  const base = result.baseline;
  const basePt: Pt[] = base?.feasible
    ? [{ x: base.torque_per_mass_Nm_kg, y: base.efficiency * 100, d: base }]
    : [];

  // FEM-refined points (real transient) overlaid on the analytical scatter.
  const refinedPts: Pt[] = (refineResults || [])
    .filter(d => d && d.efficiency > 0 && d.torque_per_mass_Nm_kg > 0)
    .map(d => ({ x: d.torque_per_mass_Nm_kg, y: d.efficiency * 100, d }));

  // Segment lines (one per geometry, I1→I2), downsampled.
  const segArr = result.segments ?? [];
  const MAX_SEG = 150;
  const sstride = Math.max(1, Math.ceil(segArr.length / MAX_SEG));
  const segLines = segArr
    .filter((_, k) => k % sstride === 0)
    .map(([a, b]) => {
      const pa = pts[a], pb = pts[b];
      if (!pa?.feasible || !pb?.feasible) return null;
      return {
        x1: pa.torque_per_mass_Nm_kg, y1: pa.efficiency * 100,
        x2: pb.torque_per_mass_Nm_kg, y2: pb.efficiency * 100,
        elig: pa.eligible && pb.eligible,
      };
    })
    .filter(Boolean) as { x1: number; y1: number; x2: number; y2: number; elig: boolean }[];

  const varNames = result.variables.map(v => v.name);
  const ops = result.operating_points || [];

  const applyDesign = (d: OptDesignPoint) => {
    const geoOverrides: Record<string, number> = {};
    Object.entries(d.overrides || {}).forEach(([k, v]) => {
      if (!NON_GEOM_KEYS.has(k)) geoOverrides[k] = v as number;
    });
    if (Object.keys(geoOverrides).length) updateGeometryViaApi(geoOverrides as any);
    if (d.current_a) updateOperatingPoint(0, { current_a: d.current_a, rpm: d.rpm });
  };

  return (
    <Box sx={{ mt: 3 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1, flexWrap: 'wrap' }}>
        <Typography variant="overline" sx={{ fontSize: 10, letterSpacing: 1, color: 'text.secondary' }}>
          Pareto Front — Torque density vs Efficiency
        </Typography>
        <Chip size="small" label={`${result.n_geometries} geom × ${ops.length} A = ${result.n_total_points} pts`} variant="outlined" sx={{ height: 18, fontSize: 10 }} />
        <Chip size="small" color="info" label={`${result.n_built ?? '?'} built`} variant="outlined" sx={{ height: 18, fontSize: 10 }} />
        {(result.n_failed ?? 0) > 0 && (
          <Tooltip title="These candidate geometries couldn't be meshed (CadQuery/gmsh TopologyException for extreme dimensions) — narrow the variable ranges to reduce them." placement="top">
            <Chip size="small" color="error" variant="outlined" label={`${result.n_failed} failed to build`} sx={{ height: 18, fontSize: 10 }} />
          </Tooltip>
        )}
        <Chip size="small" color="success" label={`${result.n_eligible_points} pass ripple ≤ ${result.ripple_max_pct.toFixed(0)}%`} sx={{ height: 18, fontSize: 10 }} />
        <Chip size="small" color="warning" label={`${result.pareto_indices.length} on front`} sx={{ height: 18, fontSize: 10 }} />
        <Tooltip title="Every point is a REAL sliding-band FEM transient — geometry + mesh rebuilt per candidate — at the scan step count. Each geometry → two currents (I1, I2) joined by a load line. Refine the front at a higher step count for accurate ripple." placement="top">
          <span style={{ color: '#475569', fontSize: 11, cursor: 'help' }}>ⓘ</span>
        </Tooltip>
      </Box>

      {/* ── FEM refinement of the front ── */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1.5, flexWrap: 'wrap' }}>
        <TextField label="steps / period" type="number" size="small" value={steps}
          onChange={e => setSteps(Math.max(8, Math.min(180, Math.round(+e.target.value) || 40)))}
          inputProps={{ min: 8, max: 180, style: { fontSize: 12, padding: '4px 8px', width: 56 } }}
          InputLabelProps={{ sx: { fontSize: 11 } }} />
        <Button variant="outlined" size="small" color="success"
          disabled={refineRunning || result.pareto_indices.length === 0}
          startIcon={refineRunning ? <CircularProgress size={13} color="inherit" /> : undefined}
          onClick={() => refineFront(steps)} sx={{ textTransform: 'none', fontSize: 12 }}>
          {refineRunning
            ? `Refining ${refineProgress?.done ?? 0}/${refineProgress?.total ?? 0}…`
            : `Refine front with FEM (${result.pareto_indices.length} pts × ${steps} steps)`}
        </Button>
        {refinedPts.length > 0 && (
          <Chip size="small" color="success" variant="outlined"
            label={`${refinedPts.length} FEM points`} sx={{ height: 18, fontSize: 10 }} />
        )}
        <Typography variant="caption" color="text.disabled">
          ≈1–2 min per point · in-memory (Simulation untouched)
        </Typography>
        {refineError && <Typography color="error" variant="caption">{refineError}</Typography>}

        <Box sx={{ flex: 1 }} />
        {/* Save this scan result to disk */}
        <TextField placeholder="name…" size="small" value={saveName}
          onChange={e => { setSaveName(e.target.value); setSaved(false); }}
          inputProps={{ style: { fontSize: 11, padding: '4px 8px', width: 110 } }} />
        <Button variant="contained" size="small" color="info"
          onClick={async () => { await saveCurrentResult(saveName); setSaved(true); setSaveName(''); }}
          sx={{ textTransform: 'none', fontSize: 12 }}>
          {saved ? 'Saved ✓' : 'Save'}
        </Button>
      </Box>

      <Box sx={{ height: 340, bgcolor: 'rgba(255,255,255,0.02)', borderRadius: 1, p: 1 }}>
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 8, right: 16, bottom: 24, left: 8 }}>
            <CartesianGrid stroke="#1e293b" strokeDasharray="2 4" />
            <XAxis type="number" dataKey="x" name="Torque density"
              domain={['dataMin - 0.2', 'dataMax + 0.2']}
              tickFormatter={(v: number) => v.toFixed(1)}
              tick={{ fontSize: 10, fill: '#94a3b8' }}
              label={{ value: 'Torque/mass [N·m/kg]', position: 'insideBottom', offset: -12, style: { fontSize: 10, fill: '#64748b' } }} />
            <YAxis type="number" dataKey="y" name="Efficiency" unit="%"
              domain={['dataMin - 0.3', 'dataMax + 0.3']}
              tickFormatter={(v: number) => v.toFixed(1)}
              tick={{ fontSize: 10, fill: '#94a3b8' }}
              label={{ value: 'Efficiency [%]', angle: -90, position: 'insideLeft', style: { fontSize: 10, fill: '#64748b' } }} />
            <ZAxis range={[22, 22]} />
            {/* Load-line segments (one per geometry, I1→I2) drawn via recharts'
                own coordinate mapping. */}
            {segLines.map((s, i) => (
              <ReferenceLine key={`seg${i}`} ifOverflow="hidden"
                segment={[{ x: s.x1, y: s.y1 }, { x: s.x2, y: s.y2 }]}
                stroke={s.elig ? '#94a3b8' : '#f87171'}
                strokeOpacity={s.elig ? 0.7 : 0.4} strokeWidth={1.5} />
            ))}
            <RcTooltip content={<ParetoTooltip varMeta={varMeta} />} cursor={{ strokeDasharray: '3 3' }} />
            <Legend wrapperStyle={{ fontSize: 10, paddingTop: 6 }} iconSize={9}
              formatter={(v: string) => <span style={{ marginRight: 10, color: '#94a3b8' }}>{v}</span>} />
            <Scatter name="filtered" data={filtered} fill="#7f1d1d" fillOpacity={0.45} />
            <Scatter name="eligible" data={cloud} fill="#64748b" fillOpacity={0.65} />
            <Scatter name="front" data={front} fill="#f59e0b" line={{ stroke: '#f59e0b', strokeWidth: 1 }} shape="circle" />
            <Scatter name="refined" data={refinedPts} fill="#22c55e" shape="star" />
            <Scatter name="baseline" data={basePt} fill="#3b82f6" shape="diamond" />
          </ScatterChart>
        </ResponsiveContainer>
      </Box>

      <Box sx={{ mt: 2, overflowX: 'auto' }}>
        <Table size="small" sx={{ '& td, & th': { fontSize: 11, py: 0.5, borderColor: '#1e293b' } }}>
          <TableHead>
            <TableRow>
              <TableCell>η&nbsp;%</TableCell>
              <TableCell align="right">T/mass</TableCell>
              <TableCell align="right">T&nbsp;[N·m]</TableCell>
              <TableCell align="right">I&nbsp;[A]</TableCell>
              <TableCell align="right">ripple&nbsp;%</TableCell>
              <TableCell align="right">mass</TableCell>
              <TableCell align="right">loss&nbsp;[W]</TableCell>
              {varNames.map(n => (
                <TableCell key={n} align="right">
                  {varMeta[n]?.label ?? n}{varMeta[n]?.unit ? ` [${varMeta[n].unit}]` : ''}
                </TableCell>
              ))}
              <TableCell />
            </TableRow>
          </TableHead>
          <TableBody>
            {front.map((p, i) => (
              <TableRow key={i} hover>
                <TableCell sx={{ color: '#fbbf24', fontWeight: 700 }}>{(p.d.efficiency * 100).toFixed(2)}</TableCell>
                <TableCell align="right">{fmt(p.d.torque_per_mass_Nm_kg)}</TableCell>
                <TableCell align="right">{fmt(p.d.T_em_Nm, 1)}</TableCell>
                <TableCell align="right">{fmt(p.d.current_a, 0)}</TableCell>
                <TableCell align="right" sx={{ color: '#34d399' }}>{fmt(p.d.T_ripple_pct, 1)}</TableCell>
                <TableCell align="right">{fmt(p.d.mass_total_kg)}</TableCell>
                <TableCell align="right">{fmt(p.d.P_loss_total_W, 0)}</TableCell>
                {varNames.map(n => (
                  <TableCell key={n} align="right">
                    {p.d.overrides && n in p.d.overrides ? (p.d.overrides[n] as number).toFixed(2) : '–'}
                  </TableCell>
                ))}
                <TableCell align="right">
                  <Button size="small" variant="outlined" sx={{ fontSize: 9, py: 0, px: 0.75, minWidth: 0 }}
                    onClick={() => applyDesign(p.d)}>Apply</Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>
      <Typography variant="caption" color="text.disabled" sx={{ display: 'block', mt: 1 }}>
        Each thin segment = one geometry's load line (I1→I2). Diamond = baseline. Points are real FEM (coarse ripple at
        a low step count); green stars = the front re-run at a higher step count. <b>Apply</b> writes a design's geometry + current to the model.
      </Typography>
    </Box>
  );
};

export default ParetoResults;
