// DutyGeometryDialog — the "duty saved on a different geometry" choice, as a
// real modal (incident 2026-09-29: a window.confirm() with unrounded floats
// and an ambiguous OK/Cancel had the owner pick "apply duty geometry" by
// mistake, then hit a second, confusing failure because the die was locked).
//
// Mount ONCE (in ActiveFamilyStrip, always on screen once signed in); the
// imperative half lives in lib/dutyGeometryDialogService — lib/dutyApply.ts
// calls askDutyGeometryChoice() and awaits whatever button gets pressed here.
import React from 'react';
import {
  Dialog, DialogTitle, DialogContent, DialogActions, Button, Box,
  Table, TableBody, TableCell, TableHead, TableRow, Tooltip, Typography,
} from '@mui/material';
import {
  formatDutyGeometryValue, dutyGeometryApplyDisabledReason,
} from '../../lib/dutyGeometryDiff';
import {
  useDutyGeometryDialogStore, resolveDutyGeometryChoice,
} from '../../lib/dutyGeometryDialogService';

const scopeLabel: Record<string, string> = {
  identity: 'lamination', die: 'die', winding: 'winding', free: 'stack length',
};

const DutyGeometryDialog: React.FC = () => {
  const req = useDutyGeometryDialogStore((s) => s.request);
  const open = !!req;
  const diffs = req?.diffs ?? [];
  const disabledReason = req ? dutyGeometryApplyDisabledReason(diffs, req.dieLocked) : null;

  return (
    <Dialog open={open} onClose={() => resolveDutyGeometryChoice(null)}
      maxWidth="sm" fullWidth
      PaperProps={{ sx: { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)',
                          borderRadius: 2 } }}>
      <DialogTitle sx={{ color: 'var(--text-0)', fontSize: '0.95rem', fontWeight: 700 }}>
        This duty was saved on a different geometry
      </DialogTitle>
      <DialogContent>
        <Typography sx={{ fontSize: 12.5, color: 'var(--text-2)', mb: 1 }}>
          This duty&apos;s results were computed on a slightly different
          lamination than the die now has. Choose which one to load with.
        </Typography>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell sx={{ fontSize: 11, color: 'var(--text-3)' }}>key</TableCell>
              <TableCell sx={{ fontSize: 11, color: 'var(--text-3)' }}>scope</TableCell>
              <TableCell sx={{ fontSize: 11, color: 'var(--text-3)' }} align="right">die (now)</TableCell>
              <TableCell sx={{ fontSize: 11, color: 'var(--text-3)' }} align="right">duty (saved)</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {diffs.map((d) => (
              <TableRow key={d.key}>
                <TableCell sx={{ fontSize: 12, fontFamily: 'monospace' }}>{d.key}</TableCell>
                <TableCell sx={{ fontSize: 11.5, color: 'var(--text-3)' }}>
                  {scopeLabel[d.scope] ?? d.scope}
                </TableCell>
                <TableCell sx={{ fontSize: 12 }} align="right">
                  {formatDutyGeometryValue(d.key, d.live)}
                </TableCell>
                <TableCell sx={{ fontSize: 12 }} align="right">
                  {formatDutyGeometryValue(d.key, d.duty)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {disabledReason && (
          <Typography sx={{ fontSize: 11.5, color: '#f59e0b', mt: 1 }}>
            Applying the duty geometry is unavailable: {disabledReason}.
          </Typography>
        )}
      </DialogContent>
      <DialogActions sx={{ flexWrap: 'wrap', gap: 0.5 }}>
        <Button onClick={() => resolveDutyGeometryChoice(null)}
          sx={{ textTransform: 'none', color: 'var(--text-2)' }}>
          Cancel
        </Button>
        <Box sx={{ flex: 1 }} />
        <Button variant="outlined" onClick={() => resolveDutyGeometryChoice('keep_die')}
          sx={{ textTransform: 'none' }}>
          Load with die geometry (recommended, nothing is written)
        </Button>
        <Tooltip title={disabledReason ?? ''}>
          <span>
            <Button variant="contained" disabled={!!disabledReason}
              onClick={() => resolveDutyGeometryChoice('apply_duty')}
              sx={{ textTransform: 'none' }}>
              Apply duty geometry to the die/configuration
            </Button>
          </span>
        </Tooltip>
      </DialogActions>
    </Dialog>
  );
};

export default DutyGeometryDialog;
