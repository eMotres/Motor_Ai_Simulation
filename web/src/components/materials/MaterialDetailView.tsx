import React, { useMemo } from 'react';
import { Box, Typography, Chip, Divider } from '@mui/material';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip as RechartTooltip, Legend, ResponsiveContainer, ReferenceLine,
} from 'recharts';
import type {
  MaterialsLibrary, SelectedMaterial,
  SteelData, MagnetData, ConductorData,
} from './useMaterialsLibrary';

// ─── Color palette for multi-freq loss curves ─────────────────────────────────
const FREQ_COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#a78bfa', '#06b6d4'];

// ─── Shared chart styles ──────────────────────────────────────────────────────
const CHART_STYLE = {
  backgroundColor: 'transparent',
  fontSize: 11,
};
const AXIS_STYLE = { fontSize: 10, fill: '#64748b' };
const GRID_STROKE = '#1e293b';
const TOOLTIP_STYLE: React.CSSProperties = {
  backgroundColor: '#1e293b',
  border: '1px solid #334155',
  borderRadius: 6,
  fontSize: 11,
};

// ─── Prop row ─────────────────────────────────────────────────────────────────
const Row: React.FC<{ label: string; value: string; unit?: string; highlight?: boolean }> = ({
  label, value, unit, highlight,
}) => (
  <Box sx={{
    display: 'flex', alignItems: 'baseline', gap: 1,
    py: '3px', px: 1,
    '&:hover': { bgcolor: 'rgba(255,255,255,0.02)' },
    borderRadius: 1,
  }}>
    <Typography sx={{ flex: 1, fontSize: '0.7rem', color: '#64748b' }}>{label}</Typography>
    <Typography sx={{
      fontSize: '0.75rem',
      fontWeight: highlight ? 700 : 400,
      color: highlight ? '#e2e8f0' : '#94a3b8',
      fontVariantNumeric: 'tabular-nums',
    }}>
      {value}
    </Typography>
    {unit && (
      <Typography sx={{ fontSize: '0.65rem', color: '#475569', minWidth: 50 }}>{unit}</Typography>
    )}
  </Box>
);

// ─── Section box ─────────────────────────────────────────────────────────────
const Section: React.FC<{ title: string; children: React.ReactNode; accentColor?: string }> = ({
  title, children, accentColor = '#3b82f6',
}) => (
  <Box sx={{
    mb: 2,
    bgcolor: 'rgba(15,23,42,0.6)',
    border: '1px solid #1e293b',
    borderRadius: 2,
    overflow: 'hidden',
  }}>
    <Box sx={{
      px: 2, py: 1,
      borderBottom: '1px solid #1e293b',
      display: 'flex', alignItems: 'center', gap: 1,
    }}>
      <Box sx={{ width: 3, height: 14, bgcolor: accentColor, borderRadius: 1, flexShrink: 0 }} />
      <Typography sx={{ fontSize: '0.7rem', fontWeight: 700, color: '#64748b', letterSpacing: '0.08em', textTransform: 'uppercase' }}>
        {title}
      </Typography>
    </Box>
    <Box sx={{ p: 1 }}>
      {children}
    </Box>
  </Box>
);

