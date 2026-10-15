// The Geometry tab with the Active | Full switch (owner 2026-10-15).
//   Active  the tab as it was: the EM cross-section parameter table and the 2-D viewer (passed in as `active`).
//   Full    the full construction: the CAD model, THE part tree, the editable construction parameters.
// The switch is the one of the Materials tab (one shared state); the Files menu next to it changes with the scope.
import React, { useMemo, useState } from 'react';
import { Box, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { nsT } from '../../i18n/nsT';
import ConstructionScopeSwitch, { useConstructionScope } from '../common/ConstructionScopeSwitch';
import NdaDialog from '../model3d/NdaDialog';
import VerificationNotice from '../model3d/VerificationNotice';
import { lockedKind } from '../../lib/verification';
import FilesMenu from './FilesMenu';
import GeometryFullPanel from './GeometryFullPanel';
import { useCadParams, useModelList, useOpenMotor } from './useConstruction';
import { modelKey } from '../../lib/model3d';

const tx = nsT('geometry');

const GeometryTab: React.FC<{ active: React.ReactNode }> = ({ active }) => {
  useTranslation('geometry');
  const [scope] = useConstructionScope();
  const openMotor = useOpenMotor();
  const die = openMotor?.die ?? null;
  const config = openMotor?.config ?? null;
  const [refresh, setRefresh] = useState(0);
  const { models, canDownload, access } = useModelList(refresh);
  const cad = useCadParams(die, config, scope === 'full', () => setRefresh((n) => n + 1));
  const [ndaOpen, setNdaOpen] = useState(false);
  const [noticeOpen, setNoticeOpen] = useState(false);
  const model = useMemo(
    () => models?.find((m) => !!die && modelKey(m) === modelKey({ die, config: config ?? '' })) ?? null, [models, die, config]);

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }} data-testid="geometry-tab">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, px: 1.5, py: 0.5, flexShrink: 0, borderBottom: '1px solid var(--line-soft)' }}>
        <ConstructionScopeSwitch testIdPrefix="geometry-scope" />
        <FilesMenu scope={scope} model={model} canDownload={canDownload} canWrite={!!cad.data?.can_write}
          importFull={cad.importFile} onNeedNda={() => (lockedKind(access) === 'verification' ? setNoticeOpen(true) : setNdaOpen(true))} />
        <Box sx={{ flex: 1 }} />
        <Typography sx={{ fontSize: 11, color: 'var(--text-4)', display: { xs: 'none', md: 'block' } }}>
          {scope === 'full' ? tx('scopeFullHint') : tx('scopeActiveHint')}
        </Typography>
      </Box>
      <Box sx={{ flex: 1, minHeight: 0 }}>
        {scope === 'full'
          ? <GeometryFullPanel models={models} cad={cad} treeKey={model?.glb_sha256 ?? 0} />
          : active}
      </Box>
      <NdaDialog open={ndaOpen} onClose={() => setNdaOpen(false)} onSigned={() => setRefresh((n) => n + 1)} />
      <VerificationNotice open={noticeOpen} onClose={() => setNoticeOpen(false)} access={access} />
    </Box>
  );
};

export default GeometryTab;
