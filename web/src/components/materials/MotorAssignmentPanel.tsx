import React, { useMemo } from 'react';
import { Box, Typography, Divider, Chip, CircularProgress, Tooltip } from '@mui/material';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import AddCircleOutlineIcon  from '@mui/icons-material/AddCircleOutline';
import type { MaterialsLibrary, SelectedMaterial, MaterialCategory } from './useMaterialsLibrary';
import type { MotorAssignments } from './useMotorAssignments';

// ─── Motor part definitions ───────────────────────────────────────────────────

interface MotorPart {
  key: keyof MotorAssignments;
  label: string;
  allowedCategory: MaterialCategory | null;   // null = not assignable from library
  /** Fill color in the diagram when no library material is assigned */
  fallbackFill: string;
  /** Accent color (matches category) */
  accentFill: string;
}

const PARTS: MotorPart[] = [
  { key: 'stator_core', label: 'Stator Core',  allowedCategory: 'steel',     fallbackFill: '#374151', accentFill: '#60a5fa' },
  { key: 'rotor_core',  label: 'Rotor Core',   allowedCategory: 'steel',     fallbackFill: 'var(--panel-2)', accentFill: '#60a5fa' },
  { key: 'magnet',      label: 'Magnets',       allowedCategory: 'magnet',    fallbackFill: '#7f1d1d', accentFill: '#f87171' },
  { key: 'slot',        label: 'Windings',      allowedCategory: 'conductor', fallbackFill: '#78350f', accentFill: '#fbbf24' },
  { key: 'slot_insulation', label: 'Slot Liner',  allowedCategory: 'insulator', fallbackFill: '#3f3f46', accentFill: '#a78bfa' },
  { key: 'wire_insulation', label: 'Wire Enamel', allowedCategory: 'insulator', fallbackFill: '#3f3f46', accentFill: '#a78bfa' },
  { key: 'shaft',       label: 'Shaft',         allowedCategory: null,        fallbackFill: 'var(--panel)', accentFill: 'var(--text-3)' },
];

// ─── SVG helper ──────────────────────────────────────────────────────────────

function arcPath(
  cx: number, cy: number,
  r1: number, r2: number,
  startDeg: number, endDeg: number,
): string {
  const toRad = (d: number) => (d - 90) * Math.PI / 180;
  const x1 = cx + r2 * Math.cos(toRad(startDeg)), y1 = cy + r2 * Math.sin(toRad(startDeg));
  const x2 = cx + r2 * Math.cos(toRad(endDeg)),   y2 = cy + r2 * Math.sin(toRad(endDeg));
  const x3 = cx + r1 * Math.cos(toRad(endDeg)),   y3 = cy + r1 * Math.sin(toRad(endDeg));
  const x4 = cx + r1 * Math.cos(toRad(startDeg)), y4 = cy + r1 * Math.sin(toRad(startDeg));
  const lg = endDeg - startDeg > 180 ? 1 : 0;
  return `M ${x1} ${y1} A ${r2} ${r2} 0 ${lg} 1 ${x2} ${y2} L ${x3} ${y3} A ${r1} ${r1} 0 ${lg} 0 ${x4} ${y4} Z`;
}

// ─── Motor Diagram SVG ───────────────────────────────────────────────────────

interface DiagramProps {
  assignments: MotorAssignments;
  library: MaterialsLibrary | null;
  highlightPart: string | null;  // which part is hovered/active
}

function isLibraryMaterial(name: string, category: MaterialCategory, library: MaterialsLibrary | null): boolean {
  if (!library) return false;
  return name in library[category];
}