// ─── BH Curve chart ───────────────────────────────────────────────────────────
const BHChart: React.FC<{ points: [number, number][]; xLabel: string; yLabel: string; lineColor: string; refLines?: { x?: number; y?: number; label: string }[] }> = ({
  points, xLabel, yLabel, lineColor, refLines = [],
}) => {
  const data = points.map(([x, y]) => ({ x, y }));

  // Use absolute max to correctly determine units for negative-H demagnetisation curves
  const xAbsMax = Math.max(...data.map(d => Math.abs(d.x)));
  const xUnit = xAbsMax > 500_000 ? 'MA/m' : xAbsMax > 500 ? 'kA/m' : 'A/m';
  const xScale = xAbsMax > 500_000 ? 1e6 : xAbsMax > 500 ? 1e3 : 1;
  const scaledData = data.map(d => ({ x: +(d.x / xScale).toFixed(4), y: d.y }));

  const formattedXLabel = `${xLabel} [${xUnit}]`;

  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={scaledData} margin={{ top: 8, right: 16, bottom: 24, left: 8 }} style={CHART_STYLE}>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} />
        <XAxis
          dataKey="x"
          type="number"
          domain={['dataMin', 'dataMax']}
          tickCount={6}
          tick={AXIS_STYLE}
          label={{ value: formattedXLabel, position: 'insideBottom', offset: -14, style: { fontSize: 10, fill: '#475569' } }}
        />
        <YAxis
          tick={AXIS_STYLE}
          width={36}
          label={{ value: yLabel, angle: -90, position: 'insideLeft', offset: 8, style: { fontSize: 10, fill: '#475569' } }}
        />
        <RechartTooltip
          contentStyle={TOOLTIP_STYLE}
          formatter={(v: number) => [v.toFixed(4), yLabel]}
          labelFormatter={(l: number) => `${xLabel}: ${l} ${xUnit}`}
        />
        {refLines.map((r, i) => (
          r.x !== undefined
            ? <ReferenceLine key={i} x={+(r.x / xScale).toFixed(4)} stroke="#ef4444" strokeDasharray="4 2" label={{ value: r.label, fill: '#ef4444', fontSize: 9 }} />
            : <ReferenceLine key={i} y={r.y} stroke="#10b981" strokeDasharray="4 2" label={{ value: r.label, fill: '#10b981', fontSize: 9 }} />
        ))}
        <Line
          type="monotone"
          dataKey="y"
          stroke={lineColor}
          dot={false}
          strokeWidth={2}
          name={yLabel}
        />
      </LineChart>
    </ResponsiveContainer>
  );
};

