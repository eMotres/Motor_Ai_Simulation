// Geometry > Full (owner 2026-10-15): the FULL construction of the motor.
//   left   the shared EM parameter column, followed by editable CAD-template construction parameters in Full.
//   right  the server-built CAD model in the same viewer as the 3-D model tab; the selected part is highlighted.
//
// An edit is validated by the server with the CAD template's own pre-check, saved with the die configuration (cad_overrides) and a
// rebuild of the CAD model is requested; the old model stays on screen with a "rebuilding" badge until the new one is ready.
// The shared electromagnetic parameters stay above the CAD editor and CAD edits never change an electromagnetic input.
import React, { useCallback, useMemo, useState } from 'react';
import {
  Alert, Box, Button, Chip, CircularProgress, IconButton, MenuItem, Select, TextField, Tooltip, Typography,
} from '@mui/material';
import RestartAltIcon from '@mui/icons-material/RestartAlt';
import SaveIcon from '@mui/icons-material/Save';
import { useTranslation } from 'react-i18next';
import { nsT } from '../../i18n/nsT';
import SectionLabel from '../common/SectionLabel';
import { useAssemblyRows } from '../common/useAssemblyTree';
import HelpTip from '../common/HelpTip';
import GeometryLayout from './GeometryLayout';
import { UnifiedViewer } from '../../modules/viewer3d';
import { fullToolsOpen, modelKey, viewOf, type ModelInfo, type PartGroup } from '../../lib/model3d';
import { modelGlbUrl } from '../../lib/model3dHost';
import { nameOf } from '../../lib/partTree';
import {
  changesOf, draftError, formatValue, invalidDrafts, modelBadge, rowsOfGroup, type CadRow,
} from '../../lib/cadParams';
import { usePartTreeData, useCadParams, useOpenMotor } from './useConstruction';
import { fullToolbar } from '../../lib/viewerToolbar';

const tx = nsT('geometry');

interface Props {
  models: ModelInfo[] | null;
  canDownload: boolean;
  /** the shared CAD hook of the Geometry tab (the Files menu imports through it) */
  cad: ReturnType<typeof useCadParams>;
  /** bump to re-fetch the part tree (after a rebuild) */
  treeKey: string | number;
  emContent: React.ReactNode;
  panelWidth: number;
  onDividerMouseDown: (event: React.MouseEvent) => void;
}

const fieldSx = { width: 84, '& .MuiInputBase-input': { px: '6px', py: '3px', fontSize: 12, textAlign: 'right' as const } };

