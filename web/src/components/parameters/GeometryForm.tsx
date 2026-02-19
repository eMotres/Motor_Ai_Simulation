import React from 'react';
import { useForm, Controller } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import {
  TextField,
  Slider,
  Typography,
  Box,
  Divider,
  InputAdornment,
} from '@mui/material';
import { useMotorStore } from '../../stores/motorStore';

const geometrySchema = z.object({
  stator_diameter: z.number().min(10).max(1000),
  slot_height: z.number().min(1).max(100),
  core_thickness: z.number().min(0.5).max(50),
  num_seg: z.number().int().min(1).max(24),
  num_slots_per_segment: z.number().int().min(1).max(12),
  num_poles_per_segment: z.number().int().min(1).max(12),
  stator_width: z.number().min(5).max(500),
  air_gap: z.number().min(0.1).max(10),
  tooth_width: z.number().min(0.5).max(20),
  magnet_height: z.number().min(1).max(50),
  rotor_house_height: z.number().min(0.5).max(20),
});

type GeometryFormData = z.infer<typeof geometrySchema>;

const GeometryForm: React.FC = () => {
  const { geometry, updateGeometry } = useMotorStore();
  
  const { control, watch } = useForm<GeometryFormData>({
    resolver: zodResolver(geometrySchema),
    defaultValues: {
      stator_diameter: geometry.stator_diameter,
      slot_height: geometry.slot_height,
      core_thickness: geometry.core_thickness,
      num_seg: geometry.num_seg,
      num_slots_per_segment: geometry.num_slots_per_segment,
      num_poles_per_segment: geometry.num_poles_per_segment,
      stator_width: geometry.stator_width,
      air_gap: geometry.air_gap,
      tooth_width: geometry.tooth_width,
      magnet_height: geometry.magnet_height,
      rotor_house_height: geometry.rotor_house_height,
    },
  });
  
  // Watch for changes and update store
  React.useEffect(() => {
    const subscription = watch((value) => {
      updateGeometry(value as Partial<GeometryFormData>);
    });
    return () => subscription.unsubscribe();
  }, [watch, updateGeometry]);
  
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {/* Stator Section */}
      <Box>
        <Typography variant="subtitle2" color="primary" sx={{ mb: 2 }}>
          Stator Parameters
        </Typography>
        
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <Controller
            name="stator_diameter"
            control={control}
            render={({ field }) => (
              <TextField
                {...field}
                label="Stator Diameter"
                type="number"
                size="small"
                InputProps={{
                  endAdornment: <InputAdornment position="end">mm</InputAdornment>,
                }}
                onChange={(e) => field.onChange(parseFloat(e.target.value))}
              />
            )}
          />
          
          <Controller
            name="slot_height"
            control={control}
            render={({ field }) => (
              <TextField
                {...field}
                label="Slot Height"
                type="number"
                size="small"
                InputProps={{
                  endAdornment: <InputAdornment position="end">mm</InputAdornment>,
                }}
                onChange={(e) => field.onChange(parseFloat(e.target.value))}
              />
            )}
          />
          
          <Controller
            name="core_thickness"
            control={control}
            render={({ field }) => (
              <TextField
                {...field}
                label="Core Thickness"
                type="number"
                size="small"
                InputProps={{
                  endAdornment: <InputAdornment position="end">mm</InputAdornment>,
                }}
                onChange={(e) => field.onChange(parseFloat(e.target.value))}
              />
            )}
          />
          
          <Controller
            name="stator_width"
            control={control}
            render={({ field }) => (
              <TextField
                {...field}
                label="Stator Width (Axial)"
                type="number"
                size="small"
                InputProps={{
                  endAdornment: <InputAdornment position="end">mm</InputAdornment>,
                }}
                onChange={(e) => field.onChange(parseFloat(e.target.value))}
              />
            )}
          />
        </Box>
      </Box>
      
      <Divider />
      
      {/* Segmentation Section */}
      <Box>
        <Typography variant="subtitle2" color="primary" sx={{ mb: 2 }}>
          Segmentation
        </Typography>
        
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <Controller
            name="num_seg"
            control={control}
            render={({ field }) => (
              <Box>
                <Typography variant="caption" color="text.secondary">
                  Number of Segments: {field.value}
                </Typography>
                <Slider
                  value={field.value}
                  onChange={(_, value) => field.onChange(value)}
                  min={1}
                  max={24}
                  step={1}
                  marks
                  valueLabelDisplay="auto"
                />
              </Box>
            )}
          />
          
          <Controller
            name="num_slots_per_segment"
            control={control}
            render={({ field }) => (
              <Box>
                <Typography variant="caption" color="text.secondary">
                  Slots per Segment: {field.value}
                </Typography>
                <Slider
                  value={field.value}
                  onChange={(_, value) => field.onChange(value)}
                  min={1}
                  max={12}
                  step={1}
                  marks
                  valueLabelDisplay="auto"
                />
              </Box>
            )}
          />
          
          <Controller
            name="num_poles_per_segment"
            control={control}
            render={({ field }) => (
              <Box>
                <Typography variant="caption" color="text.secondary">
                  Poles per Segment: {field.value}
                </Typography>
                <Slider
                  value={field.value}
                  onChange={(_, value) => field.onChange(value)}
                  min={1}
                  max={12}
                  step={1}
                  marks
                  valueLabelDisplay="auto"
                />
              </Box>
            )}
          />
          
          <Typography variant="caption" color="text.secondary">
            Total Slots: {geometry.num_slots} | Total Poles: {geometry.num_poles}
          </Typography>
        </Box>
      </Box>
      
      <Divider />
      
      {/* Air Gap & Tooth */}
      <Box>
        <Typography variant="subtitle2" color="primary" sx={{ mb: 2 }}>
          Air Gap & Tooth
        </Typography>
        
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <Controller
            name="air_gap"
            control={control}
            render={({ field }) => (
              <TextField
                {...field}
                label="Air Gap"
                type="number"
                size="small"
                InputProps={{
                  endAdornment: <InputAdornment position="end">mm</InputAdornment>,
                }}
                onChange={(e) => field.onChange(parseFloat(e.target.value))}
              />
            )}
          />
          
          <Controller
            name="tooth_width"
            control={control}
            render={({ field }) => (
              <TextField
                {...field}
                label="Tooth Width"
                type="number"
                size="small"
                InputProps={{
                  endAdornment: <InputAdornment position="end">mm</InputAdornment>,
                }}
                onChange={(e) => field.onChange(parseFloat(e.target.value))}
              />
            )}
          />
        </Box>
      </Box>
      
      <Divider />
      
      {/* Rotor Section */}
      <Box>
        <Typography variant="subtitle2" color="primary" sx={{ mb: 2 }}>
          Rotor Parameters
        </Typography>
        
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <Controller
            name="magnet_height"
            control={control}
            render={({ field }) => (
              <TextField
                {...field}
                label="Magnet Height"
                type="number"
                size="small"
                InputProps={{
                  endAdornment: <InputAdornment position="end">mm</InputAdornment>,
                }}
                onChange={(e) => field.onChange(parseFloat(e.target.value))}
              />
            )}
          />
          
          <Controller
            name="rotor_house_height"
            control={control}
            render={({ field }) => (
              <TextField
                {...field}
                label="Rotor Housing Thickness"
                type="number"
                size="small"
                InputProps={{
                  endAdornment: <InputAdornment position="end">mm</InputAdornment>,
                }}
                onChange={(e) => field.onChange(parseFloat(e.target.value))}
              />
            )}
          />
        </Box>
      </Box>
      
      {/* Computed Values */}
      <Divider />
      <Box>
        <Typography variant="subtitle2" color="primary" sx={{ mb: 2 }}>
          Computed Values
        </Typography>
        <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1 }}>
          <Typography variant="caption" color="text.secondary">
            Stator Outer R: {geometry.stator_outer_radius?.toFixed(1)} mm
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Stator Inner R: {geometry.stator_inner_radius?.toFixed(1)} mm
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Rotor Outer R: {geometry.rotor_outer_radius?.toFixed(1)} mm
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Rotor Inner R: {geometry.rotor_inner_radius?.toFixed(1)} mm
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Slot Angle: {geometry.angle_slot?.toFixed(2)}°
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Pole Angle: {geometry.angle_pole?.toFixed(2)}°
          </Typography>
        </Box>
      </Box>
    </Box>
  );
};

export default GeometryForm;
