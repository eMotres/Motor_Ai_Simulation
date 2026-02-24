import React from 'react';
import { Box, Slider, Typography, Stack, Button, ToggleButtonGroup, ToggleButton, Tooltip } from '@mui/material';
import { useUIStore } from '../../stores/motorStore';
import { CameraAlt as CameraAltIcon } from '@mui/icons-material';

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
        top: 64,
        left: 0,
        right: 0,
        zIndex: 1000,
        bgcolor: 'rgba(30, 30, 30, 0.95)',
        borderBottom: '1px solid rgba(255,255,255,0.1)',
        px: 2,
        py: 1,
      }}
    >
      <Stack direction="row" spacing={3} alignItems="center">
        {/* Preset buttons */}
        <Stack direction="row" spacing={1} sx={{ minWidth: 180 }}>
          <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.7)', mr: 1 }}>
            Presets:
          </Typography>
          <Button
            size="small"
            variant="outlined"
            onClick={() => handlePreset('matte')}
            sx={{
              minWidth: 60,
              fontSize: '0.7rem',
              borderColor: 'rgba(255,255,255,0.3)',
              color: 'rgba(255,255,255,0.8)',
              '&:hover': { borderColor: 'rgba(255,255,255,0.5)', bgcolor: 'rgba(255,255,255,0.1)' }
            }}
          >
            Matte
          </Button>
          <Button
            size="small"
            variant="outlined"
            onClick={() => handlePreset('polished')}
            sx={{
              minWidth: 60,
              fontSize: '0.7rem',
              borderColor: 'rgba(255,255,255,0.3)',
              color: 'rgba(255,255,255,0.8)',
              '&:hover': { borderColor: 'rgba(255,255,255,0.5)', bgcolor: 'rgba(255,255,255,0.1)' }
            }}
          >
            Polished
          </Button>
          <Button
            size="small"
            variant="outlined"
            onClick={() => handlePreset('metallic')}
            sx={{
              minWidth: 60,
              fontSize: '0.7rem',
              borderColor: 'rgba(255,255,255,0.3)',
              color: 'rgba(255,255,255,0.8)',
              '&:hover': { borderColor: 'rgba(255,255,255,0.5)', bgcolor: 'rgba(255,255,255,0.1)' }
            }}
          >
            Metallic
          </Button>
        </Stack>

        {/* Metalness slider */}
        <Stack direction="row" spacing={1} alignItems="center" sx={{ flex: 1, maxWidth: 200 }}>
          <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.7)', minWidth: 60 }}>
            Metalness
          </Typography>
          <Slider
            value={metalness}
            min={0}
            max={1}
            step={0.01}
            onChange={(_, v) => updateMaterialSettings({ metalness: v as number })}
            sx={{
              color: '#fbbf24',
              '& .MuiSlider-thumb': { width: 12, height: 12 },
              '& .MuiSlider-track': { height: 4 },
              '& .MuiSlider-rail': { height: 4, bgcolor: 'rgba(255,255,255,0.2)' }
            }}
          />
          <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.5)', minWidth: 30 }}>
            {metalness.toFixed(2)}
          </Typography>
        </Stack>

        {/* Roughness slider */}
        <Stack direction="row" spacing={1} alignItems="center" sx={{ flex: 1, maxWidth: 200 }}>
          <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.7)', minWidth: 60 }}>
            Roughness
          </Typography>
          <Slider
            value={roughness}
            min={0}
            max={1}
            step={0.01}
            onChange={(_, v) => updateMaterialSettings({ roughness: v as number })}
            sx={{
              color: '#60a5fa',
              '& .MuiSlider-thumb': { width: 12, height: 12 },
              '& .MuiSlider-track': { height: 4 },
              '& .MuiSlider-rail': { height: 4, bgcolor: 'rgba(255,255,255,0.2)' }
            }}
          />
          <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.5)', minWidth: 30 }}>
            {roughness.toFixed(2)}
          </Typography>
        </Stack>

        {/* Environment intensity slider */}
        <Stack direction="row" spacing={1} alignItems="center" sx={{ flex: 1, maxWidth: 200 }}>
          <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.7)', minWidth: 80 }}>
            Env Intensity
          </Typography>
          <Slider
            value={envIntensity}
            min={0}
            max={2}
            step={0.01}
            onChange={(_, v) => updateMaterialSettings({ envIntensity: v as number })}
            sx={{
              color: '#a78bfa',
              '& .MuiSlider-thumb': { width: 12, height: 12 },
              '& .MuiSlider-track': { height: 4 },
              '& .MuiSlider-rail': { height: 4, bgcolor: 'rgba(255,255,255,0.2)' }
            }}
          />
          <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.5)', minWidth: 30 }}>
            {envIntensity.toFixed(2)}
          </Typography>
        </Stack>

        {/* Camera Mode Toggle */}
        <Stack direction="row" spacing={1} alignItems="center" sx={{ minWidth: 180 }}>
          <Tooltip title="Camera Projection">
            <CameraAltIcon sx={{ color: 'rgba(255,255,255,0.7)', fontSize: 20 }} />
          </Tooltip>
          <ToggleButtonGroup
            value={cameraMode}
            exclusive
            onChange={(_, newMode) => newMode && setCameraMode(newMode)}
            size="small"
            sx={{
              '& .MuiToggleButton-root': {
                color: 'rgba(255,255,255,0.7)',
                borderColor: 'rgba(255,255,255,0.3)',
                fontSize: '0.7rem',
                px: 1,
                py: 0.25,
                '&.Mui-selected': {
                  bgcolor: 'rgba(59, 130, 246, 0.3)',
                  color: '#3b82f6',
                  '&:hover': {
                    bgcolor: 'rgba(59, 130, 246, 0.4)',
                  }
                }
              }
            }}
          >
            <ToggleButton value="orthographic">
              Ortho
            </ToggleButton>
            <ToggleButton value="perspective">
              Perspective
            </ToggleButton>
          </ToggleButtonGroup>
        </Stack>
      </Stack>
    </Box>
  );
};

export default MaterialControls;
