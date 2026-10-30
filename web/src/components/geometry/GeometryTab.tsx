// The Geometry tab with the Active | Full switch (owner 2026-10-15).
//   Active  the shared EM parameter column beside the active-part viewer.
//   Full    the same EM column, with editable CAD construction parameters below it and the shared CAD viewer beside it.
// The switch is the one of the Materials tab (one shared state); the Files menu next to it changes with the scope.
import React, { useMemo, useState } from 'react';
import { Box } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { ConstructionScopeTabs, useConstructionScope } from '../common/ConstructionScopeSwitch';
import { HeaderRight, HeaderTabs } from '../common/PanelHeader';
import NdaDialog from '../model3d/NdaDialog';
import VerificationNotice from '../model3d/VerificationNotice';
import { lockedKind } from '../../lib/verification';
import FilesMenu from './FilesMenu';
import GeometryFullPanel from './GeometryFullPanel';
import GeometryLayout from './GeometryLayout';
import { useCadParams, useModelList, useOpenMotor } from './useConstruction';
import { modelKey } from '../../lib/model3d';

interface Props {
  emContent: React.ReactNode;
  activeViewer: React.ReactNode;
  panelWidth: number;
  onDividerMouseDown: (event: React.MouseEvent) => void;
}

const GeometryTab: React.FC<Props> = ({ emContent, activeViewer, panelWidth, onDividerMouseDown }) => {
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
      {/* Active | Full and the Files menu are in the shared panel header row (components/common/PanelHeader) */}
      <HeaderTabs tab="geometry"><ConstructionScopeTabs testIdPrefix="geometry-scope" /></HeaderTabs>
      <HeaderRight tab="geometry">
        <FilesMenu scope={scope} model={model} canDownload={canDownload} canWrite={!!cad.data?.can_write}
          importFull={cad.importFile} onNeedNda={() => (lockedKind(access) === 'verification' ? setNoticeOpen(true) : setNdaOpen(true))} />
      </HeaderRight>
      <Box sx={{ flex: 1, minHeight: 0 }}>
        {scope === 'full'
          ? <GeometryFullPanel models={models} canDownload={canDownload} cad={cad} treeKey={model?.glb_sha256 ?? 0}
            emContent={emContent} panelWidth={panelWidth} onDividerMouseDown={onDividerMouseDown} />
          : <GeometryLayout emContent={emContent} viewer={activeViewer} panelWidth={panelWidth} onDividerMouseDown={onDividerMouseDown} />}
      </Box>
      <NdaDialog open={ndaOpen} onClose={() => setNdaOpen(false)} onSigned={() => setRefresh((n) => n + 1)} />
      <VerificationNotice open={noticeOpen} onClose={() => setNoticeOpen(false)} access={access} />
    </Box>
  );
};

export default GeometryTab;