const MotorDiagram: React.FC<DiagramProps> = ({ assignments, library, highlightPart }) => {
  const cx = 120, cy = 120;

  // Radii
  const rStOuter = 112, rStInner = 76;   // stator ring
  const rAirOuter = 72,  rAirInner = 68;   // air gap
  const rRotOuter = 68,  rRotInner = 22;   // rotor
  const rMagOuter = 68,  rMagInner = 60;   // magnets
  const rShaft    = 22;                     // shaft

  // Colors
  const statorFill = isLibraryMaterial(assignments.stator_core, 'steel', library)
    ? '#2563eb' : '#374151';
  const rotorFill  = isLibraryMaterial(assignments.rotor_core, 'steel', library)
    ? '#1d4ed8' : 'var(--panel-2)';
  const magnetFill = isLibraryMaterial(assignments.magnet, 'magnet', library)
    ? '#dc2626' : '#7f1d1d';
  const windingFill = isLibraryMaterial(assignments.slot, 'conductor', library)
    ? '#d97706' : '#78350f';

  const hStator  = highlightPart === 'stator_core';
  const hRotor   = highlightPart === 'rotor_core';
  const hMagnet  = highlightPart === 'magnet';
  const hWinding = highlightPart === 'slot';
  const hShaft   = highlightPart === 'shaft';

  // 12 slots, 30° pitch each (10° slot + 20° tooth)
  const nSlots = 12, slotAngle = 10, pitchAngle = 30;
  // 4 magnets (4-pole), 80° each with 10° gaps
  const nMagnets = 4, magnetSpan = 80, magnetGap = 10;

  const slots = useMemo(() =>
    Array.from({ length: nSlots }, (_, i) => {
      const start = i * pitchAngle + 5;
      return { start, end: start + slotAngle };
    }), []);

  const magnets = useMemo(() =>
    Array.from({ length: nMagnets }, (_, i) => {
      const start = i * 90 + magnetGap / 2;
      return { start, end: start + magnetSpan };
    }), []);

  const glowFilter = (id: string, color: string) => (
    <filter id={id} x="-30%" y="-30%" width="160%" height="160%">
      <feGaussianBlur in="SourceGraphic" stdDeviation="3" result="blur" />
      <feFlood floodColor={color} floodOpacity="0.6" result="color" />
      <feComposite in="color" in2="blur" operator="in" result="shadow" />
      <feMerge><feMergeNode in="shadow" /><feMergeNode in="SourceGraphic" /></feMerge>
    </filter>
  );

  return (
    <svg viewBox="0 0 240 240" style={{ width: '100%', maxWidth: 240, display: 'block', margin: '0 auto' }}>
      <defs>
        {glowFilter('glow-blue',   '#60a5fa')}
        {glowFilter('glow-red',    '#f87171')}
        {glowFilter('glow-amber',  '#fbbf24')}
        {glowFilter('glow-slate',  'var(--text-2)')}
      </defs>

      {/* Outer background circle */}
      <circle cx={cx} cy={cy} r={rStOuter + 2} fill="var(--app-bg)" />

      {/* Stator body (full ring — slots will cut in) */}
      <path
        d={arcPath(cx, cy, rStInner, rStOuter, 0, 360)}
        fill={statorFill}
        opacity={hStator ? 1 : 0.75}
        filter={hStator ? 'url(#glow-blue)' : undefined}
      />

      {/* Slot windings */}
      {slots.map((s, i) => (
        <path
          key={i}
          d={arcPath(cx, cy, rStInner + 4, rStOuter - 6, s.start, s.end)}
          fill={windingFill}
          opacity={hWinding ? 1 : 0.85}
          filter={hWinding ? 'url(#glow-amber)' : undefined}
        />
      ))}

      {/* Air gap */}
      <circle cx={cx} cy={cy} r={rAirOuter} fill="var(--app-bg)" />
      <circle cx={cx} cy={cy} r={rAirOuter} fill="none" stroke="var(--line)" strokeWidth={0.5} />

      {/* Rotor body */}
      <circle
        cx={cx} cy={cy} r={rRotOuter}
        fill={rotorFill}
        opacity={hRotor ? 1 : 0.8}
        filter={hRotor ? 'url(#glow-blue)' : undefined}
      />

      {/* Magnets on rotor surface */}
      {magnets.map((m, i) => (
        <path
          key={i}
          d={arcPath(cx, cy, rMagInner, rMagOuter, m.start, m.end)}
          fill={i % 2 === 0 ? magnetFill : `${magnetFill}cc`}
          opacity={hMagnet ? 1 : 0.9}
          filter={hMagnet ? 'url(#glow-red)' : undefined}
        />
      ))}

      {/* Shaft */}
      <circle
        cx={cx} cy={cy} r={rShaft}
        fill={hShaft ? 'var(--text-4)' : 'var(--panel)'}
        stroke="var(--line)" strokeWidth={1}
        filter={hShaft ? 'url(#glow-slate)' : undefined}
      />

      {/* Center dot */}
      <circle cx={cx} cy={cy} r={2} fill="var(--text-4)" />

      {/* Labels */}
      <text x={cx + rStOuter - 10} y={cy - 4} textAnchor="middle" fontSize={6} fill="var(--text-2)" transform={`rotate(-60,${cx},${cy})`}>Stator</text>
      <text x={cx} y={cy - rRotOuter + 14} textAnchor="middle" fontSize={6} fill="var(--text-2)">Rotor</text>
    </svg>
  );
};

