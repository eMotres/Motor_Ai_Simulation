import React, { useState } from 'react';
import {
  Box, Typography, CircularProgress, Collapse,
  List, ListItemButton, ListItemText, ListItemIcon,
  Chip,
} from '@mui/material';
import {
  ExpandMore as ExpandMoreIcon,
  ExpandLess as ExpandLessIcon,
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
}

// ─── Component ────────────────────────────────────────────────────────────────

const MaterialsLibraryTree: React.FC<Props> = ({ library, loading, error, selected, onSelect }) => {
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
              {isOpen ? <ExpandLessIcon sx={{ fontSize: 14, color: '#475569' }} /> : <ExpandMoreIcon sx={{ fontSize: 14, color: '#475569' }} />}
            </ListItemButton>

            {/* Material items */}
            <Collapse in={isOpen} timeout="auto">
              <List dense disablePadding sx={{ ml: 2, mb: 1 }}>
                {items.map(name => {
                  const isSelected = selected?.category === cat.key && selected?.name === name;
                  const data = (library[cat.key] as any)[name];

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
                        <Typography sx={{
                          fontSize: '0.72rem',
                          color: isSelected ? '#e2e8f0' : '#94a3b8',
                          fontWeight: isSelected ? 600 : 400,
                          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                        }}>
                          {friendlyName(name)}
                        </Typography>
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
  if (category === 'steel') {
    const kf = data.stacking_factor ?? '?';
    const kh = data.core_loss_kh?.toFixed(1) ?? '?';
    return `kf=${kf}  kh=${kh}`;
  }
  if (category === 'magnet') {
    const Br = data.Br?.toFixed(2) ?? '?';
    const Hc = data.Hc ? `${(data.Hc / 1000).toFixed(0)} kA/m` : '?';
    return `Br=${Br} T  Hc=${Hc}`;
  }
  if (category === 'conductor') {
    const s = data.sigma ? `${(data.sigma / 1e6).toFixed(1)} MS/m` : '?';
    return `σ=${s}`;
  }
  if (category === 'insulator') {
    const k = data.thermal_conductivity != null ? `${data.thermal_conductivity}` : '?';
    const cp = data.specific_heat != null ? `${data.specific_heat}` : '?';
    return `k=${k} W/m·K  cp=${cp}`;
  }
  if (category === 'coolant') {
    const k = data.thermal_conductivity != null ? `${data.thermal_conductivity}` : '?';
    const cp = data.specific_heat != null ? `${data.specific_heat}` : '?';
    return `${data.phase ?? ''} · k=${k}  cp=${cp}`;
  }
  return '';
}

export default MaterialsLibraryTree;
