import React, { useState } from 'react';
import {
  Box, Typography, CircularProgress, Collapse,
  List, ListItemButton, ListItemText, ListItemIcon,
  Chip, Tooltip,
} from '@mui/material';
import {
  ExpandMore as ExpandMoreIcon,
  ExpandLess as ExpandLessIcon,
  Add as AddIcon,
  Layers as LayersIcon,
  RadioButtonChecked as MagnetIcon,
  Cable as CableIcon,
  Shield as InsulatorIcon,
  WaterDrop as CoolantIcon,
} from '@mui/icons-material';
import type { MaterialsLibrary, SelectedMaterial, MaterialCategory } from './useMaterialsLibrary';

// ─── Category config ──────────────────────────────────────────────────────────

const CATEGORIES: {
  key: MaterialCategory;
  label: string;
  color: string;
  icon: React.ReactNode;
  chip?: string;
}[] = [
  { key: 'steel',     label: 'Lamination Steel', color: '#64748b', icon: <LayersIcon sx={{ fontSize: 14 }} />, chip: 'EM' },
  { key: 'magnet',    label: 'Magnets',           color: '#ef4444', icon: <MagnetIcon sx={{ fontSize: 14 }} />, chip: 'PM' },
  { key: 'conductor', label: 'Metal',              color: '#f59e0b', icon: <CableIcon  sx={{ fontSize: 14 }} /> },
  { key: 'insulator', label: 'Insulators',         color: '#3fae5a', icon: <InsulatorIcon sx={{ fontSize: 14 }} />, chip: 'INS' },
  { key: 'coolant',   label: 'Coolants & Air',     color: '#38bdf8', icon: <CoolantIcon sx={{ fontSize: 14 }} />, chip: 'FLU' },
];

// Short human-friendly label from database key
function friendlyName(key: string): string {
  return key.replace(/_/g, ' ');
}

// ─── Props ────────────────────────────────────────────────────────────────────

interface Props {
  library: MaterialsLibrary | null;
  loading: boolean;
  error: string | null;
  selected: SelectedMaterial | null;
  onSelect: (sel: SelectedMaterial) => void;
  /** Admin: show a "+" on each category to add a new shared material. */
  canAdd?: boolean;
  onAdd?: (category: MaterialCategory) => void;
}

// ─── Component ────────────────────────────────────────────────────────────────

