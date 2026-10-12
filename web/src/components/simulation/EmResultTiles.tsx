// The Electromagnetic tab's result tile panel (owner 2026-10-12: «Сделай точно такую же панель
// в Electromagnetic») — Configure's compact tile block, fed from the FEM run summary.  The
// tiles and the rows come from lib/emResultTiles.ts (pure) and render through the shared
// components/common/ResultTiles.tsx, so the two tabs cannot drift apart visually.  Tiles whose
// value the run lacks are not rendered at all (no empty tiles, no zeros).
import React from 'react';
import { Box, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { nsT } from '../../i18n/nsT';
import { LABEL, MetricTile, TileRow, LeadCableTile } from '../common/ResultTiles';
import { emTileRows, emTempLimits, emWarning, type EmTileSpec, type EmTileSummary } from '../../lib/emResultTiles';

const tx = nsT('controller');   // the tile labels and tooltips (EN source, ZH mirror — docs/I18N.md)

const EmResultTiles: React.FC<{
  /** the run summary as the card displays it (the 3-D toggle already applied) */
  summary: EmTileSummary;
  /** copper ρ(T) ratio of the R view (1 = at the solve temperature) */
  rf?: number;
  /** which 3-D factor the torque (so Kt / Km) carries, null for the raw 2-D solve */
  k3dBasis?: '3-D' | '3-D flux' | null;
}> = ({ summary: s, rf, k3dBasis }) => {
  useTranslation('controller');   // subscribe + lazy-load the namespace
  const rows = emTileRows(s, { rf, k3dBasis });
  const warn = emWarning(s);
  const lim = emTempLimits(s);
  const hasTemps = s.coupling?.coil_temp_c != null || s.coupling?.magnet_temp_c != null;
  const renderTile = (t: EmTileSpec) => {
    if (t.kind === 'leadCable') return <LeadCableTile key={t.id} section_mm2={t.value as number} />;
    const tip = [t.tipKey ? tx(t.tipKey, t.tipVars) : '', ...(t.tipExtraKeys ?? []).map((k) => tx(k))]
      .filter((x) => !!x).join(' ');
    return (
      <MetricTile key={t.id} label={t.label ?? tx(t.labelKey as string)} value={t.value} display={t.display}
        unit={t.unit} d={t.d} base={t.value ?? 0} plain absLevel={t.level}
        labelColor={t.labelColor} valueColor={t.valueColor} tip={tip || undefined} />
    );
  };
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
      {/* ONE red line (fixed height: it never moves the grid) — only when the run carries
          temperatures, so the panel cannot jump when the warning appears */}
      {hasTemps && (
        <Typography sx={{ ...LABEL, fontSize: 11, lineHeight: '16px', height: 16, whiteSpace: 'nowrap',
          overflow: 'hidden', textOverflow: 'ellipsis', color: '#f87171', mb: -0.5 }}
          title={warn
            ? [warn.windingOver ? tx('configurePropeller.overWinding', { limit: lim.winding_C, basis: lim.windingBasis }) : '',
              warn.magnetOver ? tx('configurePropeller.overMagnet', { limit: lim.magnet_C }) : '',
              tx('configurePropeller.temperatureLimits', { winding: lim.winding_C, basis: lim.windingBasis, magnet: lim.magnet_C })]
              .filter((x) => !!x).join(' ')
            : undefined}>
          {warn ? `⚠ ${tx('configurePropeller.overheat')}` : ''}
        </Typography>
      )}
      {rows.map((r, i) => <TileRow key={i}>{r.map(renderTile)}</TileRow>)}
    </Box>
  );
};

export default EmResultTiles;
