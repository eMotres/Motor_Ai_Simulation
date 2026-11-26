/**
 * The "3D" tab of a production (owner 2026-11-25): the motor model lives in ONE place.  The whole model of this die / configuration
 * in the standard viewer, optionally narrowed to one assembly step (its parts glow, earlier ones stay faint, later ones are hidden) or
 * to one part (opened from a BOM row or a part card).  The access gate is the server's: the model route answers 403 without the NDA,
 * and /api/model3d/models says `can_download` / `view`.
 *
 * `source` is the one place a later second model (e.g. a housing stress / displacement GLB) plugs into: only 'model' exists today.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Box, Button, Chip, CircularProgress, MenuItem, TextField, Typography } from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import { useTranslation } from 'react-i18next';
import HelpTip from '../../../components/common/HelpTip';
import { UnifiedViewer, type PartGroup } from '../../viewer3d';
import { useContentLang } from './BilingualText';
import { downloadModelStep, productionApi } from '../api';
import { displayText, modelRevisionLabel, modelViewFamilies } from '../helpers';
import type { AssemblyStep, Part, ProdCtx } from '../types';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const enc = encodeURIComponent;

/** The GLB the viewer loads for each source; one entry today. */
type ModelSource = 'model';
const glbFor = (source: ModelSource, die: string, config: string): string => {
  void source;
  return `${API}/api/model3d/${enc(die)}/${enc(config)}/model.glb`;
};

export interface ModelListEntry {
  die: string; config: string; view?: 'full' | 'simplified' | 'none'; step_available?: boolean;
  version?: { template?: string | null; built_at?: string | null; param_hash?: string };
}
/** What /api/model3d/models said about THIS motor: 'loading', 'none' (no model), or the entry with the caller's download right. */
export type ModelState = 'loading' | 'none' | { entry: ModelListEntry; canDownload: boolean };

export interface Model3DSelection { stepId: string | null; partId: string | null }

export interface Model3DViewProps {
  ctx: ProdCtx;
  steps: AssemblyStep[];
  parts: Part[];
  selection: Model3DSelection;
  onSelect: (s: Model3DSelection) => void;
  state: ModelState;
  onDownload?: () => void;
  downloadError?: string | null;
  source?: ModelSource;
}

