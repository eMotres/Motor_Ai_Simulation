/** Dialog to inspect and edit block parameters in a user-composed graph (stage 2). */
import { useEffect, useState } from 'react';
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import AddIcon from '@mui/icons-material/Add';

import { typeColor } from '../../lib/systemGraph';
import type { ComposeNodeData } from './systemComposeTypes';

interface BlockParamsDialogProps {
  open: boolean;
  node: ComposeNodeData | null;
  onClose: () => void;
  onSave: (nodeId: string, newParams: Record<string, unknown>) => void;
  onDeleteNode?: (nodeId: string) => void;
}

export default function BlockParamsDialog({
  open,
  node,
  onClose,
  onSave,
  onDeleteNode,
}: BlockParamsDialogProps) {
  const [params, setParams] = useState<Record<string, string>>({});
  const [newKey, setNewKey] = useState('');
  const [newVal, setNewVal] = useState('');

  useEffect(() => {
    if (node) {
      const p: Record<string, string> = {};
      for (const [k, v] of Object.entries(node.parameters || {})) {
        p[k] = v != null ? String(v) : '';
      }
      setParams(p);
      setNewKey('');
      setNewVal('');
    }
  }, [node]);

  if (!node) return null;

  const handleSave = () => {
    const parsed: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(params)) {
      if (!k.trim()) continue;
      const num = Number(v);
      if (!isNaN(num) && v.trim() !== '') {
        parsed[k.trim()] = num;
      } else if (v.toLowerCase() === 'true') {
        parsed[k.trim()] = true;
      } else if (v.toLowerCase() === 'false') {
        parsed[k.trim()] = false;
      } else {
        parsed[k.trim()] = v;
      }
    }
    onSave(node.id, parsed);
    onClose();
  };

  const handleAddParam = () => {
    if (!newKey.trim()) return;
    setParams((prev) => ({ ...prev, [newKey.trim()]: newVal }));
    setNewKey('');
    setNewVal('');
  };

  const handleRemoveParam = (k: string) => {
    setParams((prev) => {
      const copy = { ...prev };
      delete copy[k];
      return copy;
    });
  };

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle sx={{ pb: 1 }}>
        <Typography variant="h6" sx={{ fontSize: 16, fontWeight: 700 }}>
          {node.title} — Parameters
        </Typography>
        <Typography variant="caption" sx={{ color: 'text.secondary' }}>
          Capability: {node.capability}
        </Typography>
      </DialogTitle>

      <DialogContent dividers sx={{ py: 1.5 }}>
        {/* Ports summary */}
        <Box sx={{ mb: 2 }}>
          <Typography variant="caption" sx={{ fontWeight: 700, color: 'text.secondary', textTransform: 'uppercase' }}>
            Contracts
          </Typography>
          <Stack direction="row" spacing={1} sx={{ mt: 0.5, flexWrap: 'wrap', gap: 0.8 }}>
            {node.inputs.map((inp) => (
              <Box
                key={inp.port}
                sx={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  px: 0.8,
                  py: 0.2,
                  borderRadius: 4,
                  bgcolor: `${typeColor(inp.type)}14`,
                  border: `1px solid ${typeColor(inp.type)}`,
                  fontSize: 11,
                }}
              >
                In: {inp.port} ({inp.type})
              </Box>
            ))}
            {node.outputs.map((out) => (
              <Box
                key={out.port}
                sx={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  px: 0.8,
                  py: 0.2,
                  borderRadius: 4,
                  bgcolor: `${typeColor(out.type)}14`,
                  border: `1px solid ${typeColor(out.type)}`,
                  fontSize: 11,
                }}
              >
                Out: {out.port} ({out.type})
              </Box>
            ))}
          </Stack>
        </Box>

        <Divider sx={{ my: 1.5 }} />

        {/* Form fields */}
        <Typography variant="caption" sx={{ fontWeight: 700, color: 'text.secondary', textTransform: 'uppercase', mb: 1, display: 'block' }}>
          Block Parameters
        </Typography>

        {Object.keys(params).length === 0 ? (
          <Typography variant="body2" sx={{ color: 'text.secondary', fontStyle: 'italic', my: 1 }}>
            No parameters set for this block.
          </Typography>
        ) : (
          <Stack spacing={1.2} sx={{ my: 1 }}>
            {Object.entries(params).map(([k, v]) => (
              <Stack key={k} direction="row" spacing={1} alignItems="center">
                <TextField
                  size="small"
                  label={k}
                  value={v}
                  onChange={(e) => setParams((prev) => ({ ...prev, [k]: e.target.value }))}
                  sx={{ flex: 1, '& .MuiInputBase-root': { fontSize: 13, height: 36 } }}
                />
                <IconButton size="small" onClick={() => handleRemoveParam(k)} sx={{ color: 'text.secondary', '&:hover': { color: 'error.main' } }}>
                  <DeleteOutlineIcon sx={{ fontSize: 18 }} />
                </IconButton>
              </Stack>
            ))}
          </Stack>
        )}

        {/* Add custom parameter */}
        <Box sx={{ mt: 2, p: 1.2, bgcolor: '#f8fafc', borderRadius: 1, border: '1px dashed #cbd5e1' }}>
          <Typography variant="caption" sx={{ fontWeight: 600, color: '#475569', display: 'block', mb: 0.8 }}>
            Add Parameter
          </Typography>
          <Stack direction="row" spacing={1} alignItems="center">
            <TextField
              size="small"
              placeholder="Name"
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
              sx={{ flex: 1, '& .MuiInputBase-root': { fontSize: 12, height: 32 } }}
            />
            <TextField
              size="small"
              placeholder="Value"
              value={newVal}
              onChange={(e) => setNewVal(e.target.value)}
              sx={{ flex: 1, '& .MuiInputBase-root': { fontSize: 12, height: 32 } }}
            />
            <Button size="small" variant="outlined" startIcon={<AddIcon />} onClick={handleAddParam} disabled={!newKey.trim()} sx={{ height: 32, fontSize: 12 }}>
              Add
            </Button>
          </Stack>
        </Box>
      </DialogContent>

      <DialogActions sx={{ px: 2, py: 1.2, justifyContent: 'space-between' }}>
        {onDeleteNode ? (
          <Button
            color="error"
            startIcon={<DeleteOutlineIcon />}
            onClick={() => {
              onDeleteNode(node.id);
              onClose();
            }}
          >
            Delete Block
          </Button>
        ) : <Box />}
        <Stack direction="row" spacing={1}>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="contained" onClick={handleSave}>
            Save
          </Button>
        </Stack>
      </DialogActions>
    </Dialog>
  );
}
