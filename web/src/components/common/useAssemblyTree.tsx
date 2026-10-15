// The host hook of THE tree (owner 2026-10-15): binds `viewer3d/ComponentTreeView` - the "Motor Assembly" panel - to the server part
// tree and the nodes of the loaded CAD model.  Geometry > Full, Materials > Full and the 3-D model tab all use it; Geometry > Active
// binds the same component to the 2-D regions (viewer3d/ComponentTree).  The rows come from lib/assemblyTree (pure, tested); this hook
// owns the two pieces of UI state: which keys are hidden (the eye toggles) and which row is selected (the viewer highlights it).
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { TreeModel, TreeRow } from '../viewer3d/ComponentTreeView';
import type { PartGroup } from '../../lib/model3d';
import type { PartTreeData } from '../../lib/partTree';
import {
  buildAssemblyRows, hiddenNodeNames, highlightedNodeNames, isolated, toggled, type AssemblyRow, type Fmt,
} from '../../lib/assemblyTree';

export interface AssemblyTreeOptions {
  /** host content at the right end of a part row (mass, material picker) */
  extraFor?: (partId: string) => React.ReactNode;
  /** host content at the right end of a group row (group mass) */
  groupExtraFor?: (groupId: string) => React.ReactNode;
  /** the hidden set and the selection are cleared when this changes (another motor / configuration) */
  resetKey?: string;
}

export function useAssemblyTree(tree: PartTreeData | null, nodes: readonly PartGroup[], o: AssemblyTreeOptions = {}) {
  const { t, i18n } = useTranslation('geometry');
  const lang = i18n.resolvedLanguage ?? 'en';
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => { setHidden(new Set()); setSelected(null); }, [o.resetKey]);

  const fmt: Fmt = useCallback((key, vars, fallback) => t(key, { ...vars, defaultValue: fallback }), [t]);
  const raw = useMemo<AssemblyRow[]>(
    () => (tree ? buildAssemblyRows(tree, nodes, { lang, fmt, hidden }) : []), [tree, nodes, lang, fmt, hidden]);

  const { extraFor, groupExtraFor } = o;
  const rows = useMemo<TreeRow[]>(() => {
    const conv = (r: AssemblyRow, depth: number): TreeRow => ({
      key: r.key, label: r.label, colour: r.colour, visible: r.visible, defaultOpen: r.defaultOpen,
      ...(r.tag ? { tag: r.tag === 'EM' ? t('partEm') : t('partNotEm'), tagTip: r.tag === 'EM' ? t('partEmTip') : t('partNotEmTip') } : {}),
      ...(r.partId && extraFor ? { extra: extraFor(r.partId) } : {}),
      ...(depth === 0 && groupExtraFor ? { extra: groupExtraFor(r.key.slice(2)) } : {}),
      ...(r.group ? { group: r.group.map((c) => conv(c, depth + 1)) } : {}),
    });
    return raw.map((r) => conv(r, 0));
  }, [raw, t, extraFor, groupExtraFor]);

  const model: TreeModel = useMemo(() => ({
    rows,
    selected,
    allVisible: hidden.size === 0,
    onToggle: (key) => setHidden((h) => toggled(h, key)),
    onIsolate: (key) => setHidden((h) => isolated(raw, h, key)),
    onShowAll: () => setHidden(new Set()),
    onSelect: (key) => setSelected((s) => (s === key ? null : key)),
  }), [rows, selected, hidden, raw]);

  const hiddenNodes = useMemo(() => hiddenNodeNames(tree, hidden), [tree, hidden]);
  const highlight = useMemo(() => highlightedNodeNames(tree, selected), [tree, selected]);
  return { model, hiddenNodes, highlight, selected, hidden };
}
