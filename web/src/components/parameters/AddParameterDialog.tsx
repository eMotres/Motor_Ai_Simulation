import React, { useState } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Box,
  MenuItem,
  ToggleButtonGroup,
  ToggleButton,
  Typography,
  Alert,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import { useMotorStore } from '../../stores/motorStore';

const GROUPS = [
  { id: 'stator',      label: 'Stator Parameters' },
  { id: 'segmentation', label: 'Segmentation' },
  { id: 'slot',        label: 'Slot Details' },
  { id: 'winding',     label: 'Winding' },
  { id: 'rotor',       label: 'Rotor Parameters' },
  { id: 'shaft',       label: 'Shaft' },
  { id: 'custom',      label: 'Custom' },
];

interface Props {
  open: boolean;
  onClose: () => void;
}

interface FormState {
  name: string;
  label: string;
  unit: string;
  type: 'float' | 'int';
  group: string;
  default_value: string;
  min: string;
  max: string;
  step: string;
  description: string;
}

const DEFAULTS: FormState = {
  name: '',
  label: '',
  unit: '',
  type: 'float',
  group: 'custom',
  default_value: '0',
  min: '0',
  max: '100',
  step: '0.1',
  description: '',
};

const AddParameterDialog: React.FC<Props> = ({ open, onClose }) => {
  const { fetchSchemaFromApi, connectedToApi } = useMotorStore();
  const [form, setForm] = useState<FormState>(DEFAULTS);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const set = (field: keyof FormState, value: string) =>
    setForm(prev => ({ ...prev, [field]: value }));

  const nameFromLabel = (label: string) =>
    label.trim().toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, '');

  const handleLabelChange = (value: string) => {
    setForm(prev => ({
      ...prev,
      label: value,
      name: prev.name === nameFromLabel(prev.label) ? nameFromLabel(value) : prev.name,
    }));
  };

  const validate = () => {
    if (!form.name) return 'Name is required';
    if (!/^[a-z][a-z0-9_]*$/.test(form.name)) return 'Name must be snake_case (lowercase letters, digits, underscores)';
    if (!form.label) return 'Label is required';
    if (isNaN(parseFloat(form.default_value))) return 'Default value must be a number';
    if (isNaN(parseFloat(form.min))) return 'Min must be a number';
    if (isNaN(parseFloat(form.max))) return 'Max must be a number';
    if (parseFloat(form.min) >= parseFloat(form.max)) return 'Min must be less than Max';
    return '';
  };

  const handleSubmit = async () => {
    const err = validate();
    if (err) { setError(err); return; }
    setError('');
    setLoading(true);
    try {
      const res = await fetch((import.meta.env.VITE_API_URL ?? 'http://localhost:8000') + '/api/geometry/parameter', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: form.name,
          label: form.label,
          unit: form.unit,
          type: form.type,
          group: form.group,
          default_value: parseFloat(form.default_value),
          min: parseFloat(form.min),
          max: parseFloat(form.max),
          step: parseFloat(form.step),
          description: form.description,
        }),
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || 'Failed to add parameter');
      }
      await fetchSchemaFromApi();
      setForm(DEFAULTS);
      onClose();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const handleClose = () => {
    setForm(DEFAULTS);
    setError('');
    onClose();
  };

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>Add New Parameter</DialogTitle>
      <DialogContent>
        {!connectedToApi && (
          <Alert severity="warning" sx={{ mb: 2 }}>
            Not connected to API. Parameter will not be saved.
          </Alert>
        )}
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 0.5 }}>

          {/* Label + auto-name */}
          <Box sx={{ display: 'flex', gap: 1.5 }}>
            <TextField
              label="Label"
              value={form.label}
              onChange={e => handleLabelChange(e.target.value)}
              placeholder="Pole Arc Ratio"
              fullWidth
              required
            />
            <TextField
              label="Name (snake_case)"
              value={form.name}
              onChange={e => set('name', e.target.value.toLowerCase().replace(/\s/g, '_'))}
              placeholder="pole_arc_ratio"
              fullWidth
              required
              helperText="Used in code and YAML"
            />
          </Box>

          {/* Unit + Group */}
          <Box sx={{ display: 'flex', gap: 1.5 }}>
            <TextField
              label="Unit"
              value={form.unit}
              onChange={e => set('unit', e.target.value)}
              placeholder="mm, °, —"
              sx={{ flex: 1 }}
            />
            <TextField
              select
              label="Group"
              value={form.group}
              onChange={e => set('group', e.target.value)}
              sx={{ flex: 2 }}
            >
              {GROUPS.map(g => (
                <MenuItem key={g.id} value={g.id}>{g.label}</MenuItem>
              ))}
            </TextField>
          </Box>

          {/* Type */}
          <Box>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
              Type
            </Typography>
            <ToggleButtonGroup
              exclusive
              value={form.type}
              onChange={(_, v) => v && set('type', v)}
              size="small"
            >
              <ToggleButton value="float">Float</ToggleButton>
              <ToggleButton value="int">Integer</ToggleButton>
            </ToggleButtonGroup>
          </Box>

          {/* Default / Min / Max / Step */}
          <Box sx={{ display: 'flex', gap: 1.5 }}>
            <TextField
              label="Default"
              type="number"
              value={form.default_value}
              onChange={e => set('default_value', e.target.value)}
              sx={{ flex: 1 }}
              required
            />
            <TextField
              label="Min"
              type="number"
              value={form.min}
              onChange={e => set('min', e.target.value)}
              sx={{ flex: 1 }}
            />
            <TextField
              label="Max"
              type="number"
              value={form.max}
              onChange={e => set('max', e.target.value)}
              sx={{ flex: 1 }}
            />
            <TextField
              label="Step"
              type="number"
              value={form.step}
              onChange={e => set('step', e.target.value)}
              sx={{ flex: 1 }}
            />
          </Box>

          {/* Description */}
          <TextField
            label="Description"
            value={form.description}
            onChange={e => set('description', e.target.value)}
            placeholder="Brief explanation of this parameter"
            multiline
            rows={2}
            fullWidth
          />
        </Box>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={handleClose} disabled={loading}>Cancel</Button>
        <Button
          variant="contained"
          onClick={handleSubmit}
          disabled={loading || !connectedToApi}
          startIcon={<AddIcon />}
        >
          {loading ? 'Saving...' : 'Add Parameter'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default AddParameterDialog;
