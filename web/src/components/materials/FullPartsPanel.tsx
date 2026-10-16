/**
 * Materials tab, mode "Full": the whole motor part by part, on THE tree - the "Motor Assembly" panel over the 3-D viewer, the same
 * tree (same ids, names, groups, order, eye toggles, swatches, Coil n / Magnet n ...) as Geometry > Full and the 3-D model tab.
 * The active parts (used by the electromagnetic simulation) are read-only here; every other part of the assembled motor has a
 * material picker (the same picker as the viewer's material bar) in the host bar when selected. The choice is saved with
 * the die.  A row selected in the tree is highlighted in the viewer.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Box, CircularProgress, Tooltip, Typography } from '@mui/material';
import ArrowDropDownIcon from '@mui/icons-material/ArrowDropDown';
import { useTranslation } from 'react-i18next';
import { nsT } from '../../i18n/nsT';
import { type PartGroup } from '../../lib/model3d';
import { type PartTreeData, type TreePart } from '../../lib/partTree';
import { useAssemblyRows } from '../common/useAssemblyTree';
import { UnifiedViewer } from '../../modules/viewer3d';
import { fullToolsOpen, modelKey, viewOf } from '../../lib/model3d';
import { modelGlbUrl } from '../../lib/model3dHost';
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
  const { models, canDownload } = useModelList();
  const { payload, rows, loading, saving, error, choose, localMode } = useFullParts(die, config, canDownload);
  const model = useMemo(
    () => models?.find((m) => !!die && modelKey(m) === modelKey({ die, config: config ?? '' })) ?? null, [models, die, config]);
  const fullAccess = fullToolsOpen(viewOf(model, canDownload));
  const currentPayload = !!die && payload?.available === true && payload.die === die && payload.config === (config ?? '');

  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [picking, setPicking] = useState<FullPartRow | null>(null);
  const [nodes, setNodes] = useState<PartGroup[]>([]);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const byKey = useMemo(() => new Map(currentPayload ? rows.map((r) => [r.key, r]) : []), [currentPayload, rows]);

  useEffect(() => {
    setSelectedKey(null);
    setAnchor(null);
    setPicking(null);
    setNodes([]);
  }, [die, config, fullAccess]);

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
    if (!currentPayload || !payload.groups) return null;
    return { groups: payload.groups, parts: rows.map((r, i) => ({ ...r, id: r.id ?? r.key, group: r.group ?? 'other',
      names: r.names ?? { en: r.name, zh: r.name }, glb: r.glb ?? [], order: r.order ?? i })) as unknown as TreePart[] };
  }, [currentPayload, payload, rows]);

  const renderPicker = useCallback((id: string) => {
    const r = byKey.get(id);
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
  }, [byKey]);
  const assembly = useAssemblyRows(tree, nodes);

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
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', position: 'relative', overflow: 'hidden', bgcolor: 'var(--panel-2)', color: 'var(--text-1)' }} data-testid="materials-full-panel">
      <Box sx={{ position: 'relative', px: 1.5, py: 0.75, display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 1,
        flexShrink: 0, borderTop: '1px solid var(--line-soft)', bgcolor: 'var(--panel)' }}>
        <Tooltip title={tx('materialsFullHintTip')}>
          <Typography sx={{ fontSize: 12, color: 'var(--text-2)', pointerEvents: 'auto' }}>{tx('materialsFullHint')}</Typography>
        </Tooltip>
        <Box sx={{ flex: 1 }} />
        {die && <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>{die}{config ? ` / ${config}` : ''}</Typography>}
        {saving && <CircularProgress size={12} />}
        {!saving && nonEm && localMode && (
          <Typography sx={{ fontSize: 10, color: 'var(--text-4)' }}>{tx('materialsFullLocal')}</Typography>)}
        {fullAccess && currentPayload && selectedKey && byKey.has(selectedKey) && <Box data-testid="materials-full-selected-part" sx={{ display:'flex', alignItems:'center', gap:0.75, px:1, py:.25, border:'1px solid var(--line)', borderRadius:1 }}>
          <Typography sx={{ fontSize:11, color:'var(--text-3)' }}>{byKey.get(selectedKey)?.name}</Typography>{renderPicker(selectedKey)}
        </Box>}
      </Box>

      <Box sx={{ flex: 1, minHeight: 0, minWidth: 0, position: 'relative' }} data-testid="materials-full-viewer">
        {model ? (
          <UnifiedViewer sourceKey={`${modelKey(model)}:${model.glb_sha256 ?? ''}:${model.simplified_glb_sha256 ?? ''}`} resetKey={`materialsFull:${modelKey(model)}`}
            glbUrl={modelGlbUrl(model, canDownload)} toolbarSpec={fullAccess ? 'full' : 'simplified'} tree={assembly}
            selectedKey={selectedKey} onSelectedKeyChange={setSelectedKey} contextLostText={tg('contextLost')}
            simplifiedText={tg('simplifiedLine')} simplifiedTip={tg('simplifiedLineTip')} noViewText={tg('noView')}
            onLoaded={({ groups: g }) => setNodes(g)} />
        ) : (
          <Typography sx={{ position: 'absolute', top: '45%', left: 0, right: 0, textAlign: 'center', fontSize: 12.5, color: 'var(--text-3)' }}>
            {tg('fullNoModel')}
          </Typography>
        )}
      </Box>

      <Box data-testid="materials-full-table">
        {(!tree || !fullAccess) && note && (
          <Box sx={{ position: 'relative', px: 1.5, py: 0.5 }}>{note}</Box>
        )}
      </Box>

      {tree && (
        <Box sx={{ position: 'relative', zIndex: 4, width: 400, maxWidth: 'calc(100% - 16px)', mx: 1, mb: 1, px: 1.25, py: 0.75, borderRadius: 1.5,
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
        open={!!anchor && !!picking && fullAccess && currentPayload && byKey.has(picking.key)}
        anchorEl={anchor}
        onClose={() => { setAnchor(null); setPicking(null); }}
        title={tx('materialsFullPickTitle', { part: picking ? tx(`materialsPart_${picking.key}`, { defaultValue: picking.name }) : '' }).toUpperCase()}
        groups={groups}
        current={picking?.material_id}
        disabled={saving || !fullAccess || !currentPayload}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
        transformOrigin={{ vertical: 'top', horizontal: 'left' }}
        onPick={(id) => {
          if (fullAccess && currentPayload && picking && byKey.has(picking.key)) choose(picking.key, id === picking.default_material_id ? null : id);
          setAnchor(null); setPicking(null);
        }}
      />
    </Box>
  );
};

export default FullPartsPanel;
