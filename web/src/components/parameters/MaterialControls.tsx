import React from 'react';
import { Box, Slider, Stack, Button, ToggleButtonGroup, ToggleButton, Tooltip } from '@mui/material';
import { useUIStore } from '../../stores/motorStore';

interface SliderItemProps {
  label: string;
  value: number;
  color: string;
  max?: number;
  onChange: (v: number) => void;
}

const SliderItem: React.FC<SliderItemProps> = ({ label, value, color, max = 1, onChange }) => (
  <Tooltip title={`${label}: ${value.toFixed(2)}`} placement="bottom" arrow>
    <Stack direction="row" spacing={0.5} alignItems="center" sx={{ width: 100, flexShrink: 0 }}>
      <Box component="span" sx={{ color: 'rgba(255,255,255,0.45)', fontSize: '0.6rem', minWidth: 26, flexShrink: 0 }}>
        {label}
      </Box>
      <Slider
        value={value}
        min={0}
        max={max}
        step={0.01}
        onChange={(_, v) => onChange(v as number)}
        sx={{
          color,
          py: 0,
          '& .MuiSlider-thumb': { width: 8, height: 8 },
          '& .MuiSlider-track': { height: 2 },
          '& .MuiSlider-rail': { height: 2, bgcolor: 'rgba(255,255,255,0.12)' },
        }}
      />
    </Stack>
  </Tooltip>
);

const MaterialControls: React.FC = () => {
  const { metalness, roughness, envIntensity, updateMaterialSettings, cameraMode, setCameraMode } = useUIStore();

  const handlePreset = (preset: 'matte' | 'polished' | 'metallic') => {
    switch (preset) {
      case 'matte':     updateMaterialSettings({ metalness: 0.1, roughness: 0.9 }); break;
      case 'polished':  updateMaterialSettings({ metalness: 0.9, roughness: 0.1 }); break;
      case 'metallic':  updateMaterialSettings({ metalness: 1.0, roughness: 0.3 }); break;
    }
  };

  return (
    <Box
      sx={{
        position: 'absolute',
        top: 0,
        left: 0,
        right: 0,
        zIndex: 1000,
        bgcolor: 'rgba(20, 20, 20, 0.88)',
        borderBottom: '1px solid rgba(255,255,255,0.07)',
        pl: '236px',
        pr: 1,
        py: '3px',
      }}
    >
      <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="nowrap">

        {/* Preset buttons */}
        {(['matte', 'polished', 'metallic'] as const).map(p => (
          <Button
            key={p}
            size="small"
            variant="outlined"
            onClick={() => handlePreset(p)}
            sx={{
              minWidth: 0,
              px: 0.6,
              py: 0,
              fontSize: '0.6rem',
              lineHeight: '20px',
              borderColor: 'rgba(255,255,255,0.15)',
              color: 'rgba(255,255,255,0.55)',
              flexShrink: 0,
              '&:hover': { borderColor: 'rgba(255,255,255,0.4)', bgcolor: 'rgba(255,255,255,0.06)' },
            }}
          >
            {p.charAt(0).toUpperCase() + p.slice(1)}
          </Button>
        ))}

        <Box sx={{ width: '1px', height: 14, bgcolor: 'rgba(255,255,255,0.1)', mx: 0.25, flexShrink: 0 }} />

        {/* Sliders */}
        <SliderItem label="Metal" value={metalness} color="#fbbf24"
          onChange={v => updateMaterialSettings({ metalness: v })} />
        <SliderItem label="Rough" value={roughness} color="#60a5fa"
          onChange={v => updateMaterialSettings({ roughness: v })} />
        <SliderItem label="Env"   value={envIntensity} color="#a78bfa" max={2}
          onChange={v => updateMaterialSettings({ envIntensity: v })} />

        <Box sx={{ flex: 1 }} />

        {/* Camera mode */}
        <ToggleButtonGroup
          value={cameraMode}
          exclusive
          onChange={(_, m) => m && setCameraMode(m)}
          size="small"
          sx={{
            height: 20,
            flexShrink: 0,
            '& .MuiToggleButton-root': {
              color: 'rgba(255,255,255,0.5)',
              borderColor: 'rgba(255,255,255,0.15)',
              fontSize: '0.6rem',
              px: 0.6,
              py: 0,
              lineHeight: '20px',
              '&.Mui-selected': { bgcolor: 'rgba(59,130,246,0.25)', color: '#3b82f6' },
            },
          }}
        >
          <ToggleButton value="orthographic">Ortho</ToggleButton>
          <ToggleButton value="perspective">Persp</ToggleButton>
        </ToggleButtonGroup>
      </Stack>
    </Box>
  );
};

export default MaterialControls;
