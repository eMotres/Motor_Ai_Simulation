/**
 * Materials tab, mode "Full": the whole motor part by part, on THE tree - the "Motor Assembly" panel over the 3-D viewer, the same
 * tree (same ids, names, groups, order, eye toggles, swatches, Coil n / Magnet n ...) as Geometry > Full and the 3-D model tab.
 * The active parts (used by the electromagnetic simulation) are read-only here; every other part of the assembled motor has a
 * material picker (the same picker as the viewer's material bar) at the end of its row, next to its mass.  The choice is saved with
 * the die.  A row selected in the tree is highlighted in the viewer.
 */
import React, { useCallback, useMemo, useRef, useState } from 'react';
import { Box, CircularProgress, Tooltip, Typography } from '@mui/material';
import ArrowDropDownIcon from '@mui/icons-material/ArrowDropDown';
import { useTranslation } from 'react-i18next';
import { nsT } from '../../i18n/nsT';
import { modelKey, type PartGroup } from '../../lib/model3d';
import { groupMassKg, type PartTreeData, type TreePart } from '../../lib/partTree';
import ComponentTreeView from '../viewer3d/ComponentTreeView';
import { useAssemblyTree } from '../common/useAssemblyTree';
import ModelCanvas from '../model3d/ModelCanvas';
import { useModelList, useOpenMotor } from '../geometry/useConstruction';
import MaterialPickerPopover from './MaterialPickerPopover';
import { useFullParts } from './useFullParts';
import {
  countedMassKg, formatMass, GROUP_LABEL_KEYS, groupChoices, isEditable, rowsForMode,
  type FullPartRow, type MaterialChoice,
} from './fullPartsLogic';

const tx = nsT('motors');
const tg = nsT('geometry');

const GROUP_COLOR: Record<string, string> = {
  metal: '#f59e0b', steel: 'var(--text-3)', magnet: '#ef4444', insulator: '#3fae5a', other: '#38bdf8',
};

