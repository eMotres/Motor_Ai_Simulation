/**
 * Real motor geometry viewer + material assignment panel.
 * Uses the live MotorScene (Three.js) for the top section,
 * and AssignmentStrip for material assignment at the bottom.
 */
import React, { useState } from 'react';
import { Box, Typography, Chip, Tooltip } from '@mui/material';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import AddCircleOutlineIcon  from '@mui/icons-material/AddCircleOutline';
import type { MaterialsLibrary, SelectedMaterial, MaterialCategory } from './useMaterialsLibrary';
import type { MotorAssignments } from './useMotorAssignments';
import MotorScene from '../viewer3d/MotorScene';

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
  { key: 'shaft',       label: 'Shaft',         allowedCategory: 'steel',     accentColor: '#64748b', fallbackColor: '#1e293b' },
];

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
    flexShrink: 0,
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
            minWidth: 110,
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
                !selected         ? 'Pick a material first'
                : !canAssign      ? `Needs ${part.allowedCategory}`
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

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>

      {/* Header */}
      <Box sx={{ px: 2, pt: 1.25, pb: 0.5, flexShrink: 0, borderBottom: '1px solid #1e293b' }}>
        <Typography variant="overline" sx={{ color: '#475569', letterSpacing: 2, fontSize: 10 }}>
          Motor Geometry
        </Typography>
        {selected && (
          <Typography variant="body2" sx={{ color: '#64748b', fontSize: 11 }}>
            Select a part below to assign{' '}
            <Box component="span" sx={{ color: '#93c5fd', fontWeight: 600 }}>{selected.name}</Box>
          </Typography>
        )}
      </Box>

      {/* Real 3D motor scene — takes all remaining space */}
      <Box sx={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
        <MotorScene />
      </Box>

      {/* Assignment strip */}
      {assignments && (
        <AssignmentStrip
          assignments={assignments}
          library={library}
          selected={selected}
          activePart={activePart}
          saving={saving}
          onAssign={onAssign}
          onPartClick={setActivePart}
        />
      )}
    </Box>
  );
};

export default MotorCrossSection;
