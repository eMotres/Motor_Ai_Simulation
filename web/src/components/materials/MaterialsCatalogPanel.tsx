import React, { useState } from 'react';
import { Box, Tab, Tabs } from '@mui/material';
import { useTranslation } from 'react-i18next';
import CatalogBrowser from '../catalogBrowser/CatalogBrowser';
import WireStockTable from './WireStockTable';
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
  const [view, setView] = useState<MaterialsCatalogView>('materials');
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

      <Box sx={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
        {view === 'materials' ? materialView
          : view === 'wire' ? <WireStockTable />
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