const FullPartsPanel: React.FC = () => {
  useTranslation('motors');
  const openMotor = useOpenMotor();
  const die = openMotor?.die ?? null;
  const config = openMotor?.config ?? null;
  const { payload, rows, loading, saving, error, choose, localMode } = useFullParts(die, config, true);
  const { models } = useModelList();
  const model = useMemo(
    () => models?.find((m) => !!die && modelKey(m) === modelKey({ die, config: config ?? '' })) ?? null, [models, die, config]);

  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [picking, setPicking] = useState<FullPartRow | null>(null);
  const [nodes, setNodes] = useState<PartGroup[]>([]);
  const byKey = useRef(new Map<string, FullPartRow>());

  const choices: MaterialChoice[] = payload?.choices ?? [];
  const groups = useMemo(() => groupChoices(choices).map((g) => ({
    key: g.group, label: tx(GROUP_LABEL_KEYS[g.group]), color: GROUP_COLOR[g.group] ?? 'var(--text-3)',
    items: g.items.map((c) => ({ id: c.id })),
  })), [choices]);

  const ordered = rowsForMode(rows, 'full');
  const totalKg = countedMassKg(ordered);
  const partial = !!payload?.partial;
  const kit = !!payload?.kit;
  const nonEm = ordered.some((r) => !r.em);

  // THE part tree: the server's groups and order, with the (overlay-merged) rows as parts
  const tree: PartTreeData | null = useMemo(() => {
    if (!payload?.available || !payload.groups) return null;
    byKey.current = new Map(rows.map((r) => [r.key, r]));
    return { groups: payload.groups, parts: rows.map((r, i) => ({ ...r, id: r.id ?? r.key, group: r.group ?? 'other',
      names: r.names ?? { en: r.name, zh: r.name }, glb: r.glb ?? [], order: r.order ?? i })) as unknown as TreePart[] };
  }, [payload, rows]);

  const renderPicker = useCallback((id: string) => {
    const r = byKey.current.get(id);
    if (!r) return null;
    const editable = isEditable(r);
    const isDefault = !r.user_set;
    const tip = !editable ? tx('materialsFullEmTip')
      : r.user_set ? tx('materialsFullChosenTip')
        : r.status === 'assumed' ? `${tx('materialsFullDefaultTip')} ${tx('materialsFullAssumedTip')}`
          : tx('materialsFullDefaultTip');
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <span style={{ fontSize: 10, color: 'var(--text-3)', fontVariantNumeric: 'tabular-nums', opacity: r.counted === false ? 0.5 : 1 }}>{formatMass(r.mass_kg)}</span>
        <Tooltip title={tip}>
          <Box
            data-testid={`materials-full-pick-${r.key}`}
            onClick={editable ? (e) => { e.stopPropagation(); setPicking(r); setAnchor(e.currentTarget as HTMLElement); } : undefined}
            sx={{
              display: 'inline-flex', alignItems: 'center', gap: 0.25, px: 0.75, borderRadius: 1.5, width: 120,
              border: '1px solid var(--line)', cursor: editable ? 'pointer' : 'default', fontSize: 10.5,
              bgcolor: editable ? 'var(--panel)' : 'transparent',
              fontStyle: editable && isDefault ? 'italic' : 'normal',
              '&:hover': editable ? { borderColor: 'var(--text-3)' } : undefined,
            }}>
            <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {r.material_id.replace(/_/g, ' ')}
            </span>
            {editable && <ArrowDropDownIcon sx={{ fontSize: 14, color: 'var(--text-3)' }} />}
          </Box>
        </Tooltip>
      </span>
    );
  }, []);
  const groupExtraFor = useCallback((gid: string) => {
    const g = tree ? groupMassKg(tree, gid) : null;
    return g != null ? <span style={{ fontSize: 10, color: 'var(--text-3)', fontVariantNumeric: 'tabular-nums' }}>{formatMass(g)}</span> : null;
  }, [tree]);
  const { model: assembly, hiddenNodes, highlight } = useAssemblyTree(tree, nodes, { extraFor: renderPicker, groupExtraFor, resetKey: `${die}/${config}` });

  let note: React.ReactNode = null;
  if (!die) {
    note = <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{tx('materialsFullNoMotor')}</Typography>;
  } else if (loading && !payload) {
    note = (
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <CircularProgress size={14} />
        <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{tx('materialsFullLoading')}</Typography>
      </Box>);
  } else if (error || !payload || !payload.available || !tree) {
    note = (
      <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }} title={payload?.reason ?? error ?? ''}>
        {tx('materialsFullUnavailable')}
      </Typography>);
  }

  return (
    <Box sx={{ height: '100%', position: 'relative', overflow: 'hidden', bgcolor: 'var(--panel-2)', color: 'var(--text-1)' }} data-testid="materials-full-panel">
      <Box sx={{ position: 'absolute', inset: 0 }} data-testid="materials-full-viewer">
        {model ? (
          <ModelCanvas model={model} highlight={highlight} hiddenNodes={hiddenNodes} contextLostText={tg('contextLost')}
            onLoaded={({ groups: g }) => setNodes(g)} />
        ) : (
          <Typography sx={{ position: 'absolute', top: '45%', left: 0, right: 0, textAlign: 'center', fontSize: 12.5, color: 'var(--text-3)' }}>
            {tg('fullNoModel')}
          </Typography>
        )}
      </Box>

      <Box sx={{ position: 'absolute', top: 0, left: 0, right: 0, px: 1.5, py: 0.75, display: 'flex', alignItems: 'center', gap: 1,
        zIndex: 3, pointerEvents: 'none' }}>
        <Tooltip title={tx('materialsFullHintTip')}>
          <Typography sx={{ fontSize: 12, color: 'var(--text-2)', pointerEvents: 'auto' }}>{tx('materialsFullHint')}</Typography>
        </Tooltip>
        <Box sx={{ flex: 1 }} />
        {die && <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>{die}{config ? ` / ${config}` : ''}</Typography>}
        {saving && <CircularProgress size={12} />}
        {!saving && nonEm && localMode && (
          <Typography sx={{ fontSize: 10, color: 'var(--text-4)' }}>{tx('materialsFullLocal')}</Typography>)}
      </Box>

      <Box data-testid="materials-full-table">
        {tree && assembly.rows.length > 0 ? (
          <ComponentTreeView model={assembly} style={{ top: 36, left: 8, zIndex: 4, width: 400 }} rowsMaxHeight="calc(100% - 150px)" />
        ) : note && (
          <Box sx={{ position: 'absolute', top: 40, left: 12, zIndex: 4 }}>{note}</Box>
        )}
      </Box>

      {tree && (
        <Box sx={{ position: 'absolute', left: 8, bottom: 8, zIndex: 4, width: 400, px: 1.25, py: 0.75, borderRadius: 1.5,
          bgcolor: 'var(--overlay)', border: '1px solid var(--line)', fontSize: 12 }}>
          <Box sx={{ display: 'flex', gap: 1 }}>
            <Tooltip title={tx(partial ? 'materialsFullPartialTip' : kit ? 'materialsFullTotalKitTip' : 'materialsFullTotalTip')}>
              <span style={{ flex: 1, color: 'var(--text-3)' }}>{tx(partial ? 'materialsFullPartial' : kit ? 'materialsFullTotalKit' : 'materialsFullTotal')}</span>
            </Tooltip>
            <span style={{ fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{formatMass(totalKg)}</span>
          </Box>
          {!partial && payload?.active_kg != null && (
            <Box sx={{ display: 'flex', gap: 1 }}>
              <Tooltip title={tx('materialsFullActiveTip')}><span style={{ flex: 1, color: 'var(--text-3)' }}>{tx('materialsFullActive')}</span></Tooltip>
              <span style={{ fontVariantNumeric: 'tabular-nums' }}>{formatMass(payload.active_kg)}</span>
            </Box>
          )}
          {payload?.available && payload.partial && (
            <Typography sx={{ pt: 0.5, fontSize: 10.5, color: 'var(--text-4)' }}>{tx('materialsFullNoCad')}</Typography>)}
        </Box>
      )}

      <MaterialPickerPopover
        open={!!anchor && !!picking}
        anchorEl={anchor}
        onClose={() => { setAnchor(null); setPicking(null); }}
        title={tx('materialsFullPickTitle', { part: picking ? tx(`materialsPart_${picking.key}`, { defaultValue: picking.name }) : '' }).toUpperCase()}
        groups={groups}
        current={picking?.material_id}
        disabled={saving}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
        transformOrigin={{ vertical: 'top', horizontal: 'left' }}
        onPick={(id) => {
          if (picking) choose(picking.key, id === picking.default_material_id ? null : id);
          setAnchor(null); setPicking(null);
        }}
      />
    </Box>
  );
};

export default FullPartsPanel;
