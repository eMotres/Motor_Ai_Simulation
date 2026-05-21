import React, { useState } from 'react';
import {
  Box, Typography, Select, MenuItem, FormControl,
  TextField, Divider, Stack, Tooltip, Chip,
} from '@mui/material';
import { useMotorStore } from '../../stores/motorStore';
import type { MaterialConfig, ComponentMaterialKey } from '../../stores/motorStore';

// ─── Material presets ────────────────────────────────────────────────────────

const STEEL_PRESETS: Record<string, Partial<MaterialConfig['stator']>> = {
  'm19_silicon_steel': { name: 'M19 Silicon Steel', mu_r: 5000, sigma: 1.9e6, B_sat: 1.80, density: 7650, lamination_mm: 0.356 },
  'm27_silicon_steel': { name: 'M27 Silicon Steel', mu_r: 4000, sigma: 2.5e6, B_sat: 1.70, density: 7700, lamination_mm: 0.457 },
  'm36_silicon_steel': { name: 'M36 Silicon Steel', mu_r: 3500, sigma: 3.0e6, B_sat: 1.65, density: 7750, lamination_mm: 0.470 },
  'cobalt_iron':       { name: 'Cobalt-Iron (Hiperco)', mu_r: 8000, sigma: 2.3e6, B_sat: 2.30, density: 8120, lamination_mm: 0.100 },
  'amorphous_metal':   { name: 'Amorphous Metal', mu_r: 10000, sigma: 0.8e6, B_sat: 1.56, density: 7180, lamination_mm: 0.025 },
};

const MAGNET_PRESETS: Record<string, Partial<MaterialConfig['magnets']>> = {
  'ndfeb_n42':  { name: 'NdFeB N42',    Br: 1.28, Hc: 979,  mu_rec: 1.05, density: 7500, max_temp_c: 150 },
  'ndfeb_n52':  { name: 'NdFeB N52',    Br: 1.44, Hc: 995,  mu_rec: 1.05, density: 7600, max_temp_c: 80  },
  'ndfeb_n35h': { name: 'NdFeB N35H',   Br: 1.17, Hc: 1114, mu_rec: 1.05, density: 7500, max_temp_c: 120 },
  'smco_26':    { name: 'SmCo 26',      Br: 1.05, Hc: 796,  mu_rec: 1.05, density: 8400, max_temp_c: 300 },
  'ferrite_y35':{ name: 'Ferrite Y35',  Br: 0.39, Hc: 275,  mu_rec: 1.06, density: 5000, max_temp_c: 250 },
};

const WINDING_PRESETS: Record<string, Partial<MaterialConfig['windings']>> = {
  'copper':     { name: 'Copper',        sigma: 5.96e7, fill_factor: 0.55, density: 8960, alpha: 0.00393 },
  'aluminum':   { name: 'Aluminum',      sigma: 3.50e7, fill_factor: 0.55, density: 2700, alpha: 0.00429 },
  'litz_copper':{ name: 'Litz (Copper)', sigma: 4.00e7, fill_factor: 0.45, density: 8960, alpha: 0.00393 },
};

const SHAFT_PRESETS: Record<string, Partial<MaterialConfig['shaft']>> = {
  'carbon_steel_1045': { name: 'Carbon Steel 1045', density: 7850, yield_strength_mpa: 530,  tensile_mpa: 625 },
  'alloy_steel_4340':  { name: 'Alloy Steel 4340',  density: 7850, yield_strength_mpa: 855,  tensile_mpa: 1000 },
  'stainless_304':     { name: 'Stainless 304',      density: 7900, yield_strength_mpa: 215,  tensile_mpa: 505 },
  'titanium_grade5':   { name: 'Titanium Grade 5',   density: 4430, yield_strength_mpa: 880,  tensile_mpa: 950 },
};

// ─── Helper components ────────────────────────────────────────────────────────

const SectionHeader: React.FC<{ label: string; color: string; chip?: string }> = ({ label, color, chip }) => (
  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
    <Box sx={{ width: 10, height: 10, borderRadius: '2px', bgcolor: color, flexShrink: 0 }} />
    <Typography sx={{ fontSize: '0.7rem', fontWeight: 700, color: '#94a3b8', letterSpacing: '0.08em', textTransform: 'uppercase', flex: 1 }}>
      {label}
    </Typography>
    {chip && <Chip label={chip} size="small" sx={{ height: 16, fontSize: '0.6rem', bgcolor: 'rgba(59,130,246,0.15)', color: '#60a5fa' }} />}
  </Box>
);

interface PropRowProps {
  label: string;
  value: number;
  unit: string;
  decimals?: number;
  description?: string;
  onChange: (v: number) => void;
}

