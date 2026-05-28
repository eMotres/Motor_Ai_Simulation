/**
 * Real motor cross-section drawn from live geometry parameters.
 * Clicking a region directly assigns the currently selected material.
 */
import React, { useMemo, useState } from 'react';
import { Box, Typography, Chip, Tooltip, Divider } from '@mui/material';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import AddCircleOutlineIcon  from '@mui/icons-material/AddCircleOutline';
import type { MaterialsLibrary, SelectedMaterial, MaterialCategory } from './useMaterialsLibrary';
import type { MotorAssignments } from './useMotorAssignments';
import { useMotorStore } from '../../stores/motorStore';

// ─── Part config ─────────────────────────────────────────────────────────────

interface PartCfg {
  key: keyof MotorAssignments;
  label: string;
  allowedCategory: MaterialCategory | null;
  accentColor: string;
  fallbackColor: string;
}

const PARTS: PartCfg[] = [
  { key: 'stator_core', label: 'Stator Core',  allowedCategory: 'steel',     accentColor: '#3b82f6', fallbackColor: '#374151' },
  { key: 'rotor_core',  label: 'Rotor Core',   allowedCategory: 'steel',     accentColor: '#2563eb', fallbackColor: '#1f2937' },
  { key: 'magnet',      label: 'Magnets',       allowedCategory: 'magnet',    accentColor: '#ef4444', fallbackColor: '#7f1d1d' },
  { key: 'slot',        label: 'Windings',      allowedCategory: 'conductor', accentColor: '#f59e0b', fallbackColor: '#78350f' },
  { key: 'shaft',       label: 'Shaft',         allowedCategory: null,        accentColor: '#64748b', fallbackColor: '#1e293b' },
];

function partColor(key: string, assignments: MotorAssignments, library: MaterialsLibrary | null, fallback: string, accent: string): string {
  const part = PARTS.find(p => p.key === key);
  if (!part || !part.allowedCategory || !library) return fallback;
  const mat = assignments[part.key as keyof MotorAssignments];
  return mat && mat in library[part.allowedCategory] ? accent : fallback;
}

// ─── SVG helpers ─────────────────────────────────────────────────────────────

function arcPath(cx: number, cy: number, r1: number, r2: number, startDeg: number, endDeg: number): string {
  const r  = (d: number) => (d - 90) * Math.PI / 180;
  const x1 = cx + r2 * Math.cos(r(startDeg)), y1 = cy + r2 * Math.sin(r(startDeg));
  const x2 = cx + r2 * Math.cos(r(endDeg)),   y2 = cy + r2 * Math.sin(r(endDeg));
  const x3 = cx + r1 * Math.cos(r(endDeg)),   y3 = cy + r1 * Math.sin(r(endDeg));
  const x4 = cx + r1 * Math.cos(r(startDeg)), y4 = cy + r1 * Math.sin(r(startDeg));
  const lg = (endDeg - startDeg) > 180 ? 1 : 0;
  return `M${x1} ${y1} A${r2} ${r2} 0 ${lg} 1 ${x2} ${y2} L${x3} ${y3} A${r1} ${r1} 0 ${lg} 0 ${x4} ${y4}Z`;
}

// ─── Cross-section SVG ───────────────────────────────────────────────────────

interface SvgProps {
  assignments: MotorAssignments;
  library: MaterialsLibrary | null;
  activePart: string | null;
  onPartClick: (partKey: string) => void;
}

