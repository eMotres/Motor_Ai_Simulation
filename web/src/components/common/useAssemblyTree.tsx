// Host adapter: map the server's accounting/part-tree payload and loaded GLB names to the viewer's neutral tree DTO.
// Visibility and selection state belong to the viewer; this hook only translates host data and labels.
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import type { PartGroup } from '../../lib/model3d';
import type { PartTreeData } from '../../lib/partTree';
import { buildAssemblyRows, type Fmt } from '../../lib/assemblyTree';

export function useAssemblyRows(tree: PartTreeData | null, nodes: readonly PartGroup[]) {
  const { t, i18n } = useTranslation('geometry');
  const lang = i18n.resolvedLanguage ?? 'en';
  const fmt: Fmt = (key, vars, fallback) => t(key, { ...vars, defaultValue:fallback });
  return useMemo(() => tree ? buildAssemblyRows(tree, nodes, { lang, fmt }) : [], [tree, nodes, lang, t]);
}