// ─── Core loss multi-frequency chart ─────────────────────────────────────────
const CoreLossChart: React.FC<{
  curves: Record<string, [number, number][]>;
  unit: string;
}> = ({ curves, unit }) => {
  // Sort frequencies numerically: "50Hz" → 50, "1000Hz" → 1000, etc.
  const freqs = Object.keys(curves).sort(
    (a, b) => parseInt(a, 10) - parseInt(b, 10)
  );

  // Build unified dataset: each point is { B: number, [freq]: number }
  const dataset = useMemo(() => {
    const map = new Map<number, Record<string, number>>();
    freqs.forEach(freq => {
      curves[freq].forEach(([b, p]) => {
        const key = +b.toFixed(4);
        if (!map.has(key)) map.set(key, { B: key });
        map.get(key)![freq] = p;
      });
    });
    return Array.from(map.values()).sort((a, b) => a.B - b.B);
  }, [curves, freqs]);

  const yLabel = unit === 'w_per_kg' ? 'P [W/kg]' : 'P [W/m³]';

  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={dataset} margin={{ top: 8, right: 16, bottom: 24, left: 8 }} style={CHART_STYLE}>
        <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} />
        <XAxis
          dataKey="B"
          type="number"
          domain={['dataMin', 'dataMax']}
          tickCount={6}
          tick={AXIS_STYLE}
          label={{ value: 'B [T]', position: 'insideBottom', offset: -14, style: { fontSize: 10, fill: '#475569' } }}
        />
        <YAxis
          tick={AXIS_STYLE}
          width={40}
          label={{ value: yLabel, angle: -90, position: 'insideLeft', offset: 8, style: { fontSize: 10, fill: '#475569' } }}
        />
        <RechartTooltip
          contentStyle={TOOLTIP_STYLE}
          formatter={(v: number, name: string) => [`${v.toFixed(3)} ${unit === 'w_per_kg' ? 'W/kg' : 'W/m³'}`, name]}
          labelFormatter={(b: number) => `B = ${b.toFixed(3)} T`}
        />
        <Legend
          content={() => (
            <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: '6px 12px', paddingTop: 6 }}>
              {freqs.map((freq, i) => (
                <span key={freq} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 10, color: '#94a3b8' }}>
                  <span style={{ display: 'inline-block', width: 16, height: 2, borderRadius: 1, backgroundColor: FREQ_COLORS[i % FREQ_COLORS.length] }} />
                  {freq}
                </span>
              ))}
            </div>
          )}
        />
        {freqs.map((freq, i) => (
          <Line
            key={freq}
            type="monotone"
            dataKey={freq}
            stroke={FREQ_COLORS[i % FREQ_COLORS.length]}
            dot={false}
            strokeWidth={1.5}
            name={freq}
            connectNulls
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
};

// ─── Steel detail ─────────────────────────────────────────────────────────────
const SteelDetail: React.FC<{ name: string; data: SteelData }> = ({ name, data }) => (
  <Box>
    {/* Properties */}
    <Section title="Properties" accentColor="#64748b">
      <Row label="Form"             value={data.form}                            highlight />
      <Row label="Conductivity σ"   value={data.sigma > 0 ? `${(data.sigma / 1e6).toFixed(3)}` : '≈ 0 (insulating)'}  unit={data.sigma > 0 ? 'MS/m' : ''} />
      <Row label="Density"          value={String(data.density)}                 unit="kg/m³" />
      <Row label="Stacking factor"  value={String(data.stacking_factor)}         highlight />
    </Section>

    {/* Bertotti */}
    <Section title="Bertotti Core Loss Model" accentColor="#f59e0b">
      <Row label="k_h (hysteresis)"    value={data.core_loss_kh.toFixed(4)}      unit="W/(m³·Hz·T²)"    highlight />
      <Row label="k_c (classical eddy)" value={data.core_loss_kc.toFixed(6)}     unit="W/(m³·Hz²·T²)" />
      <Row label="k_e (excess)"         value={data.core_loss_ke.toFixed(6)}     unit="W/(m³·Hz^1.5·T^1.5)" />
      <Divider sx={{ my: 1, borderColor: '#1e293b' }} />
      <Row label="P @ 50 Hz, 1 T"  value={bertotti(data, 50,  1.0).toFixed(2)}   unit="W/kg" />
      <Row label="P @ 400 Hz, 1 T" value={bertotti(data, 400, 1.0).toFixed(2)}   unit="W/kg" highlight />
      <Row label="P @ 1 kHz, 1 T"  value={bertotti(data, 1000, 1.0).toFixed(2)}  unit="W/kg" />
    </Section>

    {/* BH curve */}
    {data.bh_curve?.length > 1 && (
      <Section title="B-H Magnetisation Curve" accentColor="#3b82f6">
        <BHChart
          points={data.bh_curve}
          xLabel="H"
          yLabel="B [T]"
          lineColor="#3b82f6"
        />
      </Section>
    )}

    {/* Core loss curves */}
    {data.core_loss_curves && Object.keys(data.core_loss_curves).length > 0 && (
      <Section title="Core Loss vs. Flux Density" accentColor="#10b981">
        <CoreLossChart
          curves={data.core_loss_curves}
          unit={data.core_loss_curve_unit ?? 'w_per_kg'}
        />
      </Section>
    )}
  </Box>
);

// ─── Magnet detail ────────────────────────────────────────────────────────────
const MagnetDetail: React.FC<{ name: string; data: MagnetData }> = ({ name, data }) => (
  <Box>
    <Section title="Magnetic Properties" accentColor="#ef4444">
      <Row label="Remanence Br"          value={data.Br.toFixed(3)}                 unit="T"      highlight />
      <Row label="Coercivity Hc"         value={`${(data.Hc / 1000).toFixed(1)}`}   unit="kA/m"   highlight />
      <Row label="Recoil permeability"   value={data.mu_rec.toFixed(3)}             unit="μ_rec" />
      <Row label="Energy product BHmax"  value={data.energy_product_kj_m3.toFixed(1)} unit="kJ/m³" highlight />
      <Row label="Conductivity σ"        value={data.sigma > 0 ? `${(data.sigma / 1e6).toFixed(3)}` : '≈ 0'} unit={data.sigma > 0 ? 'MS/m' : ''} />
      <Row label="Density"               value={String(data.density)}               unit="kg/m³" />
    </Section>

    {/* Demagnetisation BH */}
    {data.bh_curve?.length > 1 && (
      <Section title="Demagnetisation Curve" accentColor="#ef4444">
        <BHChart
          points={data.bh_curve}
          xLabel="H"
          yLabel="B [T]"
          lineColor="#ef4444"
          refLines={[
            { y: data.Br,  label: `Br=${data.Br.toFixed(2)}T` },
            { x: -data.Hc, label: `Hc` },
          ]}
        />
      </Section>
    )}
  </Box>
);

// ─── Conductor detail ─────────────────────────────────────────────────────────
const ConductorDetail: React.FC<{ name: string; data: ConductorData }> = ({ name, data }) => (
  <Box>
    <Section title="Electrical Properties" accentColor="#f59e0b">
      <Row label="Conductivity σ"    value={`${(data.sigma / 1e6).toFixed(3)}`}        unit="MS/m"  highlight />
      <Row label="Resistivity ρ"     value={`${(data.resistivity * 1e8).toFixed(3)}`}  unit="×10⁻⁸ Ω·m" />
      <Row label="Density"           value={String(data.density)}                       unit="kg/m³" />
    </Section>
    <Section title="Thermal Properties" accentColor="#06b6d4">
      {data.thermal_conductivity != null && (
        <Row label="Thermal conductivity" value={String(data.thermal_conductivity)} unit="W/(m·K)" />
      )}
      {data.specific_heat != null && (
        <Row label="Specific heat"        value={String(data.specific_heat)}        unit="J/(kg·K)" />
      )}
      {data.thermal_alpha != null && (
        <Row label="Temp. coeff. α"       value={data.thermal_alpha.toFixed(5)}     unit="1/K" />
      )}
      {data.thermal_conductivity == null && data.specific_heat == null && data.thermal_alpha == null && (
        <Typography sx={{ fontSize: '0.7rem', color: '#475569', px: 1, py: 0.5 }}>No thermal data</Typography>
      )}
    </Section>
    {(data.wire_width_mm != null || data.wire_height_mm != null) && (
      <Section title="Wire Geometry" accentColor="#a78bfa">
        {data.wire_width_mm  != null && <Row label="Wire width"  value={String(data.wire_width_mm)}  unit="mm" />}
        {data.wire_height_mm != null && <Row label="Wire height" value={String(data.wire_height_mm)} unit="mm" />}
      </Section>
    )}
  </Box>
);

// ─── Helpers ─────────────────────────────────────────────────────────────────

function bertotti(d: SteelData, f: number, B: number): number {
  const wm3 = d.core_loss_kh * f * B ** 2
    + d.core_loss_kc * f ** 2 * B ** 2
    + d.core_loss_ke * f ** 1.5 * B ** 1.5;
  return wm3 / (d.density || 7650);
}

// ─── Category accent ─────────────────────────────────────────────────────────
const CAT_COLOR: Record<string, string> = {
  steel: '#64748b', magnet: '#ef4444', conductor: '#f59e0b',
};
const CAT_LABEL: Record<string, string> = {
  steel: 'Electrical Steel', magnet: 'Permanent Magnet', conductor: 'Conductor',
};

// ─── Root component ───────────────────────────────────────────────────────────

interface Props {
  library: MaterialsLibrary | null;
  selected: SelectedMaterial | null;
}

const MaterialDetailView: React.FC<Props> = ({ library, selected }) => {
  if (!library || !selected) {
    return (
      <Box sx={{
        height: '100%', display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'center', color: '#334155', gap: 1,
      }}>
        <Typography sx={{ fontSize: '0.9rem' }}>Select a material from the tree</Typography>
        <Typography sx={{ fontSize: '0.75rem' }}>B-H curves and loss data will appear here</Typography>
      </Box>
    );
  }

  const { category, name } = selected;
  const data = (library[category] as any)[name];
  const color = CAT_COLOR[category];

  return (
    <Box sx={{ height: '100%', overflowY: 'auto', p: 2 }}>
      {/* Title */}
      <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1.5, mb: 2 }}>
        <Box sx={{ width: 4, height: '100%', minHeight: 40, bgcolor: color, borderRadius: 1, flexShrink: 0 }} />
        <Box sx={{ flex: 1 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
            <Typography sx={{ fontSize: '1.05rem', fontWeight: 700, color: '#e2e8f0' }}>
              {name.replace(/_/g, ' ')}
            </Typography>
            <Chip
              label={CAT_LABEL[category]}
              size="small"
              sx={{ height: 18, fontSize: '0.6rem', bgcolor: `${color}20`, color }}
            />
          </Box>
          {data.description && (
            <Typography sx={{ fontSize: '0.72rem', color: '#64748b', mt: 0.25 }}>
              {data.description}
            </Typography>
          )}
        </Box>
      </Box>

      {/* Detail by type */}
      {category === 'steel'     && <SteelDetail     name={name} data={data as SteelData}     />}
      {category === 'magnet'    && <MagnetDetail    name={name} data={data as MagnetData}    />}
      {category === 'conductor' && <ConductorDetail name={name} data={data as ConductorData} />}
    </Box>
  );
};

export default MaterialDetailView;
