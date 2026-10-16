import React, { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { UnifiedViewer } from '../../modules/viewer3d';
import { useActiveViewerAdapter } from '../model3d/ActiveModelAdapter';
import { useUIStore } from '../../stores/motorStore';
import MaterialBar from './MaterialBar';

const familyKey = (key:string|null):string|null => key?.startsWith('coil_')?'coils':key?.startsWith('magnet_')?'magnets':key;
const UnifiedActiveMotorScene: React.FC<{ force3d?: boolean }> = ({ force3d = false }) => {
  const { t } = useTranslation('geometry');
  const renderMode = useUIStore((s) => s.renderMode);
  const toggleRenderMode = useUIStore((s) => s.toggleRenderMode);
  const selected = useUIStore((s) => s.selectedPart);
  const setSelectedPart = useUIStore((s) => s.setSelectedPart);
  const source = useActiveViewerAdapter();
  useEffect(() => { if (force3d && renderMode === '2d') toggleRenderMode(); }, [force3d, renderMode, toggleRenderMode]);
  return <UnifiedViewer sourceKey={source.sourceKey} resetKey={source.sourceKey} activeParts={source.parts} toolbarSpec="full" tree={source.tree}
    selectedKey={selected} onSelectedKeyChange={(key) => setSelectedPart(familyKey(key) as Parameters<typeof setSelectedPart>[0])}
    contextLostText={t('contextLost')} hostExtras={<MaterialBar />} />;
};
export default UnifiedActiveMotorScene;
