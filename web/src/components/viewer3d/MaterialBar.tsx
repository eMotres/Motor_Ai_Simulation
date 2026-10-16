/**
 * Fusion360-style floating material bar.
 * Appears at the bottom of the 3D viewer when a part is selected.
 * Shows the current assigned material and lets the user reassign it.
 */
import React, { useState, useRef, useEffect } from 'react';
import { Box, Typography, IconButton, Tooltip, CircularProgress } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import ArrowDropDownIcon from '@mui/icons-material/ArrowDropDown';
import { useUIStore, type CompKey } from '../../stores/motorStore';
import { useMotorAssignments } from '../materials/useMotorAssignments';
import { useMaterialsLibrary } from '../materials/useMaterialsLibrary';
import MaterialPickerPopover from '../materials/MaterialPickerPopover';
import type { MaterialCategory } from '../materials/useMaterialsLibrary';
import type { MotorAssignments } from '../materials/useMotorAssignments';
import { PART_COLORS } from '../../lib/partColors';

// ─── Part config ──────────────────────────────────────────────────────────────

interface PartCfg {
  assignKey: keyof MotorAssignments;
  label: string;
  color: string;
}

const PART_CFG: Partial<Record<CompKey, PartCfg>> = {
  stator:   { assignKey: 'stator_core', label: 'Stator Core',           color: PART_COLORS.statorIron },
  rotor:    { assignKey: 'rotor_core',  label: 'Rotor Core',            color: PART_COLORS.rotorIron },
  magnets:  { assignKey: 'magnet',      label: 'Magnets',               color: PART_COLORS.magnetN },
  coils:    { assignKey: 'slot',        label: 'Windings',              color: PART_COLORS.copper },
  shaft:    { assignKey: 'shaft',       label: 'Shaft',                 color: PART_COLORS.shaft },
  in_band:  { assignKey: 'air_gap',     label: 'In Band (rotating)',    color: PART_COLORS.inBand },
  out_band: { assignKey: 'air_gap',     label: 'Out Band (static)',     color: PART_COLORS.outBand },
  slot_insulation: { assignKey: 'slot_insulation', label: 'Insulation',  color: PART_COLORS.slotLiner },
  wire_insulation: { assignKey: 'wire_insulation', label: 'Wire Enamel', color: PART_COLORS.enamel },
  // Only selectable when the machine has one (the mesh carries a `sleeve`
  // part exactly then), so no guard is needed here (user 2026-09-04: "make
  // everything standard, like for the other motor parts").
  sleeve:   { assignKey: 'sleeve',      label: 'Retaining Sleeve',      color: PART_COLORS.sleeve },
};

// All categories in display order
const CATEGORY_CFG: { key: MaterialCategory; label: string; color: string }[] = [
  { key: 'steel',     label: 'Lamination Steel', color: 'var(--text-3)' },
  { key: 'magnet',    label: 'Magnets',          color: '#ef4444' },
  { key: 'conductor', label: 'Metal',            color: '#f59e0b' },
  { key: 'insulator', label: 'Insulators',       color: '#3fae5a' },
  { key: 'coolant',   label: 'Coolants & Air',   color: '#38bdf8' },
];

// ─── Component ────────────────────────────────────────────────────────────────