// ─── Part row ─────────────────────────────────────────────────────────────────

interface PartRowProps {
  part: MotorPart;
  currentMaterial: string;
  selected: SelectedMaterial | null;
  library: MaterialsLibrary | null;
  saving: boolean;
  onAssign: (partKey: string, materialName: string) => void;
  onHover: (partKey: string | null) => void;
}

const PartRow: React.FC<PartRowProps> = ({
  part, currentMaterial, selected, library, saving, onAssign, onHover,
}) => {
  const isInLibrary = library && part.allowedCategory
    ? currentMaterial in library[part.allowedCategory]
    : false;

  // Can we assign the selected material to this part?
  const canAssign = selected !== null
    && part.allowedCategory !== null
    && selected.category === part.allowedCategory;

  const alreadyAssigned = canAssign && selected!.name === currentMaterial;

  const displayName = isInLibrary
    ? currentMaterial.replace(/_/g, ' ')
    : currentMaterial;

  return (
    <Box
      onMouseEnter={() => onHover(part.key)}
      onMouseLeave={() => onHover(null)}
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 1.5,
        px: 1.5,
        py: 1,
        borderRadius: 1,
        cursor: 'default',
        transition: 'background 0.15s',
        '&:hover': { bgcolor: 'var(--panel)' },
      }}
    >
      {/* Color dot */}
      <Box sx={{
        width: 10, height: 10, borderRadius: '50%', flexShrink: 0,
        bgcolor: isInLibrary ? part.accentFill : part.fallbackFill,
        boxShadow: isInLibrary ? `0 0 6px ${part.accentFill}80` : 'none',
      }} />

      {/* Part label */}
      <Typography variant="body2" sx={{ color: 'var(--text-1)', width: 90, flexShrink: 0, fontSize: 12 }}>
        {part.label}
      </Typography>

      {/* Current material */}
      <Tooltip title={isInLibrary ? 'From materials library' : 'Legacy / custom material'} placement="top">
        <Chip
          label={displayName}
          size="small"
          sx={{
            flex: 1,
            fontSize: 10,
            height: 20,
            bgcolor: isInLibrary ? `${part.accentFill}20` : 'var(--panel)',
            color: isInLibrary ? part.accentFill : 'var(--text-3)',
            border: `1px solid ${isInLibrary ? `${part.accentFill}40` : 'var(--line)'}`,
            '& .MuiChip-label': { px: 1 },
          }}
        />
      </Tooltip>

      {/* Assign button */}
      {part.allowedCategory && (
        <Tooltip
          title={
            !selected ? 'Select a material from the tree first'
            : !canAssign ? `Needs a ${part.allowedCategory} material`
            : alreadyAssigned ? 'Already assigned'
            : `Assign "${selected.name}" here`
          }
          placement="top"
        >
          <Box
            component="button"
            disabled={!canAssign || alreadyAssigned || saving}
            onClick={() => canAssign && !alreadyAssigned && onAssign(part.key, selected!.name)}
            sx={{
              display: 'flex', alignItems: 'center', gap: 0.5,
              px: 1, py: 0.25, borderRadius: 1, border: 'none', cursor: 'pointer',
              fontSize: 10, fontWeight: 600, transition: 'all 0.15s', flexShrink: 0,
              bgcolor: alreadyAssigned
                ? 'var(--ok-bg)'
                : canAssign ? `${part.accentFill}25` : 'transparent',
              color: alreadyAssigned
                ? '#4ade80'
                : canAssign ? part.accentFill : '#374151',
              '&:hover:not(:disabled)': {
                bgcolor: canAssign ? `${part.accentFill}40` : 'transparent',
              },
              '&:disabled': { cursor: 'not-allowed', opacity: 0.4 },
            }}
          >
            {alreadyAssigned
              ? <><CheckCircleOutlineIcon sx={{ fontSize: 12 }} />Active</>
              : <><AddCircleOutlineIcon  sx={{ fontSize: 12 }} />Assign</>
            }
          </Box>
        </Tooltip>
      )}
    </Box>
  );
};

