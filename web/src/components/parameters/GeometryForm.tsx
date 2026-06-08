import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  TextField,
  Typography,
  Box,
  Divider,
  CircularProgress,
  Alert,
  Button,
  IconButton,
  Tooltip,
  Snackbar,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import { useMotorStore } from '../../stores/motorStore';

/**
 * Free-typing numeric field.
 *
 * The previous geometry inputs were controlled `type="number"` fields bound
 * straight to the store, parsing + debouncing on every keystroke.  Every
 * commit re-rendered and snapped the visible text back to the parsed number,
 * so typing a decimal ("12." → 12) or clearing the field to retype was fought
 * by React.  Here we keep a LOCAL draft string: you type freely (decimals,
 * comma, empty, intermediate values), and the value is committed to the store
 * only on Enter or blur — then clamped to [min, max].  Focus selects the text
 * so you can immediately overwrite it, like a normal spreadsheet cell.
 */
interface NumberFieldProps {
  label: string;
  value: number;
  type: 'float' | 'int';
  min?: number;
  max?: number;
  step?: number;
  helperText?: string;
  disabled?: boolean;
  onCommit: (v: number) => void;
}

const NumberField: React.FC<NumberFieldProps> = ({
  label, value, type, min, max, step, helperText, disabled, onCommit,
}) => {
  const [draft, setDraft] = useState<string>('');
  const [focused, setFocused] = useState(false);

  const fmt = (v: number) =>
    v === undefined || v === null || Number.isNaN(v) ? '' : String(v);

  // Resync from the store ONLY while the user is not editing this field, so an
  // external change (preset applied, optimization result) updates the display
  // but in-progress typing is never clobbered.
  useEffect(() => {
    if (!focused) setDraft(fmt(value));
  }, [value, focused]);

  const parse = (s: string) => {
    const raw = s.trim().replace(',', '.');           // accept comma decimal
    return type === 'int' ? parseInt(raw, 10) : parseFloat(raw);
  };

  const commit = () => {
    let n = parse(draft);
    if (Number.isNaN(n)) { setDraft(fmt(value)); return; }   // revert if blank/garbage
    if (typeof min === 'number' && n < min) n = min;          // clamp
    if (typeof max === 'number' && n > max) n = max;
    if (type === 'int') n = Math.round(n);
    setDraft(fmt(n));
    if (n !== value) onCommit(n);
  };

  const parsed = parse(draft);
  const invalid = draft.trim() !== '' && (
    Number.isNaN(parsed) ||
    (typeof min === 'number' && parsed < min) ||
    (typeof max === 'number' && parsed > max)
  );

  return (
    <TextField
      label={label}
      size="small"
      fullWidth
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onFocus={(e) => { setFocused(true); e.target.select(); }}
      onBlur={() => { setFocused(false); commit(); }}
      onKeyDown={(e) => {
        if (e.key === 'Enter') { (e.target as HTMLInputElement).blur(); }
        else if (e.key === 'Escape') {
          setDraft(fmt(value));
          (e.target as HTMLInputElement).blur();
        }
      }}
      inputProps={{ inputMode: 'decimal', step }}
      error={invalid}
      helperText={
        invalid
          ? `Allowed: ${min ?? '−∞'} … ${max ?? '∞'}`
          : helperText
      }
      disabled={disabled}
    />
  );
};

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
  const debouncedUpdate = useCallback((name: string, value: number | string) => {
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
  
  // Handle field change for string types with options
  const handleStringChange = (name: string) => (
    event: React.ChangeEvent<{ value: unknown }>
  ) => {
    const value = event.target.value as string;
    debouncedUpdate(name, value);
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
    parameters: parameterSchema.filter(p => p.group === group.id && !p.hidden),
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
                param.options && param.options.length > 0 ? (
                  // Render Select for string types with predefined options
                  <FormControl key={param.name} size="small" fullWidth>
                    <InputLabel>{param.label}</InputLabel>
                    <Select
                      value={geometry[param.name] ?? param.options[0]}
                      label={param.label}
                      onChange={handleStringChange(param.name)}
                      disabled={isLoading}
                    >
                      {param.options.map((option) => (
                        <MenuItem key={option} value={option}>
                          {option}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                ) : (
                  // Free-typing numeric field (local draft, commit on Enter/blur)
                  <NumberField
                    key={param.name}
                    label={param.unit ? `${param.label} (${param.unit})` : param.label}
                    value={(geometry[param.name] ?? 0) as number}
                    type={param.type as 'float' | 'int'}
                    min={param.min}
                    max={param.max}
                    step={param.step}
                    helperText={param.description || undefined}
                    disabled={isLoading}
                    onCommit={(v) => {
                      if (connectedToApi) updateGeometryViaApi({ [param.name]: v });
                      else updateGeometry({ [param.name]: v });
                    }}
                  />
                )
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
