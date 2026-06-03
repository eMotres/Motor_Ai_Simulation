/**
 * Fusion360-style floating material bar.
 * Appears at the bottom of the 3D viewer when a part is selected.
 * Shows the current assigned material and lets the user reassign it.
 */
import React, { useState, useRef, useEffect } from 'react';
import {
  Box, Typography, Chip, Popover, List, ListItem, ListItemButton,
  ListItemText, Divider, IconButton, Tooltip, CircularProgress,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import ArrowDropDownIcon from '@mui/icons-material/ArrowDropDown';
import CheckIcon from '@mui/icons-material/Check';
import { useUIStore, type CompKey } from '../../stores/motorStore';
import { useMotorAssignments } from '../materials/useMotorAssignments';
import { useMaterialsLibrary } from '../materials/useMaterialsLibrary';
import type { MotorAssignments } from '../materials/useMotorAssignments';

// ─── Part config ──────────────────────────────────────────────────────────────

interface PartCfg {
  assignKey: keyof MotorAssignments;
  label: string;
  color: string;
}

const PART_CFG: Record<CompKey, PartCfg> = {
  stator:   { assignKey: 'stator_core', label: 'Stator Core',           color: '#3b82f6' },
  rotor:    { assignKey: 'rotor_core',  label: 'Rotor Core',            color: '#2563eb' },
  magnets:  { assignKey: 'magnet',      label: 'Magnets',               color: '#ef4444' },
  coils:    { assignKey: 'slot',        label: 'Windings',              color: '#f59e0b' },
  shaft:    { assignKey: 'shaft',       label: 'Shaft',                 color: '#64748b' },
  in_band:  { assignKey: 'air_gap',     label: 'In Band (rotating)',    color: '#22c55e' },
  out_band: { assignKey: 'air_gap',     label: 'Out Band (static)',     color: '#a855f7' },
};

// All categories in display order
const CATEGORY_CFG: { key: 'steel' | 'magnet' | 'conductor'; label: string; color: string }[] = [
  { key: 'steel',     label: 'Lamination Steel', color: '#64748b' },
  { key: 'magnet',    label: 'Magnets',          color: '#ef4444' },
  { key: 'conductor', label: 'Metal',            color: '#f59e0b' },
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
        bgcolor: 'rgba(8, 15, 26, 0.92)',
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
          <Typography sx={{ fontSize: 12, fontWeight: 700, color: '#e2e8f0', letterSpacing: 0.3 }}>
            {cfg.label}
          </Typography>
        </Box>

        <Box sx={{ color: '#334155', fontSize: 16, flexShrink: 0 }}>│</Box>

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
              fontSize: 11, color: current ? cfg.color : '#475569',
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
          <IconButton size="small" onClick={() => setSelectedPart(null)} sx={{ color: '#475569', flexShrink: 0, p: 0.25 }}>
            <CloseIcon sx={{ fontSize: 15 }} />
          </IconButton>
        </Tooltip>
      </Box>

      {/* ── Material picker popover ── */}
      <Popover
        open={open}
        anchorEl={anchorEl}
        onClose={() => setAnchorEl(null)}
        anchorOrigin={{ vertical: 'top', horizontal: 'center' }}
        transformOrigin={{ vertical: 'bottom', horizontal: 'center' }}
        slotProps={{
          paper: {
            sx: {
              bgcolor: '#0d1929',
              border: '1px solid #1e293b',
              borderRadius: 2,
              minWidth: 260,
              maxHeight: 520,
              overflow: 'hidden',
              boxShadow: '0 8px 32px rgba(0,0,0,0.7)',
            },
          },
        }}
      >
        {/* Header */}
        <Box sx={{ px: 2, py: 1, borderBottom: '1px solid #1e293b' }}>
          <Typography sx={{ fontSize: 10, fontWeight: 700, color: '#475569', letterSpacing: 1 }}>
            MATERIALS — {cfg.label.toUpperCase()}
          </Typography>
        </Box>

        {/* Material list — all categories grouped */}
        <List dense disablePadding sx={{ overflowY: 'auto', maxHeight: 460 }}>
          {groupedMats.map((group, gi) => (
            <React.Fragment key={group.key}>
              {/* Category section header */}
              <Box sx={{
                px: 2, py: '4px',
                bgcolor: '#060d17',
                borderLeft: `2px solid ${group.color}`,
                mx: 1, mt: gi > 0 ? 0.5 : 0, borderRadius: '2px',
              }}>
                <Typography sx={{ fontSize: 9, fontWeight: 700, color: group.color, letterSpacing: 1 }}>
                  {group.label.toUpperCase()}
                </Typography>
              </Box>

              {group.names.map((name, i) => {
                const isActive = name === current;
                return (
                  <React.Fragment key={name}>
                    {i > 0 && <Divider sx={{ borderColor: '#0f172a' }} />}
                    <ListItemButton
                      onClick={() => { assign(cfg.assignKey, name); setAnchorEl(null); }}
                      disabled={saving}
                      sx={{
                        py: 0.75, px: 2,
                        bgcolor: isActive ? `${group.color}18` : 'transparent',
                        '&:hover': { bgcolor: `${group.color}28` },
                      }}
                    >
                      <ListItemText
                        primary={name.replace(/_/g, ' ')}
                        primaryTypographyProps={{
                          sx: {
                            fontSize: 11,
                            fontWeight: isActive ? 700 : 400,
                            color: isActive ? group.color : '#cbd5e1',
                          },
                        }}
                      />
                      {isActive && <CheckIcon sx={{ fontSize: 14, color: group.color, ml: 1 }} />}
                    </ListItemButton>
                  </React.Fragment>
                );
              })}
            </React.Fragment>
          ))}
          {groupedMats.length === 0 && (
            <ListItem>
              <ListItemText primary="No materials available" primaryTypographyProps={{ sx: { fontSize: 11, color: '#475569' } }} />
            </ListItem>
          )}
        </List>
      </Popover>
    </>
  );
};

export default MaterialBar;
