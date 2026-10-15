// Geometry > Full (owner 2026-10-15): the FULL construction of the motor.
//   left   THE part tree (the same tree as Materials > Full and the 3-D model) and the construction parameters of the CAD template -
//          walls, shafts, holders, rings, bearings, the phase board, screws, leads, CAD-only clearances - EDITABLE, grouped by the
//          part-tree groups, with units, limits and tooltips (EN / zh-CN).  Every one of them is "CAD only, not simulated".
//   right  the server-built CAD model in the same viewer as the 3-D model tab; the part selected in the tree is highlighted.
//
// An edit is validated by the server with the CAD template's own pre-check, saved with the die configuration (cad_overrides) and a
// rebuild of the CAD model is requested; the old model stays on screen with a "rebuilding" badge until the new one is ready.
// The electromagnetic parameters are NOT here (they are edited in Active) and a Full edit never changes an electromagnetic input.
import React, { useCallback, useMemo, useRef, useState } from 'react';
import {
  Alert, Box, Button, Chip, CircularProgress, IconButton, MenuItem, Select, TextField, Tooltip, Typography,
} from '@mui/material';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import CenterFocusStrongIcon from '@mui/icons-material/CenterFocusStrong';
import SaveIcon from '@mui/icons-material/Save';
import { useTranslation } from 'react-i18next';
import { nsT } from '../../i18n/nsT';
import SectionLabel from '../common/SectionLabel';
import PartTree from '../common/PartTree';
import HelpTip from '../common/HelpTip';
import ModelCanvas, { type ModelCanvasHandle } from '../model3d/ModelCanvas';
import { modelKey, type ModelInfo } from '../../lib/model3d';
import { highlightNodes, nameOf } from '../../lib/partTree';
import {
  changesOf, draftError, formatValue, invalidDrafts, modelBadge, rowsOfGroup, type CadRow,
} from '../../lib/cadParams';
import { usePartTreeData, useCadParams, useOpenMotor } from './useConstruction';

const tx = nsT('geometry');

interface Props {
  panelWidth: number;
  divider: React.ReactNode;
  models: ModelInfo[] | null;
  /** the shared CAD hook of the Geometry tab (the Files menu imports through it) */
  cad: ReturnType<typeof useCadParams>;
  /** bump to re-fetch the part tree (after a rebuild) */
  treeKey: string | number;
}

const fieldSx = { width: 84, '& .MuiInputBase-input': { px: '6px', py: '3px', fontSize: 12, textAlign: 'right' as const } };