// ─── Main Panel ───────────────────────────────────────────────────────────────

interface Props {
  library: MaterialsLibrary | null;
  selected: SelectedMaterial | null;
  assignments: MotorAssignments | null;
  loadingAssignments: boolean;
  saving: boolean;
  onAssign: (part: string, material: string) => void;
}

const MotorAssignmentPanel: React.FC<Props> = ({
  library, selected, assignments, loadingAssignments, saving, onAssign,
}) => {
  const [hovered, setHovered] = React.useState<string | null>(null);

  if (loadingAssignments || !assignments) {
    return (
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
        <CircularProgress size={24} />
      </Box>
    );
  }

  return (
    <Box sx={{ height: '100%', overflow: 'auto', p: 2, display: 'flex', flexDirection: 'column', gap: 2 }}>

      {/* Header */}
      <Box>
        <Typography variant="overline" sx={{ color: 'var(--text-4)', letterSpacing: 2, fontSize: 10 }}>
          Motor Assembly
        </Typography>
        <Typography variant="body2" sx={{ color: 'var(--text-3)', fontSize: 11, mt: 0.25 }}>
          {selected
            ? <>Select a part below to assign <Box component="span" sx={{ color: 'var(--text-2)', fontWeight: 600 }}>{selected.name}</Box></>
            : 'Select a material from the tree, then assign it to a motor part'}
        </Typography>
      </Box>

      {/* SVG diagram */}
      <Box sx={{
        bgcolor: 'var(--app-bg)',
        borderRadius: 2,
        p: 1.5,
        border: '1px solid var(--line-soft)',
      }}>
        <MotorDiagram
          assignments={assignments}
          library={library}
          highlightPart={hovered}
        />

        {/* Legend */}
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px', mt: 1, justifyContent: 'center' }}>
          {[
            { label: 'Steel',     color: '#2563eb' },
            { label: 'Magnets',   color: '#dc2626' },
            { label: 'Windings',  color: '#d97706' },
            { label: 'Air gap',   color: 'var(--line)' },
            { label: 'Shaft',     color: 'var(--text-4)' },
          ].map(({ label, color }) => (
            <Box key={label} sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
              <Box sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: color }} />
              <Typography sx={{ fontSize: 9, color: 'var(--text-4)' }}>{label}</Typography>
            </Box>
          ))}
        </Box>
      </Box>

      {/* Part assignments */}
      <Box>
        <Typography variant="overline" sx={{ color: 'var(--text-4)', letterSpacing: 2, fontSize: 10, px: 1.5 }}>
          Part Assignments
        </Typography>

        <Box sx={{ mt: 0.5 }}>
          {PARTS.map((part, i) => (
            <React.Fragment key={part.key}>
              {i > 0 && <Divider sx={{ borderColor: 'var(--panel)', mx: 1.5 }} />}
              <PartRow
                part={part}
                currentMaterial={assignments[part.key] ?? '—'}
                selected={selected}
                library={library}
                saving={saving}
                onAssign={onAssign}
                onHover={setHovered}
              />
            </React.Fragment>
          ))}
        </Box>
      </Box>

      {saving && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 1.5 }}>
          <CircularProgress size={12} />
          <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>Saving…</Typography>
        </Box>
      )}
    </Box>
  );
};

export default MotorAssignmentPanel;