const GeometryFullPanel: React.FC<Props> = ({ models, canDownload, cad, treeKey, emContent, panelWidth, onDividerMouseDown }) => {
  const { i18n } = useTranslation('geometry');
  const lang = i18n.resolvedLanguage ?? 'en';
  const openMotor = useOpenMotor();
  const die = openMotor?.die ?? null;
  const config = openMotor?.config ?? null;
  const model = useMemo(() => models?.find((m) => !!die && modelKey(m) === modelKey({ die, config: config ?? '' })) ?? null, [models, die, config]);
  const { tree, loading: treeLoading } = usePartTreeData(die, config, 'family', !!die, treeKey);
  const { data, loading, saving, save, chooseBearing, requestRebuild } = cad;

  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [nodes, setNodes] = useState<PartGroup[]>([]);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[]>([]);

  const rows = data?.available ? data.rows : [];
  const changes = useMemo(() => changesOf(rows, drafts), [rows, drafts]);
  const invalid = useMemo(() => invalidDrafts(rows, drafts), [rows, drafts]);
  const dirty = Object.keys(changes).length;
  const canWrite = !!data?.can_write;
  const badge = modelBadge(data?.rebuild);
  const assembly = useAssemblyRows(tree, nodes);

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
            {/* one style for every parameter row (owner 2026-10-16): one line, the template key only in the tooltip */}
            <Typography data-testid={`cad-label-${r.name}`} noWrap sx={{ fontSize: 12, lineHeight: 1.4, fontWeight: r.overridden ? 700 : 400 }}>
              {nameOf(r.label, lang)}
            </Typography>
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
      <Box sx={{ display: 'flex', alignItems: 'center', px: 0.5, pt: 1.25, pb: 0.25 }}>
        <SectionLabel sx={{ flex: 1 }}>{tx('cadDrawer')}</SectionLabel>
        <HelpTip title={`${tx('cadOnlyTip')} ${tx('fullEditHelp')}`} />
      </Box>
      {tree?.partial && <Typography sx={{ px: 0.5, mb: 1, fontSize: 11, color: 'var(--text-4)' }}>{tx('fullPartialNote')}</Typography>}


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

  const items = fullToolbar({ hasModel: !!model, badge, canWrite });
  const has = (id: string) => (items as string[]).includes(id);
  const viewer = <>
    {model ? (
      <UnifiedViewer sourceKey={`${modelKey(model)}:${model.glb_sha256 ?? ''}:${model.simplified_glb_sha256 ?? ''}`} resetKey={`geometryFull:${modelKey(model)}`}
        glbUrl={modelGlbUrl(model, canDownload)} toolbarSpec={fullToolsOpen(viewOf(model, canDownload)) ? 'full' : 'simplified'} tree={assembly}
        selectedKey={selectedKey} onSelectedKeyChange={setSelectedKey} contextLostText={tx('contextLost')}
        simplifiedText={tx('simplifiedLine')} simplifiedTip={tx('simplifiedLineTip')} noViewText={tx('noView')}
        onLoaded={({ groups }) => setNodes(groups)} hostExtras={badge === 'failed' && data?.rebuild.message ? (
          <Alert severity="error" sx={{ position:'absolute', left:10, right:10, bottom:10, fontSize:11.5, zIndex:2, maxHeight:130, overflow:'auto' }}>
            {data.rebuild.message}
          </Alert>) : null} />
    ) : (
      <Typography sx={{ position:'absolute', top:'45%', left:0, right:0, textAlign:'center', fontSize:13, color:'var(--text-3)' }} data-testid="full-no-model">
        {models === null ? tx('filesWorking') : tx('fullNoModel')}
      </Typography>
    )}
    {(treeLoading && !tree) && <Box sx={{ position:'absolute', top:8, left:8, zIndex:5 }}><CircularProgress size={14} /></Box>}
    {(fullToolsOpen(viewOf(model, canDownload)) && assembly.length === 0) && <Typography sx={{ position:'absolute', top:10, left:12, zIndex:5, fontSize:12, color:'var(--text-3)' }}>{tx('fullNoTree')}</Typography>}
  </>;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }} data-testid="geometry-full">
      {/* Model status and rebuild action stay in the Full host toolbar. */}
      <Box data-testid="geometry-full-toolbar" sx={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 1, px: 1.25, py: 0.5, flexShrink: 0,
        borderBottom: '1px solid var(--line-soft)', bgcolor: 'var(--panel)' }}>
        {has('badge') && (
          <Chip data-testid="model-badge" icon={badge === 'failed' || badge === 'stale' ? undefined : <CircularProgress size={12} />}
            color={badge === 'failed' ? 'error' : 'warning'} size="small" label={badgeText} />
        )}
        {has('rebuildNow') && (
          <Button size="small" variant="outlined" onClick={() => void requestRebuild()} data-testid="cad-rebuild-now"
            sx={{ textTransform: 'none', fontSize: 12 }}>{tx('rebuildNow')}</Button>
        )}
        <Box sx={{ flex: 1 }} />
        {data?.model?.template && badge === 'none' && (
          <Typography sx={{ fontSize: 10.5, color: 'var(--text-4)', display: { xs: 'none', sm: 'block' } }}>
            {tx('modelVersion', { template: data.model.template, built: (data.model.built_at ?? '').slice(0, 10) })}
          </Typography>
        )}
      </Box>

      <GeometryLayout emContent={emContent} cadContent={leftBody} viewer={viewer} panelWidth={panelWidth} onDividerMouseDown={onDividerMouseDown} />
    </Box>
  );
};

export default GeometryFullPanel;
