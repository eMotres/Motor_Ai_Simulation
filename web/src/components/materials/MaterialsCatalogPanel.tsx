import React from 'react';
import { Box, Tab, Tabs } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { oneOf } from '../../lib/viewMemory';
import { useRememberedView } from '../../lib/useRememberedView';
import CatalogBrowser from '../catalogBrowser/CatalogBrowser';
import WireStockTable from './WireStockTable';
import PropellersCatalogPanel from './PropellersCatalogPanel';
import FullPartsPanel from './FullPartsPanel';
import ConstructionScopeSwitch, { useConstructionScope } from '../common/ConstructionScopeSwitch';
import {
  catalogKindForMaterialsView,
  MATERIALS_CATALOG_VIEWS,
  type MaterialsCatalogView,
} from './materialsCatalogViews';
import { nsT } from '../../i18n/nsT';

const tx = nsT('motors');

interface Props {
  /** The established material assignment/tree and detail layout. */
  materialView: React.ReactNode;
}

const MaterialsCatalogPanel: React.FC<Props> = ({ materialView }) => {
  useTranslation('motors');
  const [view, setView] = useRememberedView<MaterialsCatalogView>('materials.catalogView', 'materials', oneOf(MATERIALS_CATALOG_VIEWS.map((v) => v.id)));
  // Active = the tab as it was (the parts the electromagnetic simulation uses); Full = the whole motor.  The SAME switch and the
  // SAME state as the Geometry tab (owner 2026-10-15): switching it here switches Geometry too.
  const [mode] = useConstructionScope();
  const kind = catalogKindForMaterialsView(view);
  const labelKey = MATERIALS_CATALOG_VIEWS.find((item) => item.id === view)?.labelKey
    ?? 'materialsViewMaterials';

  return (
    <Box sx={{ height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <Tabs
        value={view}
        onChange={(_event, next: MaterialsCatalogView | null) => { if (next) setView(next); }}
        variant="scrollable"
        scrollButtons="auto"
        allowScrollButtonsMobile
        aria-label={tx('materialsViewMenu')}
        sx={{ minHeight: 38, borderBottom: '1px solid var(--line)', flexShrink: 0,
          '& .MuiTab-root': { minHeight: 38, textTransform: 'none', fontSize: 12, px: 1.5 } }}
      >
        {MATERIALS_CATALOG_VIEWS.map((item) => (
          <Tab key={item.id} value={item.id} label={tx(item.labelKey)} data-testid={`materials-view-${item.id}`} />
        ))}
      </Tabs>

      {view === 'materials' && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 1.5, py: 0.5, flexShrink: 0,
          borderBottom: '1px solid var(--line-soft)' }}>
          <ConstructionScopeSwitch testIdPrefix="materials-scope" />
        </Box>
      )}

      <Box sx={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
        {view === 'materials' ? (mode === 'full' ? <FullPartsPanel /> : materialView)
          : view === 'wire' ? <WireStockTable />
            : view === 'propellers' ? <PropellersCatalogPanel />
            : kind ? (
              <CatalogBrowser
                key={kind}
                kinds={[kind]}
                title={tx(labelKey)}
                help={tx('materialsViewCatalogHelp')}
              />
            ) : null}
      </Box>
    </Box>
  );
};

export default MaterialsCatalogPanel;
