/**
 * Materials tab, mode "Full": the whole motor part by part, on THE part tree (the same tree as Geometry > Full and the 3-D model:
 * same ids, names, groups and order).  The active parts (used by the electromagnetic simulation) are read-only here; every other
 * part of the assembled motor has a material picker (the same picker as the viewer's material bar) and the small tag "not EM".
 * The choice is saved with the die.  A part selected in the tree is highlighted in the 3-D view next to it.
 */
import React, { useMemo, useRef, useState } from 'react';
import { Box, CircularProgress, Tooltip, Typography } from '@mui/material';
import ArrowDropDownIcon from '@mui/icons-material/ArrowDropDown';
import { useTranslation } from 'react-i18next';
import { nsT } from '../../i18n/nsT';
import { modelKey } from '../../lib/model3d';
import { highlightNodes, type PartTreeData, type TreePart } from '../../lib/partTree';
import PartTree from '../common/PartTree';
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
  const [selPart, setSelPart] = useState<string | null>(null);
  const [selGroup, setSelGroup] = useState<string | null>(null);
  const byKey = useRef(new Map<string, FullPartRow>());

  const choices: MaterialChoice[] = payload?.choices ?? [];
  const groups = useMemo(() => groupChoices(choices).map((g) => ({
    key: g.group, label: tx(GROUP_LABEL_KEYS[g.group]), color: GROUP_COLOR[g.group] ?? 'var(--text-3)',
    items: g.items.map((c) => ({ id: c.id })),
  })), [choices]);

  const ordered = rowsForMode(rows, 'full');
  const totalKg = countedMassKg(ordered);
  const partial = !!payload?.partial;
  const nonEm = ordered.some((r) => !r.em);

  // THE part tree: the server's groups and order, with the (overlay-merged) rows as parts
  const tree: PartTreeData | null = useMemo(() => {
    if (!payload?.available || !payload.groups) return null;
    byKey.current = new Map(rows.map((r) => [r.key, r]));
    return { groups: payload.groups, parts: rows.map((r, i) => ({ ...r, id: r.id ?? r.key, group: r.group ?? 'other',
      names: r.names ?? { en: r.name, zh: r.name }, glb: r.glb ?? [], order: r.order ?? i })) as unknown as TreePart[] };
  }, [payload, rows]);
  const highlight = useMemo(() => new Set(highlightNodes(tree, { part: selPart, group: selGroup })), [tree, selPart, selGroup]);

  const renderPicker = (p: TreePart) => {
    const r = byKey.current.get(p.id);
    if (!r) return null;
    const editable = isEditable(r);
    const isDefault = !r.user_set;
    const tip = !editable ? tx('materialsFullEmTip')
      : r.user_set ? tx('materialsFullChosenTip')
        : r.status === 'assumed' ? `${tx('materialsFullDefaultTip')} ${tx('materialsFullAssumedTip')}`
          : tx('materialsFullDefaultTip');
    return (
      <Tooltip title={tip}>
        <Box
          data-testid={`materials-full-pick-${r.key}`}
          onClick={editable ? (e) => { setPicking(r); setAnchor(e.currentTarget as HTMLElement); } : undefined}
          sx={{
            display: 'inline-flex', alignItems: 'center', gap: 0.5, px: 1, py: 0.1, borderRadius: 1.5,
            border: '1px solid var(--line)', cursor: editable ? 'pointer' : 'default', fontSize: 11.5,
            bgcolor: editable ? 'var(--panel)' : 'transparent', maxWidth: 220,
            fontStyle: editable && isDefault ? 'italic' : 'normal',
            '&:hover': editable ? { borderColor: 'var(--text-3)' } : undefined,
          }}>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', paddingRight: 2 }}>
            {r.material_id.replace(/_/g, ' ')}
          </span>
          {editable && <ArrowDropDownIcon sx={{ fontSize: 16, color: 'var(--text-3)' }} />}
        </Box>
      </Tooltip>
    );
  };

  let body: React.ReactNode;
  if (!die) {
    body = <Typography sx={{ p: 2, fontSize: 12, color: 'var(--text-3)' }}>{tx('materialsFullNoMotor')}</Typography>;
  } else if (loading && !payload) {
    body = (
      <Box sx={{ p: 2, display: 'flex', alignItems: 'center', gap: 1 }}>
        <CircularProgress size={14} />
        <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{tx('materialsFullLoading')}</Typography>
      </Box>);
  } else if (error || !payload || !payload.available || !tree) {
    body = (
      <Typography sx={{ p: 2, fontSize: 12, color: 'var(--text-3)' }} title={payload?.reason ?? error ?? ''}>
        {tx('materialsFullUnavailable')}
      </Typography>);
  } else {
    body = (
      <Box data-testid="materials-full-table">
        <Box sx={{ display: 'flex', px: 1.5, py: 0.5, borderBottom: '1px solid var(--line)', fontSize: 10, fontWeight: 600,
          letterSpacing: 0.5, textTransform: 'uppercase', color: 'var(--text-4)', gap: 1 }}>
          <span style={{ flex: 1 }}>{tx('materialsFullPart')}</span>
          <span style={{ minWidth: 62, textAlign: 'right' }}>{tx('materialsFullMass')}</span>
          <span style={{ width: 200 }}>{tx('materialsFullMaterial')}</span>
        </Box>
        <PartTree tree={tree} selectedPart={selPart} selectedGroup={selGroup} onSelectPart={setSelPart} onSelectGroup={setSelGroup}
          showMass renderRight={renderPicker} rightWidth={210} testId="materials-part-tree" />
        <Box sx={{ display: 'flex', px: 1.5, py: 0.75, borderTop: '1px solid var(--line)', fontSize: 12, gap: 1 }}>
          <Tooltip title={tx(partial ? 'materialsFullPartialTip' : 'materialsFullTotalTip')}>
            <span style={{ flex: 1, color: 'var(--text-3)' }}>{tx(partial ? 'materialsFullPartial' : 'materialsFullTotal')}</span>
          </Tooltip>
          <span style={{ fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{formatMass(totalKg)}</span>
        </Box>
        {!partial && payload?.active_kg != null && (
          <Box sx={{ display: 'flex', px: 1.5, pb: 0.75, fontSize: 12, gap: 1 }}>
            <Tooltip title={tx('materialsFullActiveTip')}><span style={{ flex: 1, color: 'var(--text-3)' }}>{tx('materialsFullActive')}</span></Tooltip>
            <span style={{ fontVariantNumeric: 'tabular-nums' }}>{formatMass(payload.active_kg)}</span>
          </Box>
        )}
      </Box>);
  }

  return (
    <Box sx={{ height: '100%', display: 'flex', bgcolor: 'var(--panel-2)', color: 'var(--text-1)' }} data-testid="materials-full-panel">
      <Box sx={{ flex: '1 1 52%', minWidth: 0, overflow: 'auto' }}>
        <Box sx={{ px: 1.5, py: 1, borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 1 }}>
          <Tooltip title={tx('materialsFullHintTip')}>
            <Typography sx={{ fontSize: 12, color: 'var(--text-2)', flex: 1 }}>{tx('materialsFullHint')}</Typography>
          </Tooltip>
          {die && <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>{die}{config ? ` / ${config}` : ''}</Typography>}
          {saving && <CircularProgress size={12} />}
          {!saving && nonEm && localMode && (
            <Typography sx={{ fontSize: 10, color: 'var(--text-4)' }}>{tx('materialsFullLocal')}</Typography>)}
        </Box>
        {body}
        {payload?.available && payload.partial && (
          <Typography sx={{ px: 1.5, py: 1, fontSize: 11, color: 'var(--text-4)' }}>{tx('materialsFullNoCad')}</Typography>)}
      </Box>
      <Box sx={{ flex: '1 1 48%', minWidth: 0, position: 'relative', borderLeft: '1px solid var(--line)' }} data-testid="materials-full-viewer">
        {model ? (
          <ModelCanvas model={model} highlight={highlight} contextLostText={tg('contextLost')} />
        ) : (
          <Typography sx={{ position: 'absolute', top: '45%', left: 0, right: 0, textAlign: 'center', fontSize: 12.5, color: 'var(--text-3)' }}>
            {tg('fullNoModel')}
          </Typography>
        )}
      </Box>
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