const MaterialsLibraryTree: React.FC<Props> = ({ library, loading, error, selected, onSelect, canAdd, onAdd }) => {
  const [open, setOpen] = useState<Record<MaterialCategory, boolean>>({
    steel: true, magnet: true, conductor: true, insulator: true, coolant: true,
  });

  const toggle = (cat: MaterialCategory) =>
    setOpen(prev => ({ ...prev, [cat]: !prev[cat] }));

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', mt: 4 }}>
        <CircularProgress size={24} />
      </Box>
    );
  }

  if (error || !library) {
    return (
      <Box sx={{ p: 2 }}>
        <Typography variant="caption" color="error">
          {error ?? 'No data'}
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ height: '100%', overflowY: 'auto' }}>
      {/* Header */}
      <Box sx={{ px: 2, pt: 2, pb: 1 }}>
        <Typography sx={{ fontSize: '0.65rem', fontWeight: 700, color: '#475569', letterSpacing: '0.1em', textTransform: 'uppercase' }}>
          Materials Library
        </Typography>
      </Box>

      {CATEGORIES.map(cat => {
        const items = Object.keys(library[cat.key] ?? {});
        const isOpen = open[cat.key];

        return (
          <Box key={cat.key}>
            {/* Group header */}
            <ListItemButton
              dense
              onClick={() => toggle(cat.key)}
              sx={{
                px: 1.5, py: '6px',
                borderLeft: `3px solid ${cat.color}`,
                mx: 1, borderRadius: 1,
                mb: '2px',
                bgcolor: 'rgba(255,255,255,0.03)',
                '&:hover': { bgcolor: 'rgba(255,255,255,0.06)' },
              }}
            >
              <ListItemIcon sx={{ minWidth: 24, color: cat.color }}>
                {cat.icon}
              </ListItemIcon>
              <ListItemText
                primary={cat.label}
                primaryTypographyProps={{
                  sx: { fontSize: '0.75rem', fontWeight: 600, color: '#cbd5e1' },
                }}
              />
              {cat.chip && (
                <Chip
                  label={cat.chip}
                  size="small"
                  sx={{ height: 16, fontSize: '0.6rem', bgcolor: `${cat.color}22`, color: cat.color, mr: 0.5 }}
                />
              )}
              <Typography sx={{ fontSize: '0.65rem', color: '#475569', mr: 0.5 }}>
                {items.length}
              </Typography>
              {canAdd && onAdd && (
                <Tooltip title={`Add a new ${cat.label} material (shared library)`}>
                  <Box
                    component="span"
                    role="button"
                    aria-label={`add ${cat.label} material`}
                    onClick={(e) => { e.stopPropagation(); onAdd(cat.key); }}
                    sx={{ display: 'inline-flex', alignItems: 'center', p: 0.25, mr: 0.25,
                          cursor: 'pointer', color: '#475569', '&:hover': { color: cat.color } }}
                  >
                    <AddIcon sx={{ fontSize: 14 }} />
                  </Box>
                </Tooltip>
              )}
              {isOpen ? <ExpandLessIcon sx={{ fontSize: 14, color: '#475569' }} /> : <ExpandMoreIcon sx={{ fontSize: 14, color: '#475569' }} />}
            </ListItemButton>

            {/* Material items */}
            <Collapse in={isOpen} timeout="auto">
              <List dense disablePadding sx={{ ml: 2, mb: 1 }}>
                {items.map(name => {
                  const isSelected = selected?.category === cat.key && selected?.name === name;
                  const data = (library[cat.key] as any)?.[name] ?? {};

                  return (
                    <ListItemButton
                      key={name}
                      selected={isSelected}
                      onClick={() => onSelect({ category: cat.key, name })}
                      sx={{
                        px: 1.5, py: '4px',
                        borderRadius: 1,
                        mb: '1px',
                        '&.Mui-selected': {
                          bgcolor: `${cat.color}18`,
                          borderLeft: `2px solid ${cat.color}`,
                          '&:hover': { bgcolor: `${cat.color}28` },
                        },
                        '&:hover': { bgcolor: 'rgba(255,255,255,0.04)' },
                      }}
                    >
                      <Box sx={{ flex: 1, minWidth: 0 }}>
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, minWidth: 0 }}>
                          <Typography sx={{
                            fontSize: '0.72rem',
                            color: isSelected ? '#e2e8f0' : '#94a3b8',
                            fontWeight: isSelected ? 600 : 400,
                            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                          }}>
                            {friendlyName(name)}
                          </Typography>
                          {data?._source === 'mine' && (
                            <Chip label="mine" size="small" sx={{ height: 14, fontSize: '0.55rem', bgcolor: '#a78bfa22', color: '#a78bfa', flexShrink: 0, '& .MuiChip-label': { px: 0.5 } }} />
                          )}
                          {data?._source === 'global' && (
                            <Chip label="shared" size="small" sx={{ height: 14, fontSize: '0.55rem', bgcolor: '#38bdf822', color: '#38bdf8', flexShrink: 0, '& .MuiChip-label': { px: 0.5 } }} />
                          )}
                        </Box>
                        <Typography sx={{ fontSize: '0.62rem', color: '#475569', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {subtitleFor(cat.key, data)}
                        </Typography>
                      </Box>
                    </ListItemButton>
                  );
                })}
              </List>
            </Collapse>
          </Box>
        );
      })}
    </Box>
  );
};

function subtitleFor(category: MaterialCategory, data: any): string {
  if (!data) return '';
  const num = (v: any, d = 1) => (typeof v === 'number' && isFinite(v) ? v.toFixed(d) : '?');
  if (category === 'steel')
    return `kf=${data.stacking_factor ?? '?'}  kh=${num(data.core_loss_kh)}`;
  if (category === 'magnet')
    return `Br=${num(data.Br, 2)} T  Hc=${typeof data.Hc === 'number' ? `${(data.Hc / 1000).toFixed(0)} kA/m` : '?'}`;
  if (category === 'conductor')
    return `σ=${typeof data.sigma === 'number' && data.sigma ? `${(data.sigma / 1e6).toFixed(1)} MS/m` : '?'}`;
  if (category === 'insulator')
    return `k=${data.thermal_conductivity ?? '?'} W/m·K  cp=${data.specific_heat ?? '?'}`;
  if (category === 'coolant')
    return `${data.phase ?? ''} · k=${data.thermal_conductivity ?? '?'}  cp=${data.specific_heat ?? '?'}`;
  return '';
}

export default MaterialsLibraryTree;
