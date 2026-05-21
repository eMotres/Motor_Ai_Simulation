import React from 'react';
import { Box, Slider, Typography, Stack, Button, ToggleButtonGroup, ToggleButton } from '@mui/material';
import { useUIStore } from '../../stores/motorStore';

interface SliderItemProps {
  label: string;
  value: number;
  color: string;
  max?: number;
  onChange: (v: number) => void;
}

const SliderItem: React.FC<SliderItemProps> = ({ label, value, color, max = 1, onChange }) => (
  <Stack direction="row" spacing={0.75} alignItems="center" sx={{ minWidth: 120, flex: 1, maxWidth: 180 }}>
    <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.6)', minWidth: 32, fontSize: '0.65rem' }}>
      {label}
    </Typography>
    <Slider
      value={value}
      min={0}
      max={max}
      step={0.01}
      onChange={(_, v) => onChange(v as number)}
      sx={{
        color,
        py: 0,
        '& .MuiSlider-thumb': { width: 10, height: 10 },
        '& .MuiSlider-track': { height: 3 },
        '& .MuiSlider-rail': { height: 3, bgcolor: 'rgba(255,255,255,0.15)' },
      }}
    />
    <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.45)', minWidth: 24, fontSize: '0.65rem', textAlign: 'right' }}>
      {value.toFixed(2)}
    </Typography>
  </Stack>
);

const MaterialControls: React.FC = () => {
  const { metalness, roughness, envIntensity, updateMaterialSettings, cameraMode, setCameraMode } = useUIStore();

  const handlePreset = (preset: 'matte' | 'polished' | 'metallic') => {
    switch (preset) {
      case 'matte':
        updateMaterialSettings({ metalness: 0.1, roughness: 0.9 });
        break;
      case 'polished':
        updateMaterialSettings({ metalness: 0.9, roughness: 0.1 });
        break;
      case 'metallic':
        updateMaterialSettings({ metalness: 1.0, roughness: 0.3 });
        break;
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
        bgcolor: 'rgba(20, 20, 20, 0.92)',
        borderBottom: '1px solid rgba(255,255,255,0.08)',
        pl: '236px',   // clear the 220px ComponentTree panel + 8px offset + gap
        pr: 1.5,
        py: 0.5,
      }}
    >
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>

        {/* Preset buttons */}
        <Stack direction="row" spacing={0.5} alignItems="center">
          {(['matte', 'polished', 'metallic'] as const).map(p => (
            <Button
              key={p}
              size="small"
              variant="outlined"
              onClick={() => handlePreset(p)}
              sx={{
                minWidth: 0,
                px: 0.75,
                py: 0,
                fontSize: '0.65rem',
                lineHeight: '22px',
                borderColor: 'rgba(255,255,255,0.2)',
                color: 'rgba(255,255,255,0.7)',
                '&:hover': { borderColor: 'rgba(255,255,255,0.5)', bgcolor: 'rgba(255,255,255,0.08)' },
              }}
            >
              {p.charAt(0).toUpperCase() + p.slice(1)}
            </Button>
          ))}
        </Stack>

        <Box sx={{ width: 1, borderLeft: '1px solid rgba(255,255,255,0.1)', height: 18, mx: 0.5 }} />

        {/* Metalness */}
        <SliderItem label="Metal" value={metalness} color="#fbbf24"
          onChange={v => updateMaterialSettings({ metalness: v })} />

        {/* Roughness */}
        <SliderItem label="Rough" value={roughness} color="#60a5fa"
          onChange={v => updateMaterialSettings({ roughness: v })} />

        {/* Env */}
        <SliderItem label="Env" value={envIntensity} color="#a78bfa" max={2}
          onChange={v => updateMaterialSettings({ envIntensity: v })} />

        <Box sx={{ flex: 1 }} />

        {/* Camera Mode */}
        <ToggleButtonGroup
          value={cameraMode}
          exclusive
          onChange={(_, m) => m && setCameraMode(m)}
          size="small"
          sx={{
            height: 22,
            '& .MuiToggleButton-root': {
              color: 'rgba(255,255,255,0.6)',
              borderColor: 'rgba(255,255,255,0.2)',
              fontSize: '0.65rem',
              px: 0.75,
              py: 0,
              '&.Mui-selected': { bgcolor: 'rgba(59,130,246,0.3)', color: '#3b82f6' },
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
