/**
 * SavedRunsDialog — browse, load and delete scan results persisted on disk
 * (via /api/optimization/saved).  Opened from the Sweep header.
 */
import React, { useEffect } from 'react';
import {
  Dialog, DialogTitle, DialogContent, IconButton, Box, Typography,
  Table, TableBody, TableCell, TableHead, TableRow, Button, Tooltip,
} from '@mui/material';
import CloseIcon  from '@mui/icons-material/Close';
import DeleteIcon from '@mui/icons-material/DeleteOutline';
import { useMotorStore } from '../../stores/motorStore';

const SavedRunsDialog: React.FC<{ open: boolean; onClose: () => void }> = ({ open, onClose }) => {
  const savedRuns    = useMotorStore(s => s.savedRuns);
  const refreshSaved = useMotorStore(s => s.refreshSaved);
  const loadSaved    = useMotorStore(s => s.loadSaved);
  const deleteSaved  = useMotorStore(s => s.deleteSaved);

  useEffect(() => { if (open) refreshSaved(); }, [open, refreshSaved]);

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth
      PaperProps={{ sx: { bgcolor: '#0f172a', backgroundImage: 'none' } }}>
      <DialogTitle sx={{ fontSize: 15, display: 'flex', alignItems: 'center' }}>
        Saved scan results
        <Typography component="span" variant="caption" sx={{ ml: 1, color: 'text.disabled' }}>
          (stored on disk · optimization_saves/)
        </Typography>
        <Box sx={{ flex: 1 }} />
        <IconButton size="small" onClick={onClose}><CloseIcon fontSize="small" /></IconButton>
      </DialogTitle>
      <DialogContent>
        {savedRuns.length === 0 ? (
          <Typography variant="body2" color="text.disabled" sx={{ py: 3, textAlign: 'center' }}>
            No saved results yet. Run a scan and click <b>Save</b>.
          </Typography>
        ) : (
          <Table size="small" sx={{ '& td, & th': { fontSize: 12, borderColor: '#1e293b' } }}>
            <TableHead>
              <TableRow>
                <TableCell>Name</TableCell>
                <TableCell>Date</TableCell>
                <TableCell align="right">geom</TableCell>
                <TableCell align="right">built</TableCell>
                <TableCell align="right">front</TableCell>
                <TableCell align="right">steps</TableCell>
                <TableCell>variables</TableCell>
                <TableCell align="right" />
              </TableRow>
            </TableHead>
            <TableBody>
              {savedRuns.map(s => (
                <TableRow key={s.id} hover>
                  <TableCell sx={{ fontWeight: 600 }}>{s.name}</TableCell>
                  <TableCell sx={{ color: 'text.secondary' }}>{(s.created_at || '').replace('T', ' ')}</TableCell>
                  <TableCell align="right">{s.n_geometries ?? '–'}</TableCell>
                  <TableCell align="right">{s.n_built ?? '–'}</TableCell>
                  <TableCell align="right">{s.n_front ?? '–'}</TableCell>
                  <TableCell align="right">{s.steps_per_period ?? '–'}</TableCell>
                  <TableCell sx={{ color: 'text.secondary', maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {(s.variables || []).join(', ')}
                  </TableCell>
                  <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                    <Button size="small" variant="outlined" sx={{ fontSize: 10, py: 0, mr: 0.5 }}
                      onClick={() => { loadSaved(s.id); onClose(); }}>Load</Button>
                    <Tooltip title="Delete">
                      <IconButton size="small" color="error"
                        onClick={() => { if (window.confirm(`Delete "${s.name}"?`)) deleteSaved(s.id); }}>
                        <DeleteIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </DialogContent>
    </Dialog>
  );
};

export default SavedRunsDialog;