const MaterialBar: React.FC = () => {
  const { selectedPart, setSelectedPart } = useUIStore();
  const { assignments, saving, assign } = useMotorAssignments();
  const { library } = useMaterialsLibrary();

  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null);
  const chipRef = useRef<HTMLDivElement | null>(null);

  // Escape to deselect
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setAnchorEl(null); setSelectedPart(null); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [setSelectedPart]);

  if (!selectedPart) return null;

  const cfg = PART_CFG[selectedPart];
  if (!cfg) return null;   // e.g. insulation parts — no material-reassign bar
  const current = assignments ? assignments[cfg.assignKey] : null;
  const open = Boolean(anchorEl);

  // Build full grouped material list for popover
  const groupedMats: { key: string; label: string; color: string; names: string[] }[] = library
    ? CATEGORY_CFG
        .map(cat => ({ ...cat, names: Object.keys(library[cat.key] ?? {}) }))
        .filter(g => g.names.length > 0)
    : [];

  return (
    <>
      {/* ── Floating bar ── */}
      <Box sx={{
        position: 'absolute',
        bottom: 16,
        left: '50%',
        transform: 'translateX(-50%)',
        zIndex: 1200,
        display: 'flex',
        alignItems: 'center',
        gap: 1.5,
        bgcolor: 'var(--overlay)',
        backdropFilter: 'blur(10px)',
        border: `1px solid ${cfg.color}55`,
        borderRadius: 2,
        px: 2,
        py: 1,
        boxShadow: `0 4px 24px rgba(0,0,0,0.6), 0 0 0 1px ${cfg.color}22`,
        minWidth: 320,
        maxWidth: 520,
      }}>

        {/* Color dot + part name */}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, flexShrink: 0 }}>
          <Box sx={{
            width: 10, height: 10, borderRadius: '50%',
            bgcolor: cfg.color,
            boxShadow: `0 0 6px ${cfg.color}`,
          }} />
          <Typography sx={{ fontSize: 12, fontWeight: 700, color: 'var(--text-0)', letterSpacing: 0.3 }}>
            {cfg.label}
          </Typography>
        </Box>

        <Box sx={{ color: 'var(--line)', fontSize: 16, flexShrink: 0 }}>│</Box>

        {/* Material chip — click to open picker */}
        <Tooltip title="Click to change material">
          <Box
            ref={chipRef}
            onClick={e => setAnchorEl(e.currentTarget as HTMLElement)}
            sx={{
              display: 'flex', alignItems: 'center', gap: 0.5,
              px: 1.25, py: 0.4,
              borderRadius: 1.5,
              border: `1px solid ${cfg.color}44`,
              bgcolor: `${cfg.color}14`,
              cursor: 'pointer',
              transition: 'all 0.15s',
              '&:hover': { bgcolor: `${cfg.color}28`, borderColor: `${cfg.color}88` },
              flex: 1,
              minWidth: 0,
            }}
          >
            {saving ? (
              <CircularProgress size={12} sx={{ color: cfg.color, flexShrink: 0 }} />
            ) : (
              <Box sx={{ width: 8, height: 8, borderRadius: 1, bgcolor: cfg.color, flexShrink: 0 }} />
            )}
            <Typography sx={{
              fontSize: 11, color: current ? cfg.color : 'var(--text-4)',
              fontWeight: 500, flex: 1, minWidth: 0,
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>
              {current?.replace(/_/g, ' ') ?? 'No material assigned'}
            </Typography>
            <ArrowDropDownIcon sx={{ fontSize: 16, color: cfg.color, flexShrink: 0 }} />
          </Box>
        </Tooltip>

        {/* Dismiss */}
        <Tooltip title="Deselect (Escape)">
          <IconButton size="small" onClick={() => setSelectedPart(null)} sx={{ color: 'var(--text-4)', flexShrink: 0, p: 0.25 }}>
            <CloseIcon sx={{ fontSize: 15 }} />
          </IconButton>
        </Tooltip>
      </Box>

      {/* ── Material picker popover (shared with the Materials tab "Full" list) ── */}
      <MaterialPickerPopover
        open={open}
        anchorEl={anchorEl}
        onClose={() => setAnchorEl(null)}
        title={`MATERIALS — ${cfg.label.toUpperCase()}`}
        groups={groupedMats.map(g => ({ key: g.key, label: g.label, color: g.color, items: g.names.map(id => ({ id })) }))}
        current={current}
        disabled={saving}
        onPick={name => { assign(cfg.assignKey, name); setAnchorEl(null); }}
      />
    </>
  );
};

export default MaterialBar;