const CrossSectionSvg: React.FC<SvgProps> = ({ assignments, library, activePart, onPartClick }) => {
  const geometry = useMotorStore(s => s.geometry);

  // Real geometry parameters (with fallbacks)
  const g = useMemo(() => {
    const n = (k: string, fb: number) => Number(geometry[k] ?? fb);
    const r_so = n('stator_outer_radius', 75);
    const r_si = n('stator_inner_radius', 57.2);
    const r_ro = n('rotor_outer_radius', 56.7);
    const r_ri = n('rotor_inner_radius', 41.7);
    const nSlots = Math.round(n('num_slots', 24));
    const nPoles = Math.round(n('num_poles', 28));
    const slotW  = n('slot_width', 4.5);
    const slotH  = n('slot_height', 14.0);
    const rouseH = n('rotor_house_height', 1.2);
    const magH   = n('magnet_height', 13.8);
    const mfDown = n('magnet_fill_down', 0.90);
    const mfUp   = n('magnet_fill_up',   0.46);

    return { r_so, r_si, r_ro, r_ri, nSlots, nPoles, slotW, slotH, rouseH, magH, mfDown, mfUp };
  }, [geometry]);

  const S = 400;       // SVG canvas px
  const cx = S / 2, cy = S / 2;
  const scale = (cx - 8) / g.r_so;

  // Scaled radii
  const RS = (r: number) => r * scale;
  const rSoS = RS(g.r_so);               // stator outer
  const rSiS = RS(g.r_si);               // stator bore
  const rSlotTopS = RS(g.r_si + g.slotH); // slot top / back-iron inner
  const rRoS = RS(g.r_ro);               // rotor outer
  const rRiS = RS(g.r_ri);               // rotor inner (shaft bore)
  const rHouseS = RS(g.r_ro - g.rouseH); // top of magnet region
  const rMagBotS = RS(g.r_ro - g.rouseH - g.magH); // bottom of magnet

  // Angular dimensions
  const slotPitch = 360 / g.nSlots;
  const slotHalfDeg = (Math.asin(Math.min(g.slotW / (2 * g.r_si), 0.999)) * 180 / Math.PI);
  const polePitch = 360 / g.nPoles;
  const magHalfDegOuter = polePitch * g.mfUp   / 2;
  const magHalfDegInner = polePitch * g.mfDown / 2;

  // Colors
  const statorColor   = partColor('stator_core', assignments, library, '#374151', '#1d4ed8');
  const rotorColor    = partColor('rotor_core',  assignments, library, '#1f2937', '#1e40af');
  const magnetColor   = partColor('magnet',      assignments, library, '#7f1d1d', '#dc2626');
  const windingColor  = partColor('slot',        assignments, library, '#78350f', '#d97706');
  const shaftColor    = '#1e293b';

  const activeOpacity = (key: string) => activePart === key ? 1 : 0.82;
  const activeStroke  = (key: string) => activePart === key ? 2.5 : 0;
  const glowId = (key: string) => `glow-${key}`;

  const slots = useMemo(() => Array.from({ length: g.nSlots }, (_, i) => ({
    center: i * slotPitch,
  })), [g.nSlots, slotPitch]);

  const poles = useMemo(() => Array.from({ length: g.nPoles }, (_, i) => ({
    center: i * polePitch,
    isNorth: i % 2 === 0,
  })), [g.nPoles, polePitch]);

  return (
    <svg viewBox={`0 0 ${S} ${S}`} style={{ width: '100%', height: '100%', display: 'block' }}>
      <defs>
        {['stator_core','rotor_core','magnet','slot'].map(k => {
          const p = PARTS.find(p => p.key === k)!;
          return (
            <filter key={k} id={glowId(k)} x="-40%" y="-40%" width="180%" height="180%">
              <feGaussianBlur in="SourceGraphic" stdDeviation="4" result="blur"/>
              <feFlood floodColor={p.accentColor} floodOpacity="0.55" result="c"/>
              <feComposite in="c" in2="blur" operator="in" result="sh"/>
              <feMerge><feMergeNode in="sh"/><feMergeNode in="SourceGraphic"/></feMerge>
            </filter>
          );
        })}
        <radialGradient id="shaftGrad" cx="50%" cy="50%" r="50%">
          <stop offset="0%"   stopColor="#334155"/>
          <stop offset="100%" stopColor="#0f172a"/>
        </radialGradient>
      </defs>

      {/* ── Outer background ── */}
      <circle cx={cx} cy={cy} r={rSoS + 3} fill="#0f172a"/>

      {/* ── Stator back-iron (full ring from slot-top to outer) ── */}
      <path
        d={arcPath(cx, cy, rSlotTopS, rSoS, 0, 360)}
        fill={statorColor}
        opacity={activeOpacity('stator_core')}
        filter={activePart === 'stator_core' ? `url(#${glowId('stator_core')})` : undefined}
        stroke={activePart === 'stator_core' ? '#93c5fd' : 'none'}
        strokeWidth={activeStroke('stator_core')}
        onClick={() => onPartClick('stator_core')}
        style={{ cursor: 'pointer' }}
      />

      {/* ── Stator teeth (full ring at bore area, overdrawn by slots) ── */}
      <path
        d={arcPath(cx, cy, rSiS, rSlotTopS, 0, 360)}
        fill={statorColor}
        opacity={activeOpacity('stator_core') * 0.9}
        onClick={() => onPartClick('stator_core')}
        style={{ cursor: 'pointer' }}
      />

      {/* ── Slot windings ── */}
      {slots.map((s, i) => (
        <path
          key={i}
          d={arcPath(cx, cy, rSiS + RS(0.5), rSlotTopS - RS(0.3),
            s.center - slotHalfDeg, s.center + slotHalfDeg)}
          fill={windingColor}
          opacity={activeOpacity('slot')}
          filter={activePart === 'slot' ? `url(#${glowId('slot')})` : undefined}
          stroke={activePart === 'slot' ? '#fcd34d' : 'none'}
          strokeWidth={activeStroke('slot') * 0.6}
          onClick={() => onPartClick('slot')}
          style={{ cursor: 'pointer' }}
        />
      ))}

      {/* ── Air gap ── */}
      <circle cx={cx} cy={cy} r={rRoS + RS(0.3)} fill="#0f172a"/>

      {/* ── Rotor body ── */}
      <path
        d={arcPath(cx, cy, rRiS, rRoS, 0, 360)}
        fill={rotorColor}
        opacity={activeOpacity('rotor_core')}
        filter={activePart === 'rotor_core' ? `url(#${glowId('rotor_core')})` : undefined}
        stroke={activePart === 'rotor_core' ? '#93c5fd' : 'none'}
        strokeWidth={activeStroke('rotor_core')}
        onClick={() => onPartClick('rotor_core')}
        style={{ cursor: 'pointer' }}
      />

      {/* ── Magnets (V-shape approximated as tapered arc per pole) ── */}
      {poles.map((p, i) => {
        // Trapezoidal: wider at inner radius, narrower at outer (matches mfDown/mfUp)
        const outerStart = p.center - magHalfDegOuter;
        const outerEnd   = p.center + magHalfDegOuter;
        const innerStart = p.center - magHalfDegInner;
        const innerEnd   = p.center + magHalfDegInner;
        const r1 = Math.max(rMagBotS, rRiS + 2);
        const r2 = Math.min(rHouseS,  rRoS - 1);

        // Use outer angles for both arcs, but scale slightly
        const avgHalfDeg = (magHalfDegOuter + magHalfDegInner) / 2;
        const d = arcPath(cx, cy, r1, r2, p.center - avgHalfDeg, p.center + avgHalfDeg);
        const col = p.isNorth
          ? magnetColor
          : `${magnetColor}cc`;

        return (
          <path
            key={i}
            d={d}
            fill={col}
            opacity={activeOpacity('magnet')}
            filter={activePart === 'magnet' ? `url(#${glowId('magnet')})` : undefined}
            stroke={activePart === 'magnet' ? '#fca5a5' : p.isNorth ? '#991b1b55' : 'none'}
            strokeWidth={activePart === 'magnet' ? 1 : 0.5}
            onClick={() => onPartClick('magnet')}
            style={{ cursor: 'pointer' }}
          />
        );
      })}

      {/* ── Shaft ── */}
      <circle
        cx={cx} cy={cy} r={rRiS}
        fill="url(#shaftGrad)"
        stroke={activePart === 'shaft' ? '#94a3b8' : '#1e293b'}
        strokeWidth={activePart === 'shaft' ? 2 : 1}
        onClick={() => onPartClick('shaft')}
        style={{ cursor: 'pointer' }}
      />
      <circle cx={cx} cy={cy} r={3} fill="#475569"/>

      {/* ── Active-part label ── */}
      {activePart && (() => {
        const p = PARTS.find(pp => pp.key === activePart);
        return p ? (
          <text x={cx} y={S - 12} textAnchor="middle" fontSize={11} fill="#94a3b8">
            {p.label}
          </text>
        ) : null;
      })()}
    </svg>
  );
};

