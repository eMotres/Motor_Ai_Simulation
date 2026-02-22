import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  TextField,
  Typography,
  Box,
  Divider,
  InputAdornment,
  CircularProgress,
  Alert,
  Button,
  IconButton,
  Tooltip,
  Snackbar,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import { useMotorStore } from '../../stores/motorStore';

/**
 * Dynamic Geometry Form
 * 
 * This form is dynamically generated from the parameter schema
 * fetched from the Python API. The schema is defined in 
 * config/motor_config.yaml, making it the single source of truth.
 * 
 * To add a new parameter:
 * 1. Add it to motor_config.yaml geometry section
 * 2. Add metadata to geometry_schema section
 * 3. The form will automatically show the new parameter
 */
const GeometryForm: React.FC = () => {
  const { 
    geometry, 
    parameterSchema, 
    parameterGroups, 
    isLoading, 
    error,
    connectedToApi,
    fetchSchemaFromApi,
    fetchGeometryFromApi,
    updateGeometryViaApi,
    updateGeometry,
  } = useMotorStore();
  
  // Debounce timer ref
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  
  // Reload state
  const [isReloading, setIsReloading] = useState(false);
  const [showReloadSuccess, setShowReloadSuccess] = useState(false);
  
  // Fetch schema and geometry on mount
  useEffect(() => {
    if (connectedToApi) {
      fetchSchemaFromApi();
      fetchGeometryFromApi();
    }
  }, [connectedToApi, fetchSchemaFromApi, fetchGeometryFromApi]);
  
  // Handle reload schema from API
  const handleReloadSchema = async () => {
    setIsReloading(true);
    try {
      await fetchSchemaFromApi();
      await fetchGeometryFromApi();
      setShowReloadSuccess(true);
    } finally {
      setIsReloading(false);
    }
  };
  
  // Debounced update function
  const debouncedUpdate = useCallback((name: string, value: number) => {
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
    }
    
    debounceTimerRef.current = setTimeout(() => {
      const params = { [name]: value };
      if (connectedToApi) {
        // Send changes to Python API
        updateGeometryViaApi(params);
      } else {
        // Local update only
        updateGeometry(params);
      }
    }, 300); // 300ms debounce
  }, [connectedToApi, updateGeometryViaApi, updateGeometry]);
  
  // Handle field change
  const handleChange = (name: string, type: 'float' | 'int') => (
    event: React.ChangeEvent<HTMLInputElement>
  ) => {
    const value = type === 'int' 
      ? parseInt(event.target.value, 10) 
      : parseFloat(event.target.value);
    
    if (!isNaN(value)) {
      debouncedUpdate(name, value);
    }
  };
  
  // Cleanup debounce timer
  useEffect(() => {
    return () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
    };
  }, []);
  
  // Show loading state
  if (isLoading && parameterSchema.length === 0) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
        <CircularProgress />
      </Box>
    );
  }
  
  // Show error state
  if (error) {
    return (
      <Alert severity="error" sx={{ m: 2 }}>
        {error}
      </Alert>
    );
  }
  
  // Show message if not connected to API
  if (!connectedToApi) {
    return (
      <Alert severity="warning" sx={{ m: 2 }}>
        Not connected to API. Start the Python server to edit geometry parameters.
      </Alert>
    );
  }
  
  // Group parameters by group
  const groupedParams = parameterGroups.map(group => ({
    ...group,
    parameters: parameterSchema.filter(p => p.group === group.id),
  }));
  
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {/* Reload Schema Button */}
      <Box sx={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 1 }}>
        <Typography variant="caption" color="text.secondary">
          {parameterSchema.length} parameters loaded
        </Typography>
        <Tooltip title="Reload schema from API (after editing motor_config.yaml)">
          <IconButton 
            size="small" 
            onClick={handleReloadSchema}
            disabled={isReloading || !connectedToApi}
            color="primary"
          >
            {isReloading ? <CircularProgress size={20} /> : <RefreshIcon />}
          </IconButton>
        </Tooltip>
      </Box>
      
      {groupedParams.map((group, groupIndex) => (
        <React.Fragment key={group.id}>
          <Box>
            <Typography variant="subtitle2" color="primary" sx={{ mb: 2 }}>
              {group.label}
            </Typography>
            
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              {group.parameters.map(param => (
                <TextField
                  key={param.name}
                  label={param.label}
                  type="number"
                  size="small"
                  value={geometry[param.name] ?? 0}
                  onChange={handleChange(param.name, param.type as 'float' | 'int')}
                  inputProps={{
                    min: param.min,
                    max: param.max,
                    step: param.step,
                  }}
                  InputProps={{
                    endAdornment: param.unit ? (
                      <InputAdornment position="end">{param.unit}</InputAdornment>
                    ) : undefined,
                  }}
                  helperText={param.description}
                  disabled={isLoading}
                />
              ))}
            </Box>
          </Box>
          
          {groupIndex < groupedParams.length - 1 && <Divider />}
        </React.Fragment>
      ))}
      
      {/* Success notification */}
      <Snackbar
        open={showReloadSuccess}
        autoHideDuration={3000}
        onClose={() => setShowReloadSuccess(false)}
        message="Schema reloaded from API"
      />
    </Box>
  );
};

export default GeometryForm;
