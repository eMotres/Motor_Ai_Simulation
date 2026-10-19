// DutyGeometryDialog — the "duty saved on a different geometry" choice, as a
// real modal (incident 2026-09-29: a window.confirm() with unrounded floats
// and an ambiguous OK/Cancel had the owner pick "apply duty geometry" by
// mistake, then hit a second, confusing failure because the die was locked).
//
// Mount ONCE (in ActiveFamilyStrip, always on screen once signed in); the
// imperative half lives in lib/dutyGeometryDialogService — lib/dutyApply.ts
// calls askDutyGeometryChoice() and awaits whatever button gets pressed here.
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Dialog, DialogTitle, DialogContent, DialogActions, Button, Box,
  Table, TableBody, TableCell, TableHead, TableRow, Tooltip, Typography,
} from '@mui/material';
import { nsT } from '../../i18n/nsT';
import {
  formatDutyGeometryValue, dutyGeometryApplyDisabledCode, dutyGeometryButtonPlan,
} from '../../lib/dutyGeometryDiff';
import {
  useDutyGeometryDialogStore, resolveDutyGeometryChoice,
} from '../../lib/dutyGeometryDialogService';

const tx = nsT('motors');

const SCOPE_KEY: Record<string, string> = {
  identity: 'dutyGeoScopeIdentity', die: 'dutyGeoScopeDie',
  winding: 'dutyGeoScopeWinding', free: 'dutyGeoScopeFree',
};

const DutyGeometryDialog: React.FC = () => {
  useTranslation('motors'); // re-render on language change; lazy-loads the namespace
  const req = useDutyGeometryDialogStore((s) => s.request);
  const open = !!req;
  const diffs = req?.diffs ?? [];
  const disabledCode = req ? dutyGeometryApplyDisabledCode(diffs, req.dieLocked) : null;
  const disabledReason = disabledCode === 'identity' ? tx('dutyGeoReasonIdentity')
    : disabledCode === 'locked' ? tx('dutyGeoReasonLocked') : null;
  const plan = dutyGeometryButtonPlan(diffs, req?.configCount);
  const applyText = tx(plan.applyKey, {
    values: plan.applyValues, n: plan.configCount ?? 0, config: req?.config ?? '',
  });
  const keepText = tx('dutyGeoKeepDie', { values: plan.keepValues });

  // Second step of "change the die back": the consequence is repeated and
  // only an explicit "Yes" writes anything.  The step is bound to the request
  // it was asked for, so a new request always opens on the first step.
  const [confirmFor, setConfirmFor] = useState<typeof req>(null);
  const confirming = !!req && confirmFor === req;
  const setConfirming = (on: boolean) => setConfirmFor(on ? req : null);

  return (
    <Dialog open={open} onClose={() => resolveDutyGeometryChoice(null)}
      maxWidth="sm" fullWidth
      PaperProps={{ sx: { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)',
                          borderRadius: 2 } }}>
      <DialogTitle sx={{ color: 'var(--text-0)', fontSize: '0.95rem', fontWeight: 700 }}>
        {confirming ? tx('dutyGeoConfirmTitle') : tx('dutyGeoTitle')}
      </DialogTitle>
      {confirming ? (
        <DialogContent data-testid="duty-geometry-confirm">
          <Typography sx={{ fontSize: 13, color: '#f59e0b', mb: 1 }}>{applyText}</Typography>
          <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>
            {plan.applyKey === 'dutyGeoApplyConfig'
              ? tx('dutyGeoConfirmNoteConfig') : tx('dutyGeoConfirmNote')}
          </Typography>
        </DialogContent>
      ) : (
        <DialogContent>
          <Typography sx={{ fontSize: 12.5, color: 'var(--text-2)', mb: 1 }}>
            {tx('dutyGeoWhy')}
          </Typography>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell sx={{ fontSize: 11, color: 'var(--text-3)' }}>{tx('dutyGeoColKey')}</TableCell>
                <TableCell sx={{ fontSize: 11, color: 'var(--text-3)' }}>{tx('dutyGeoColScope')}</TableCell>
                <TableCell sx={{ fontSize: 11, color: 'var(--text-3)' }} align="right">{tx('dutyGeoColDie')}</TableCell>
                <TableCell sx={{ fontSize: 11, color: 'var(--text-3)' }} align="right">{tx('dutyGeoColDuty')}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {diffs.map((d) => (
                <TableRow key={d.key}>
                  <TableCell sx={{ fontSize: 12, fontFamily: 'monospace' }}>{d.key}</TableCell>
                  <TableCell sx={{ fontSize: 11.5, color: 'var(--text-3)' }}>
                    {SCOPE_KEY[d.scope] ? tx(SCOPE_KEY[d.scope]) : d.scope}
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
              {tx('dutyGeoApplyUnavailable', { reason: disabledReason })}
            </Typography>
          )}
        </DialogContent>
      )}
      {confirming ? (
        <DialogActions sx={{ flexWrap: 'wrap', gap: 0.5 }}>
          <Button onClick={() => setConfirming(false)}
            sx={{ textTransform: 'none', color: 'var(--text-2)' }}>
            {tx('dutyGeoConfirmBack')}
          </Button>
          <Box sx={{ flex: 1 }} />
          <Button variant="contained" color="warning"
            onClick={() => resolveDutyGeometryChoice('apply_duty')}
            sx={{ textTransform: 'none' }}>
            {plan.applyKey === 'dutyGeoApplyConfig'
              ? tx('dutyGeoConfirmYesConfig') : tx('dutyGeoConfirmYesDie')}
          </Button>
        </DialogActions>
      ) : (
        <DialogActions sx={{ flexWrap: 'wrap', gap: 0.5 }}>
          <Button onClick={() => resolveDutyGeometryChoice(null)}
            sx={{ textTransform: 'none', color: 'var(--text-2)' }}>
            {tx('dutyGeoCancel')}
          </Button>
          <Box sx={{ flex: 1 }} />
          <Button variant="outlined" onClick={() => resolveDutyGeometryChoice('keep_die')}
            sx={{ textTransform: 'none', textAlign: 'left' }}>
            {keepText}
          </Button>
          <Tooltip title={disabledReason ?? ''}>
            <span>
              <Button variant="contained" disabled={!!disabledReason}
                onClick={() => setConfirming(true)}
                sx={{ textTransform: 'none', textAlign: 'left' }}>
                {applyText}
              </Button>
            </span>
          </Tooltip>
        </DialogActions>
      )}
    </Dialog>
  );
};

export default DutyGeometryDialog;