// ─── Part assignment strip ────────────────────────────────────────────────────

interface StripProps {
  assignments: MotorAssignments;
  library: MaterialsLibrary | null;
  selected: SelectedMaterial | null;
  activePart: string | null;
  saving: boolean;
  onAssign: (part: string, mat: string) => void;
  onPartClick: (part: string) => void;
}

const AssignmentStrip: React.FC<StripProps> = ({
  assignments, library, selected, activePart, saving, onAssign, onPartClick,
}) => (
  <Box sx={{
    display: 'flex',
    flexWrap: 'wrap',
    gap: 0.75,
    px: 2,
    py: 1.25,
    bgcolor: '#080f1a',
    borderTop: '1px solid #1e293b',
  }}>
    {PARTS.map(part => {
      const current = assignments[part.key] ?? '—';
      const inLib = part.allowedCategory && library ? current in library[part.allowedCategory] : false;
      const canAssign = selected && part.allowedCategory === selected.category;
      const isActive = activePart === part.key;
      const alreadyAssigned = canAssign && selected?.name === current;

      return (
        <Box
          key={part.key}
          onClick={() => onPartClick(part.key)}
          sx={{
            flex: '1 1 auto',
            minWidth: 120,
            display: 'flex',
            flexDirection: 'column',
            gap: 0.5,
            p: 1,
            borderRadius: 1.5,
            border: `1px solid ${isActive ? part.accentColor + '80' : '#1e293b'}`,
            bgcolor: isActive ? `${part.accentColor}12` : 'transparent',
            cursor: 'pointer',
            transition: 'all 0.15s',
            '&:hover': { bgcolor: `${part.accentColor}18`, borderColor: `${part.accentColor}60` },
          }}
        >
          {/* Part label */}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
            <Box sx={{
              width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
              bgcolor: inLib ? part.accentColor : part.fallbackColor,
              boxShadow: inLib ? `0 0 5px ${part.accentColor}80` : 'none',
            }}/>
            <Typography sx={{ fontSize: 10, fontWeight: 600, color: '#94a3b8', letterSpacing: 0.5 }}>
              {part.label.toUpperCase()}
            </Typography>
          </Box>

          {/* Current material */}
          <Tooltip title={inLib ? 'Library material' : 'Legacy/custom'} placement="top">
            <Chip
              label={current.replace(/_/g, ' ')}
              size="small"
              sx={{
                fontSize: 9, height: 18,
                bgcolor: inLib ? `${part.accentColor}20` : '#1e293b',
                color: inLib ? part.accentColor : '#475569',
                border: `1px solid ${inLib ? `${part.accentColor}40` : '#334155'}`,
                '& .MuiChip-label': { px: 0.75 },
              }}
            />
          </Tooltip>

          {/* Assign button */}
          {part.allowedCategory && (
            <Tooltip
              title={
                !selected      ? 'Pick a material first'
                : !canAssign   ? `Needs ${part.allowedCategory}`
                : alreadyAssigned ? 'Already assigned'
                : `Assign ${selected.name}`
              }
              placement="top"
            >
              <Box
                component="button"
                disabled={!canAssign || alreadyAssigned || saving}
                onClick={e => { e.stopPropagation(); canAssign && !alreadyAssigned && onAssign(part.key, selected!.name); }}
                sx={{
                  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 0.4,
                  p: '2px 6px', borderRadius: 1, border: 'none', cursor: 'pointer',
                  fontSize: 9, fontWeight: 700, transition: 'all 0.15s',
                  bgcolor: alreadyAssigned ? '#14532d' : canAssign ? `${part.accentColor}30` : 'transparent',
                  color: alreadyAssigned ? '#4ade80' : canAssign ? part.accentColor : '#1e293b',
                  '&:hover:not(:disabled)': { bgcolor: canAssign ? `${part.accentColor}50` : 'transparent' },
                  '&:disabled': { cursor: 'not-allowed' },
                }}
              >
                {alreadyAssigned
                  ? <><CheckCircleOutlineIcon sx={{ fontSize: 10 }}/>Active</>
                  : <><AddCircleOutlineIcon  sx={{ fontSize: 10 }}/>Assign</>
                }
              </Box>
            </Tooltip>
          )}
        </Box>
      );
    })}
  </Box>
);