/** The presentational tab (no fetching), so a render test can drive every state. */
export const Model3DView: React.FC<Model3DViewProps> = ({ ctx, steps, parts, selection, onSelect, state, onDownload, downloadError, source = 'model' }) => {
  const { t } = useTranslation('production');
  const ui = useContentLang();
  const [loaded, setLoaded] = useState<ReadonlySet<string> | null>(null);
  const onLoaded = useCallback((r: { groups: PartGroup[] }) => setLoaded(new Set(r.groups.map((g) => g.family))), []);
  const part = useMemo(() => parts.find((p) => p.id === selection.partId) ?? null, [parts, selection.partId]);
  // a saved link may name a step that is not loaded yet or is gone: only a loaded step is honoured, anything else reads as All
  const stepId = part ? null : (selection.stepId && steps.some((x) => x.id === selection.stepId) ? selection.stepId : null);
  const view = useMemo(() => modelViewFamilies({ electronics: ctx.productType === 'electronics', steps, stepId, part }),
    [ctx.productType, steps, stepId, part]);
  const stepTitle = (s: AssemblyStep) => `${s.n}. ${displayText(s.title, ui).text}`;

  if (state === 'loading') return <Box sx={{ display: 'flex', gap: 1.5, color: 'var(--text-3)', py: 3 }}><CircularProgress size={18} /><Typography sx={{ fontSize: 13 }}>{t('loading')}</Typography></Box>;
  if (state === 'none') return <Typography sx={{ fontSize: 13.5, color: 'var(--text-1)', py: 1 }} data-testid="model3d-none">{t('tab3d.none')}</Typography>;

  const { entry, canDownload } = state;
  const rev = modelRevisionLabel(entry.version);
  const shown = view.only ?? [];
  const missing = loaded !== null && view.only !== null && !shown.some((f) => loaded.has(f));
  const legendKey = view.legend === 'own' ? 'process.model3dOwn' : view.legend === 'ghost' ? 'process.model3dGhost' : view.legend === 'upTo' ? 'process.model3dUpTo' : null;
  const full = entry.view === undefined || entry.view === 'full';
  const glb = glbFor(source, ctx.die, ctx.config);

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.25 }} data-testid="model3d-tab">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
        <TextField select size="small" label={t('tab3d.step')} value={part ? '' : (stepId ?? '')} sx={{ minWidth: 260, maxWidth: 420 }}
          SelectProps={{ displayEmpty: true, renderValue: (v) => { const hit = v ? steps.find((s) => s.id === v) : undefined; return hit ? stepTitle(hit) : t('tab3d.all'); } }}
          InputLabelProps={{ shrink: true }}
          onChange={(e) => onSelect({ stepId: e.target.value ? String(e.target.value) : null, partId: null })} inputProps={{ 'data-testid': 'model3d-step-select' }}>
          <MenuItem value="">{t('tab3d.all')}</MenuItem>
          {steps.map((s) => <MenuItem key={s.id} value={s.id}>{stepTitle(s)}</MenuItem>)}
        </TextField>
        {legendKey && <HelpTip title={t(legendKey)} />}
        {part && (
          <Chip size="small" color="primary" variant="outlined" data-testid="model3d-part-chip" label={displayText(part.name, ui).text}
            onDelete={() => onSelect({ stepId: null, partId: null })} />
        )}
        {rev.label && (
          <Typography sx={{ fontSize: 13, color: 'var(--text-1)', display: 'inline-flex', alignItems: 'center', gap: 0.5 }} data-testid="model3d-revision">
            {t('tab3d.revision', { rev: rev.label })}{rev.hash && <HelpTip title={t('tab3d.revisionTip', { hash: rev.hash })} />}
          </Typography>
        )}
        {canDownload && entry.step_available !== false && onDownload && (
          <Button size="small" variant="outlined" startIcon={<DownloadIcon sx={{ fontSize: 16 }} />} onClick={onDownload} data-testid="model3d-download" sx={{ textTransform: 'none', ml: 'auto' }}>
            {t('tab3d.download')}
          </Button>
        )}
      </Box>
      {downloadError && <Typography sx={{ fontSize: 12, color: '#f87171' }}>{downloadError}</Typography>}
      {!full ? (
        <Alert severity="warning" sx={{ fontSize: 12.5, alignItems: 'center' }} data-testid="model3d-nda">{t('nda.required')} <HelpTip title={t('nda.requiredTip')} /></Alert>
      ) : (
        <Box sx={{ height: { xs: 420, md: 640 }, border: '1px solid var(--line-soft)', borderRadius: 1.5, overflow: 'hidden', bgcolor: 'var(--panel-2)', position: 'relative' }}>
          {view.noParts ? (
            <Typography sx={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', textAlign: 'center', px: 3, fontSize: 13, color: 'var(--text-2)' }}>
              {t('process.model3dNone')}
            </Typography>
          ) : (
            <>
              <UnifiedViewer sourceKey={`production:${ctx.die}:${ctx.config}`}
                resetKey={`production:${ctx.die}:${ctx.config}:${source}:${part ? `part:${part.id}` : stepId ? `step:${stepId}` : 'all'}:${shown.join('|')}:${(view.highlight ?? []).join('|')}`}
                glbUrl={glb} toolbarSpec="full" tree={null} onlyFamilies={view.only} highlightFamilies={view.highlight}
                defaultExplode={view.explode} onLoaded={onLoaded} contextLostText={t('process.model3dLost')} noViewText="" />
              {missing && (
                <Typography sx={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', textAlign: 'center', px: 3, fontSize: 13, color: 'var(--text-2)', pointerEvents: 'none' }}>
                  {t('process.model3dNone')}
                </Typography>
              )}
            </>
          )}
        </Box>
      )}
    </Box>
  );
};

/** The tab: loads the assembly steps and the model list, then draws the view. */
const Model3DTab: React.FC<{ ctx: ProdCtx; parts: Part[]; selection: Model3DSelection; onSelect: (s: Model3DSelection) => void }> = ({ ctx, parts, selection, onSelect }) => {
  const { t } = useTranslation('production');
  const [steps, setSteps] = useState<AssemblyStep[]>([]);
  const [state, setState] = useState<ModelState>('loading');
  const [dlErr, setDlErr] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    productionApi.assemblyProcess(ctx.die, ctx.config).then((v) => { if (live) setSteps(v.steps); }).catch(() => { /* no steps: only "All" */ });
    return () => { live = false; };
  }, [ctx.die, ctx.config]);

  useEffect(() => {
    let live = true;
    setState('loading');
    (async () => {
      try {
        const r = await fetch(`${API}/api/model3d/models`);
        if (!r.ok) { if (live) setState('none'); return; }
        const j = await r.json() as { can_download?: boolean; models?: ModelListEntry[] };
        const entry = (j.models ?? []).find((m) => m.die === ctx.die && m.config === ctx.config);
        if (live) setState(entry ? { entry, canDownload: j.can_download === true } : 'none');
      } catch { if (live) setState('none'); }
    })();
    return () => { live = false; };
  }, [ctx.die, ctx.config]);

  const download = () => {
    setDlErr(null);
    void downloadModelStep(ctx.die, ctx.config).catch((e) => setDlErr(t('failed', { reason: e instanceof Error ? e.message : String(e) })));
  };
  return <Model3DView ctx={ctx} steps={steps} parts={parts} selection={selection} onSelect={onSelect} state={state} onDownload={download} downloadError={dlErr} />;
};

export default Model3DTab;