const GeometryFullPanel: React.FC<Props> = ({ panelWidth, divider, models, cad, treeKey }) => {
  const { i18n } = useTranslation('geometry');
  const lang = i18n.resolvedLanguage ?? 'en';
  const openMotor = useOpenMotor();
  const die = openMotor?.die ?? null;
  const config = openMotor?.config ?? null;
  const model = useMemo(() => models?.find((m) => !!die && modelKey(m) === modelKey({ die, config: config ?? '' })) ?? null, [models, die, config]);
  const { tree, loading: treeLoading } = usePartTreeData(die, config, 'family', !!die, treeKey);
  const { data, loading, saving, save, chooseBearing, requestRebuild } = cad;

  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [selPart, setSelPart] = useState<string | null>(null);
  const [selGroup, setSelGroup] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const canvas = useRef<ModelCanvasHandle>(null);

  const rows = data?.available ? data.rows : [];
  const changes = useMemo(() => changesOf(rows, drafts), [rows, drafts]);
  const invalid = useMemo(() => invalidDrafts(rows, drafts), [rows, drafts]);
  const dirty = Object.keys(changes).length;
  const canWrite = !!data?.can_write;
  const badge = modelBadge(data?.rebuild);
  const highlight = useMemo(() => new Set(highlightNodes(tree, { part: selPart, group: selGroup })), [tree, selPart, selGroup]);

  const onSave = useCallback(async () => {
    setProblems([]);
    const res = await save(changes);
    if (res.ok) setDrafts({}); else setProblems(res.lines);
  }, [save, changes]);

  const onBearing = useCallback(async (card: string) => {
    setProblems([]);
    const res = await chooseBearing(card || null);
    if (!res.ok) setProblems(res.lines);
  }, [chooseBearing]);

  const rowView = (r: CadRow) => {
    const txt = drafts[r.name] ?? formatValue(r.value);
    const err = drafts[r.name] !== undefined ? draftError(r, drafts[r.name]) : null;
    const changed = drafts[r.name] !== undefined && r.name in changes;
    const ro = !r.editable;
    const tip = `${nameOf(r.tip, lang)}${ro ? ` ${nameOf(data?.read_only_note, lang)}` : ''} (${r.name})`;
    return (
      <Box key={r.name} data-testid={`cad-row-${r.name}`}
        sx={{ display: 'flex', alignItems: 'center', gap: 0.75, px: 0.5, py: 0.25,
          bgcolor: changed ? 'rgba(59,130,246,.10)' : 'transparent', opacity: ro ? 0.75 : 1 }}>
        <Tooltip title={tip} placement="right" enterDelay={250}>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography data-testid={`cad-label-${r.name}`} sx={{ fontSize: 12, lineHeight: 1.25, fontWeight: r.overridden ? 700 : 400,
              overflowWrap: 'anywhere' }}>{nameOf(r.label, lang)}</Typography>
            <Typography sx={{ fontSize: 9.5, lineHeight: 1.2, color: 'var(--text-4)', fontFamily: 'monospace', overflowWrap: 'anywhere' }}>{r.name}</Typography>
          </Box>
        </Tooltip>
        {r.name.startsWith('offset_') && (
          <Tooltip title={tx('cadOnlyTip')}><Chip size="small" label={tx('cadOnlyShort')}
            sx={{ height: 15, fontSize: 9, '& .MuiChip-label': { px: 0.6 }, border: '1px solid var(--line)', bgcolor: 'transparent' }} /></Tooltip>
        )}
        <TextField size="small" value={txt} disabled={!canWrite || saving || ro} error={!!err}
          onChange={(e) => setDrafts((d) => ({ ...d, [r.name]: e.target.value }))}
          onKeyDown={(e) => { if (e.key === 'Enter' && dirty && !invalid.length) void onSave(); }}
          onFocus={(e) => e.target.select()} sx={fieldSx}
          inputProps={{ inputMode: 'decimal', 'aria-label': r.name, readOnly: ro,
            title: err ? tx(`cadErr_${err}`, { min: r.min, max: r.max }) : `${r.min} … ${r.max} ${r.unit}`.trim() }} />
        <Typography sx={{ width: 24, fontSize: 10.5, color: 'var(--text-4)' }}>{r.unit}</Typography>
        <Tooltip title={tx('cadReset', { value: formatValue(r.default) })}>
          <span>
            <IconButton size="small" sx={{ p: 0.25, visibility: !ro && (r.overridden || changed) ? 'visible' : 'hidden' }} disabled={!canWrite || saving}
              onClick={() => setDrafts((d) => ({ ...d, [r.name]: formatValue(r.default) }))}>
              <RestartAltIcon sx={{ fontSize: 15 }} />
            </IconButton>
          </span>
        </Tooltip>
      </Box>
    );
  };

  const leftBody = (
    <>
      <SectionLabel sx={{ px: 0.5, py: 0.5 }}>{tx('fullPartsTitle')}</SectionLabel>
      <Box sx={{ maxHeight: 260, overflowY: 'auto', border: '1px solid var(--line-soft)', borderRadius: 1, mb: 1 }}>
        {treeLoading && !tree ? <Box sx={{ p: 1 }}><CircularProgress size={14} /></Box>
          : tree && tree.available !== false && tree.groups.length ? (
            <PartTree tree={tree} selectedPart={selPart} selectedGroup={selGroup} onSelectPart={setSelPart} onSelectGroup={setSelGroup} showMass />
          ) : <Typography sx={{ p: 1, fontSize: 12, color: 'var(--text-3)' }}>{tx('fullNoTree')}</Typography>}
      </Box>
      {tree?.partial && <Typography sx={{ px: 0.5, mb: 1, fontSize: 11, color: 'var(--text-4)' }}>{tx('fullPartialNote')}</Typography>}

      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 0.5, mb: 0.75 }}>
        <Chip size="small" variant="outlined" label={tx('cadParamCount', { n: rows.length })} sx={{ fontSize: 10, height: 20 }} data-testid="cad-count" />
        <Tooltip title={tx('cadOnlyTip')}><Chip size="small" label={tx('cadOnlyLabel')} sx={{ fontSize: 10, height: 20, bgcolor: 'transparent', border: '1px solid var(--line)' }} /></Tooltip>
        <Box sx={{ flex: 1 }} />
        <HelpTip title={tx('fullEditHelp')} />
      </Box>

      {!die ? <Typography sx={{ p: 1, fontSize: 12, color: 'var(--text-3)' }}>{tx('fullNoMotor')}</Typography>
        : loading && !data ? <Box sx={{ p: 1 }}><CircularProgress size={14} /></Box>
          : !data?.available ? (
            <Typography sx={{ p: 1, fontSize: 12, color: 'var(--text-3)' }} data-testid="cad-unavailable">{data?.reason ?? tx('fullUnavailable')}</Typography>
          ) : (
            <>
              {!canWrite && <Typography sx={{ px: 0.5, mb: 0.5, fontSize: 11, color: 'var(--text-4)' }}>{tx('cadReadOnly')}</Typography>}
              {problems.length > 0 && (
                <Alert severity="error" sx={{ mb: 1, fontSize: 11.5, py: 0 }} onClose={() => setProblems([])} data-testid="cad-problems">
                  <Typography sx={{ fontSize: 11.5, fontWeight: 700 }}>{tx('cadRefused')}</Typography>
                  {problems.map((p, i) => <div key={i}>{p}</div>)}
                </Alert>
              )}
              {data.groups.map((g) => (
                <Box key={g.id} sx={{ mb: 0.75 }} data-testid={`cad-group-${g.id}`}>
                  <SectionLabel sx={{ px: 0.5, py: 0.5 }}>{nameOf(g.name, lang)}</SectionLabel>
                  {rowsOfGroup(rows, g.id).map(rowView)}
                  {g.id === 'bearings' && data.bearing && (
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, px: 0.5, py: 0.4 }} data-testid="cad-bearing">
                      <Typography sx={{ flex: 1, fontSize: 12 }}>{tx('cadBearingCard')}</Typography>
                      <Select size="small" displayEmpty value={data.bearing.override ?? ''} disabled={!canWrite || saving}
                        onChange={(e) => void onBearing(String(e.target.value))} sx={{ fontSize: 12, maxWidth: 150 }}
                        renderValue={(v) => (v ? String(v) : (data.bearing?.config_card ?? tx('cadBearingModel')))}>
                        <MenuItem value="" sx={{ fontSize: 12 }}>{tx('cadBearingModel')}</MenuItem>
                        {data.bearing.choices.map((c) => (
                          <MenuItem key={c.id} value={c.id} sx={{ fontSize: 12 }}>{`${c.id}  (${c.d}×${c.D}×${c.B})`}</MenuItem>
                        ))}
                      </Select>
                      <HelpTip title={tx('cadBearingTip')} />
                    </Box>
                  )}
                </Box>
              ))}
              <Box sx={{ position: 'sticky', bottom: 0, bgcolor: 'var(--panel-2, var(--panel))', pt: 0.75, pb: 0.5 }}>
                <Button variant={dirty ? 'contained' : 'outlined'} color={dirty ? 'primary' : 'inherit'} size="small" fullWidth
                  disabled={!canWrite || saving || !dirty || invalid.length > 0} data-testid="cad-save"
                  startIcon={saving ? <CircularProgress size={13} color="inherit" /> : <SaveIcon sx={{ fontSize: 16 }} />}
                  onClick={() => void onSave()} sx={{ fontWeight: 700, fontSize: 11, letterSpacing: '0.06em', py: 0.6 }}>
                  {saving ? tx('cadSaving') : dirty ? tx('cadSaveN', { n: dirty }) : tx('cadSave')}
                </Button>
              </Box>
            </>
          )}
    </>
  );

  const badgeText = badge === 'rebuilding' ? tx('modelRebuilding') : badge === 'queued' ? tx('modelQueued')
    : badge === 'stale' ? tx('modelStale') : badge === 'failed' ? tx('modelRebuildFailed') : '';

  return (
    <Box sx={{ display: 'flex', height: '100%', minHeight: 0 }} data-testid="geometry-full">
      <Box sx={{ width: Math.max(panelWidth, 400), flexShrink: 0, overflowY: 'auto', p: 1.5, pt: 0.5 }}>{leftBody}</Box>
      {divider}
      <Box sx={{ flex: 1, position: 'relative', overflow: 'hidden', minWidth: 0 }}>
        {model ? (
          <ModelCanvas ref={canvas} model={model} highlight={highlight} contextLostText={tx('contextLost')}>
            <Box sx={{ position: 'absolute', top: 10, left: 10, display: 'flex', alignItems: 'center', gap: 1, zIndex: 2, pointerEvents: 'none' }}>
              {badge !== 'none' && (
                <Chip data-testid="model-badge" icon={badge === 'failed' || badge === 'stale' ? undefined : <CircularProgress size={12} />}
                  color={badge === 'failed' ? 'error' : 'warning'} size="small" label={badgeText} sx={{ pointerEvents: 'auto' }} />
              )}
            </Box>
            <Box sx={{ position: 'absolute', top: 8, right: 8, zIndex: 2, display: 'flex', gap: 1, alignItems: 'center' }}>
              {(badge === 'stale' || badge === 'failed') && canWrite && (
                <Button size="small" variant="outlined" onClick={() => void requestRebuild()} data-testid="cad-rebuild-now"
                  sx={{ textTransform: 'none', fontSize: 12, bgcolor: 'var(--panel)' }}>{tx('rebuildNow')}</Button>
              )}
              <Button size="small" onClick={() => canvas.current?.fit()} startIcon={<CenterFocusStrongIcon sx={{ fontSize: 16 }} />}
                sx={{ textTransform: 'none', fontSize: 12, color: 'var(--text-2)', bgcolor: 'var(--panel)' }}>{tx('fit')}</Button>
            </Box>
            {badge === 'failed' && data?.rebuild.message && (
              <Alert severity="error" sx={{ position: 'absolute', left: 10, right: 10, bottom: 10, fontSize: 11.5, zIndex: 2, maxHeight: 130, overflow: 'auto' }}>
                {data.rebuild.message}
              </Alert>
            )}
            {data?.model?.template && badge === 'none' && (
              <Typography sx={{ position: 'absolute', left: 12, bottom: 8, fontSize: 10.5, color: 'var(--text-4)', zIndex: 2 }}>
                {tx('modelVersion', { template: data.model.template, built: (data.model.built_at ?? '').slice(0, 10) })}
              </Typography>
            )}
          </ModelCanvas>
        ) : (
          <Typography sx={{ position: 'absolute', top: '45%', left: 0, right: 0, textAlign: 'center', fontSize: 13, color: 'var(--text-3)' }}
            data-testid="full-no-model">
            {models === null ? tx('filesWorking') : tx('fullNoModel')}
          </Typography>
        )}
      </Box>
    </Box>
  );
};

export default GeometryFullPanel;
