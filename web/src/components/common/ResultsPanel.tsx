// The motor information panel - ONE component for every place that shows a motor's results (owner 2026-11-01: «take the whole
// information panel from Configure, it must be the same everywhere; a cell that cannot be computed is simply not shown»).
//
// The tile list is Configure's, built by lib/emResultTiles.ts (the same labels, units, tooltips and colours the ordinary Configure
// and the Electromagnetic tab use) and drawn by EmResultTiles; a tile whose value the point does not store is not drawn, and a
// row left empty disappears.  This component adds the ONE caption line - which record the numbers are (the coupled loop with its
// temperatures and drive, or the EM run) - with its tooltip.  Used by the points-limited Configure (PointsConfigure): the
// project motor view and a motor loaded from the Motors tab open the same view.
import React from 'react';
import { Box, Chip, Tooltip } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { nsT } from '../../i18n/nsT';
import EmResultTiles from '../simulation/EmResultTiles';
import { panelCaption, panelCogging, panelMech, panelSummary } from '../../lib/resultsPanel';
import type { StoredPanel } from '../../modules/development/types';

const tx = nsT('controller');   // the tile names and the caption (EN source, ZH mirror - docs/I18N.md)

const ResultsPanel: React.FC<{ panel: StoredPanel }> = ({ panel }) => {
  useTranslation('controller');   // subscribe + lazy-load the namespace
  const { summary, k3dBasis } = panelSummary(panel);
  const caption = panelCaption(panel.source);
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75, minWidth: 0 }} data-testid="results-panel">
      <Tooltip title={tx(`emTiles.${caption.tipKey}`)}>
        <Chip size="small" label={tx(`emTiles.${caption.key}`, caption.vars)} data-testid="em-source-chip"
          sx={{ alignSelf: 'flex-start', height: 20, fontSize: 11, color: 'var(--text-2)', bgcolor: 'var(--panel-2)' }} />
      </Tooltip>
      <EmResultTiles summary={summary} k3dBasis={k3dBasis} mech={panelMech(panel.summary)} cooling={panel.cooling ?? null} cogging={panelCogging(panel)} />
    </Box>
  );
};

export default ResultsPanel;
