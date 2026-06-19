/**
 * GeometryProjections — two live SVG projections of the tuned motor for the
 * Configurator.  Pure client-side schematics (no FEM/backend): they redraw
 * instantly from the reference cross-section geometry + the current knobs.
 *
 *   • Cross-section (XY): stator slots filled with the winding drawn as N rows
 *     of (wire_height + spacing) inside the insulation — so the coil structure
 *     and slot fill respond live to the turns/wire knobs.  Rotor + magnets +
 *     shaft for context.  (motor_length is axial → does not change this view.)
 *   • Side view: the lamination stack as a rectangle whose length tracks the
 *     motor_length knob, against the fixed outer diameter.
 */
import React, { useMemo } from 'react';
import { Box, Typography } from '@mui/material';
import type { Knobs } from '../../lib/motorScaling';
import type { ReferenceMotor } from '../../lib/referencePassports';

const STEEL = '#3b4453', STEEL_DK = '#2a3142', COPPER = '#c27d33', COPPER_DK = '#8a5520',
  SHAFT = '#5b6675', MAG_N = '#ef4444', MAG_S = '#3b82f6', BG = '#060d17', STROKE = '#1e293b';
const LABEL = { fontSize: 11, color: '#64748b', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.03em' } as const;
const SUB = { fontSize: 10, color: '#475569', mb: 0.5 } as const;
const PANEL = { flex: '1 1 300px', minWidth: 270, bgcolor: '#0b1424', border: '1px solid #1e293b', borderRadius: 1, p: 1.5 } as const;

const CrossSection: React.FC<{ ref0: ReferenceMotor; knobs: Knobs }> = ({ ref0, knobs }) => {
  const g = ref0.geo, f = ref0.fit;
  const SZ = 300, c = SZ / 2, margin = 6;
  const s = (c - margin) / g.statorOR_mm;
  const P = (mm: number) => mm * s;

  const slotEls = useMemo(() => {
    const out: React.ReactNode[] = [];
    const slotWpx = P(f.slotWidth_mm), insPx = P(f.insulation_mm);
    const rowPitch = knobs.wireH_mm + f.wireSpacingY_mm;
    const outerR = g.statorIR_mm + f.slotHeight_mm;
    for (let i = 0; i < g.numSlots; i++) {
      const parts: React.ReactNode[] = [
        <rect key="bg" x={c - slotWpx / 2} y={c - P(outerR)} width={slotWpx} height={P(f.slotHeight_mm)} rx={1} fill={BG} stroke={STROKE} strokeWidth={0.4} />,
      ];
      for (let k = 0; k < knobs.N; k++) {
        const ri = g.statorIR_mm + f.insulation_mm + k * rowPitch;
        const ro = ri + knobs.wireH_mm;
        if (ro > outerR - f.insulation_mm + 1e-6) break;
        parts.push(
          <rect key={`w${k}`} x={c - slotWpx / 2 + insPx} y={c - P(ro)}
            width={slotWpx - 2 * insPx} height={Math.max(0.6, P(knobs.wireH_mm))}
            fill={COPPER} stroke={COPPER_DK} strokeWidth={0.3} />,
        );
      }
      out.push(<g key={i} transform={`rotate(${i * (360 / g.numSlots)} ${c} ${c})`}>{parts}</g>);
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [g, f, knobs.N, knobs.wireH_mm]);

  const magEls = useMemo(() => {
    const out: React.ReactNode[] = [];
    const innerR = Math.max(g.rotorIR_mm, g.rotorOR_mm - g.magnetHeight_mm);
    const wpx = P(g.rotorOR_mm) * 0.7 * (2 * Math.PI / g.numPoles);
    for (let j = 0; j < g.numPoles; j++) {
      out.push(
        <g key={j} transform={`rotate(${j * (360 / g.numPoles)} ${c} ${c})`}>
          <rect x={c - wpx / 2} y={c - P(g.rotorOR_mm)} width={wpx} height={P(g.rotorOR_mm - innerR)}
            fill={j % 2 ? MAG_S : MAG_N} opacity={0.85} />
        </g>,
      );
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [g]);

  return (
    <svg viewBox={`0 0 ${SZ} ${SZ}`} style={{ width: '100%', maxWidth: 320, height: 'auto' }}>
      <circle cx={c} cy={c} r={P(g.statorOR_mm)} fill={STEEL} stroke={STROKE} />
      <circle cx={c} cy={c} r={P(g.statorIR_mm)} fill={BG} />
      <circle cx={c} cy={c} r={P(g.rotorOR_mm)} fill={STEEL_DK} stroke={STROKE} strokeWidth={0.4} />
      {magEls}
      <circle cx={c} cy={c} r={P(g.rotorIR_mm)} fill={SHAFT} />
      {slotEls}
    </svg>
  );
};

const SideView: React.FC<{ ref0: ReferenceMotor; knobs: Knobs }> = ({ ref0, knobs }) => {
  const g = ref0.geo;
  const W = 320, H = 168;
  const OD = 2 * g.statorOR_mm;
  const s2 = 118 / OD;                 // outer diameter → 118 px
  const stackH = OD * s2;
  const stackW = knobs.L_mm * s2;
  const cx = W / 2, cy = H / 2 - 6;
  const x0 = cx - stackW / 2, y0 = cy - stackH / 2;
  const shaftR = g.rotorIR_mm * s2;
  const overhang = 24;

  const hatch: React.ReactNode[] = [];
  const n = Math.max(2, Math.round(stackW / 5));
  for (let i = 1; i < n; i++) {
    const x = x0 + (i / n) * stackW;
    hatch.push(<line key={i} x1={x} y1={y0} x2={x} y2={y0 + stackH} stroke={STEEL_DK} strokeWidth={0.5} />);
  }

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', maxWidth: 340, height: 'auto' }}>
      {/* shaft through the stack */}
      <rect x={x0 - overhang} y={cy - shaftR} width={stackW + 2 * overhang} height={2 * shaftR} rx={2} fill={SHAFT} />
      {/* lamination stack */}
      <rect x={x0} y={y0} width={stackW} height={stackH} rx={2} fill={STEEL} stroke={STROKE} />
      {hatch}
      {/* length dimension line */}
      <line x1={x0} y1={y0 + stackH + 11} x2={x0 + stackW} y2={y0 + stackH + 11} stroke="#94a3b8" strokeWidth={0.8} />
      <line x1={x0} y1={y0 + stackH + 7} x2={x0} y2={y0 + stackH + 15} stroke="#94a3b8" strokeWidth={0.8} />
      <line x1={x0 + stackW} y1={y0 + stackH + 7} x2={x0 + stackW} y2={y0 + stackH + 15} stroke="#94a3b8" strokeWidth={0.8} />
      <text x={cx} y={y0 + stackH + 26} fill="#cbd5e1" fontSize={12} textAnchor="middle" fontFamily="monospace">{knobs.L_mm.toFixed(0)} mm</text>
      {/* diameter tick */}
      <text x={x0 - overhang - 4} y={cy} fill="#64748b" fontSize={10} textAnchor="middle" dominantBaseline="middle"
        transform={`rotate(-90 ${x0 - overhang - 4} ${cy})`}>Ø{OD.toFixed(0)} mm</text>
    </svg>
  );
};

const GeometryProjections: React.FC<{ ref0: ReferenceMotor; knobs: Knobs }> = ({ ref0, knobs }) => (
  <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
    <Box sx={PANEL}>
      <Typography sx={LABEL}>Cross-section (XY) — winding</Typography>
      <Typography sx={SUB}>{knobs.N} turns/slot · {knobs.wireH_mm.toFixed(2)} mm wire · {ref0.geo.numSlots} slots / {ref0.geo.numPoles} poles</Typography>
      <Box sx={{ textAlign: 'center' }}><CrossSection ref0={ref0} knobs={knobs} /></Box>
    </Box>
    <Box sx={PANEL}>
      <Typography sx={LABEL}>Side view — stack length</Typography>
      <Typography sx={SUB}>L = {knobs.L_mm.toFixed(0)} mm · Ø{(2 * ref0.geo.statorOR_mm).toFixed(0)} mm (copper = winding, grey = shaft)</Typography>
      <Box sx={{ textAlign: 'center' }}><SideView ref0={ref0} knobs={knobs} /></Box>
    </Box>
  </Box>
);

export default GeometryProjections;
