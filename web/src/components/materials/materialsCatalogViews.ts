import type { CatalogKind } from '../catalogBrowser/catalogLogic';

export type MaterialsCatalogView = 'materials' | 'bearing' | 'lubricant' | 'wire' | 'device' | 'propellers';

export const MATERIALS_CATALOG_VIEWS: ReadonlyArray<{
  id: MaterialsCatalogView;
  labelKey: string;
}> = [
  { id: 'materials', labelKey: 'materialsViewMaterials' },
  { id: 'bearing', labelKey: 'materialsViewBearings' },
  { id: 'lubricant', labelKey: 'materialsViewLubricants' },
  { id: 'wire', labelKey: 'materialsViewWireStock' },
  { id: 'device', labelKey: 'materialsViewPowerDevices' },
  { id: 'propellers', labelKey: 'materialsViewPropellers' },
];

/** Reference catalogue kinds use the existing read-only card browser. */
export function catalogKindForMaterialsView(view: MaterialsCatalogView): CatalogKind | null {
  return view === 'bearing' || view === 'lubricant' || view === 'device' ? view : null;
}
