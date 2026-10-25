/**
 * StoredResultsDrawer - under the ORDINARY Configure (a motor with a full passport): the thermal and mechanical results
 * the stores hold for the loaded duty, collapsed by default.  The same blocks the points-limited view shows
 * (StoredPointResults), so a computed point reads the same whichever way Configure was opened.  Shown only when the
 * loaded duty has a stored thermal or mechanical result; read-only.
 */
import React, { useState } from 'react';
import { Box, Button, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import StoredPointResults from './StoredPointResults';
import { PANEL, LABEL } from '../common/ResultTiles';
import type { MotorView } from '../../modules/development/types';

const StoredResultsDrawer: React.FC<{ view: MotorView; duty: string | null }> = ({ view, duty }) => {
  const { t } = useTranslation('development');
  const [open, setOpen] = useState(false);
  const point = view.duties.find((d) => d.duty === duty && (d.thermal || d.mechanical)) ?? null;
  if (!point) return null;
  return (
    <Box sx={{ ...PANEL, flexShrink: 0, maxHeight: '45vh', overflow: 'auto', p: 0.75 }} data-testid="stored-results-drawer">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <Typography sx={LABEL}>{t('points.drawer.title', { duty: point.duty })}</Typography>
        <Button size="small" onClick={() => setOpen((v) => !v)}>{open ? t('points.drawer.hide') : t('points.drawer.show')}</Button>
      </Box>
      {open && <Box sx={{ mt: 0.75 }}><StoredPointResults duty={point} /></Box>}
    </Box>
  );
};

export default StoredResultsDrawer;