// ─── Main export ──────────────────────────────────────────────────────────────

interface Props {
  library: MaterialsLibrary | null;
  selected: SelectedMaterial | null;
  assignments: MotorAssignments | null;
  saving: boolean;
  onAssign: (part: string, mat: string) => void;
}

const MotorCrossSection: React.FC<Props> = ({ library, selected, assignments, saving, onAssign }) => {
  const [activePart, setActivePart] = useState<string | null>(null);
  const geometry = useMotorStore(s => s.geometry);
  const hasGeometry = Object.keys(geometry).length > 0;

  const handlePartClick = (partKey: string) => {
    setActivePart(prev => prev === partKey ? null : partKey);
    // If material selected and category matches — assign immediately
    if (selected && assignments) {
      const part = PARTS.find(p => p.key === partKey);
      if (part?.allowedCategory === selected.category && selected.name !== assignments[part.key as keyof MotorAssignments]) {
        onAssign(partKey, selected.name);
      }
    }
  };

  if (!assignments || !hasGeometry) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#475569' }}>
        <Typography>Loading geometry…</Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>

      {/* Header */}
      <Box sx={{ px: 2, pt: 1.5, pb: 0.75, flexShrink: 0 }}>
        <Typography variant="overline" sx={{ color: '#475569', letterSpacing: 2, fontSize: 10 }}>
          Motor Cross-Section
        </Typography>
        <Typography variant="body2" sx={{ color: '#64748b', fontSize: 11 }}>
          {selected
            ? <>Click a region to assign <Box component="span" sx={{ color: '#93c5fd', fontWeight: 600 }}>{selected.name}</Box></>
            : 'Select a material from the tree, then click a region to assign'}
        </Typography>
      </Box>

      {/* SVG — takes all remaining space */}
      <Box sx={{ flex: 1, overflow: 'hidden', p: 1 }}>
        <CrossSectionSvg
          assignments={assignments}
          library={library}
          activePart={activePart}
          onPartClick={handlePartClick}
        />
      </Box>

      {/* Geometry summary */}
      <Box sx={{ px: 2, py: 0.5, flexShrink: 0 }}>
        <Typography sx={{ fontSize: 10, color: '#334155', letterSpacing: 0.5 }}>
          Ø{(Number(geometry.stator_outer_radius ?? 0) * 2).toFixed(0)} mm stator &nbsp;·&nbsp;
          {Math.round(Number(geometry.num_slots ?? 0))} slots &nbsp;·&nbsp;
          {Math.round(Number(geometry.num_poles ?? 0))} poles
        </Typography>
      </Box>

      {/* Assignment strip */}
      <AssignmentStrip
        assignments={assignments}
        library={library}
        selected={selected}
        activePart={activePart}
        saving={saving}
        onAssign={onAssign}
        onPartClick={setActivePart}
      />
    </Box>
  );
};

export default MotorCrossSection;