const PropRow: React.FC<PropRowProps> = ({ label, value, unit, decimals = 3, description, onChange }) => {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');

  const displayVal = value >= 1e6
    ? `${(value / 1e6).toFixed(2)}M`
    : value >= 1e3
    ? `${(value / 1e3).toFixed(1)}k`
    : value.toFixed(decimals);

  return (
    <Tooltip title={description || ''} placement="right" arrow disableHoverListener={!description}>
      <Box sx={{
        display: 'flex', alignItems: 'center',
        py: '3px', px: 1,
        '&:hover': { bgcolor: 'rgba(255,255,255,0.03)' },
        borderRadius: 1,
        gap: 1,
      }}>
        <Typography sx={{ flex: 1, fontSize: '0.72rem', color: '#94a3b8', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {label}
        </Typography>
        {editing ? (
          <TextField
            autoFocus
            size="small"
            defaultValue={value}
            onBlur={e => {
              const n = parseFloat(e.target.value);
              if (!isNaN(n)) onChange(n);
              setEditing(false);
            }}
            onKeyDown={e => {
              if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
              if (e.key === 'Escape') setEditing(false);
            }}
            sx={{
              width: 80,
              '& .MuiInputBase-input': { fontSize: '0.72rem', py: '2px', px: '6px', textAlign: 'right' },
            }}
          />
        ) : (
          <Box
            onClick={() => { setDraft(String(value)); setEditing(true); }}
            sx={{
              width: 80, textAlign: 'right', cursor: 'text',
              fontSize: '0.72rem', color: '#cbd5e1',
              fontVariantNumeric: 'tabular-nums',
              '&:hover': { color: '#3b82f6' },
            }}
          >
            {displayVal}
          </Box>
        )}
        <Typography sx={{ width: 40, fontSize: '0.65rem', color: '#475569', flexShrink: 0 }}>
          {unit}
        </Typography>
      </Box>
    </Tooltip>
  );
};

// ─── MaterialsPanel ──────────────────────────────────────────────────────────

const MaterialsPanel: React.FC = () => {
  const { materialConfig, updateComponentMaterial } = useMotorStore();

  const upd = (comp: ComponentMaterialKey) => (patch: Record<string, number | string>) =>
    updateComponentMaterial(comp, patch);

  return (
    <Box sx={{ height: '100%', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 0 }}>

      {/* ── Stator Core ── */}
      <Section>
        <SectionHeader label="Stator Core" color="#7f8c8d" />
        <PresetSelect
          presets={STEEL_PRESETS}
          value={materialConfig.stator.preset}
          onSelect={key => upd('stator')({ preset: key, ...STEEL_PRESETS[key] })}
        />
        <PropRow label="Relative permeability μr" value={materialConfig.stator.mu_r} unit="" decimals={0}
          description="Relative magnetic permeability"
          onChange={v => upd('stator')({ mu_r: v })} />
        <PropRow label="Conductivity σ" value={materialConfig.stator.sigma} unit="S/m" decimals={2}
          description="Electrical conductivity"
          onChange={v => upd('stator')({ sigma: v })} />
        <PropRow label="Saturation flux B_sat" value={materialConfig.stator.B_sat} unit="T" decimals={2}
          description="Magnetic saturation flux density"
          onChange={v => upd('stator')({ B_sat: v })} />
        <PropRow label="Density" value={materialConfig.stator.density} unit="kg/m³" decimals={0}
          onChange={v => upd('stator')({ density: v })} />
        <PropRow label="Lamination thickness" value={materialConfig.stator.lamination_mm} unit="mm" decimals={3}
          description="Individual lamination sheet thickness"
          onChange={v => upd('stator')({ lamination_mm: v })} />
      </Section>

      <Divider sx={{ borderColor: 'rgba(255,255,255,0.05)' }} />

      {/* ── Rotor Core ── */}
      <Section>
        <SectionHeader label="Rotor Core" color="#5d6d7e" />
        <PresetSelect
          presets={STEEL_PRESETS}
          value={materialConfig.rotor.preset}
          onSelect={key => upd('rotor')({ preset: key, ...STEEL_PRESETS[key] })}
        />
        <PropRow label="Relative permeability μr" value={materialConfig.rotor.mu_r} unit="" decimals={0}
          onChange={v => upd('rotor')({ mu_r: v })} />
        <PropRow label="Conductivity σ" value={materialConfig.rotor.sigma} unit="S/m" decimals={2}
          onChange={v => upd('rotor')({ sigma: v })} />
        <PropRow label="Saturation flux B_sat" value={materialConfig.rotor.B_sat} unit="T" decimals={2}
          onChange={v => upd('rotor')({ B_sat: v })} />
        <PropRow label="Density" value={materialConfig.rotor.density} unit="kg/m³" decimals={0}
          onChange={v => upd('rotor')({ density: v })} />
        <PropRow label="Lamination thickness" value={materialConfig.rotor.lamination_mm} unit="mm" decimals={3}
          onChange={v => upd('rotor')({ lamination_mm: v })} />
      </Section>

      <Divider sx={{ borderColor: 'rgba(255,255,255,0.05)' }} />

      {/* ── Magnets ── */}
      <Section>
        <SectionHeader label="Magnets" color="#ef4444" />
        <PresetSelect
          presets={MAGNET_PRESETS}
          value={materialConfig.magnets.preset}
          onSelect={key => upd('magnets')({ preset: key, ...MAGNET_PRESETS[key] })}
        />
        <PropRow label="Remanence Br" value={materialConfig.magnets.Br} unit="T" decimals={3}
          description="Residual flux density at zero field"
          onChange={v => upd('magnets')({ Br: v })} />
        <PropRow label="Coercivity Hc" value={materialConfig.magnets.Hc} unit="kA/m" decimals={1}
          description="Magnetic field strength to demagnetize"
          onChange={v => upd('magnets')({ Hc: v })} />
        <PropRow label="Recoil permeability μrec" value={materialConfig.magnets.mu_rec} unit="" decimals={3}
          description="Relative permeability in the working point"
          onChange={v => upd('magnets')({ mu_rec: v })} />
        <PropRow label="Density" value={materialConfig.magnets.density} unit="kg/m³" decimals={0}
          onChange={v => upd('magnets')({ density: v })} />
        <PropRow label="Max temperature" value={materialConfig.magnets.max_temp_c} unit="°C" decimals={0}
          description="Maximum operating temperature before demagnetization"
          onChange={v => upd('magnets')({ max_temp_c: v })} />
      </Section>

      <Divider sx={{ borderColor: 'rgba(255,255,255,0.05)' }} />

      {/* ── Windings ── */}
      <Section>
        <SectionHeader label="Windings" color="#b87333" />
        <PresetSelect
          presets={WINDING_PRESETS}
          value={materialConfig.windings.preset}
          onSelect={key => upd('windings')({ preset: key, ...WINDING_PRESETS[key] })}
        />
        <PropRow label="Conductivity σ" value={materialConfig.windings.sigma} unit="S/m" decimals={2}
          description="Electrical conductivity at 20°C"
          onChange={v => upd('windings')({ sigma: v })} />
        <PropRow label="Fill factor kfill" value={materialConfig.windings.fill_factor} unit="" decimals={3}
          description="Ratio of conductor area to slot area"
          onChange={v => upd('windings')({ fill_factor: v })} />
        <PropRow label="Density" value={materialConfig.windings.density} unit="kg/m³" decimals={0}
          onChange={v => upd('windings')({ density: v })} />
        <PropRow label="Temp. coefficient α" value={materialConfig.windings.alpha} unit="1/°C" decimals={5}
          description="Resistivity temperature coefficient"
          onChange={v => upd('windings')({ alpha: v })} />
      </Section>

      <Divider sx={{ borderColor: 'rgba(255,255,255,0.05)' }} />

      {/* ── Shaft ── */}
      <Section>
        <SectionHeader label="Shaft" color="#374151" chip="Structural" />
        <PresetSelect
          presets={SHAFT_PRESETS}
          value={materialConfig.shaft.preset}
          onSelect={key => upd('shaft')({ preset: key, ...SHAFT_PRESETS[key] })}
        />
        <PropRow label="Density" value={materialConfig.shaft.density} unit="kg/m³" decimals={0}
          onChange={v => upd('shaft')({ density: v })} />
        <PropRow label="Yield strength" value={materialConfig.shaft.yield_strength_mpa} unit="MPa" decimals={0}
          onChange={v => upd('shaft')({ yield_strength_mpa: v })} />
        <PropRow label="Tensile strength" value={materialConfig.shaft.tensile_mpa} unit="MPa" decimals={0}
          onChange={v => upd('shaft')({ tensile_mpa: v })} />
      </Section>

    </Box>
  );
};

// ─── Small helpers ────────────────────────────────────────────────────────────

const Section: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <Box sx={{ px: 1.5, pt: 1.5, pb: 1 }}>
    {children}
  </Box>
);

const PresetSelect: React.FC<{
  presets: Record<string, { name?: string }>;
  value: string;
  onSelect: (key: string) => void;
}> = ({ presets, value, onSelect }) => (
  <FormControl size="small" fullWidth sx={{ mb: 1 }}>
    <Select
      value={value}
      onChange={e => onSelect(e.target.value)}
      displayEmpty
      sx={{
        fontSize: '0.72rem',
        height: 26,
        bgcolor: 'rgba(255,255,255,0.04)',
        '& .MuiSelect-select': { py: '3px' },
        '& .MuiOutlinedInput-notchedOutline': { borderColor: 'rgba(255,255,255,0.1)' },
        '&:hover .MuiOutlinedInput-notchedOutline': { borderColor: 'rgba(255,255,255,0.25)' },
        '&.Mui-focused .MuiOutlinedInput-notchedOutline': { borderColor: '#3b82f6' },
      }}
    >
      {Object.entries(presets).map(([key, mat]) => (
        <MenuItem key={key} value={key} sx={{ fontSize: '0.72rem', py: '3px' }}>
          {mat.name ?? key}
        </MenuItem>
      ))}
    </Select>
  </FormControl>
);

export default MaterialsPanel;
