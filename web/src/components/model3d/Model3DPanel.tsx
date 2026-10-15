// The "3D model" tab: look at, rotate, section and download the 3D model of a granted motor.
//
// One model for everybody (owner 2026-10-13): every signed-in user who may see the motor can look at it and rotate it.  The
// tools (parts, exploded view, section, measure) and the STEP download come together with the NDA: ONE flag, the server's
// `can_download` (403 on the STEP without it), decides what this panel shows (lib/model3d.ts panelParts).  Without NDA:
// the model, the motor picker, Fit, the "Download after NDA" note and the Sign button.  No bill of materials, no prices.
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Box, Button, CircularProgress, FormControlLabel, IconButton, MenuItem, Popover, Select, Slider, Switch,
  ToggleButton, ToggleButtonGroup, Tooltip, Typography,
} from '@mui/material';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import DownloadIcon from '@mui/icons-material/Download';
import CenterFocusStrongIcon from '@mui/icons-material/CenterFocusStrong';
import { useTranslation } from 'react-i18next';
import HelpTip from '../common/HelpTip';
import { useLastMotorState } from '../../lib/lastMotor';
import NdaDialog from './NdaDialog';
import VerificationStatus from './VerificationStatus';
import { lockedKind, parseAccess, type AccessInfo } from '../../lib/verification';
import ComponentTreeView from '../viewer3d/ComponentTreeView';
import { useViewportWidth } from '../common/useNarrow';
import { model3dToolbar, treeStartsCollapsed, type Model3DItem } from '../../lib/viewerToolbar';
import { useAssemblyTree } from '../common/useAssemblyTree';
import ModelCanvas, { modelBase as base, responseDetail as detail, type CanvasTools, type ModelCanvasHandle } from './ModelCanvas';
import { usePartTreeData } from '../geometry/useConstruction';
import {
  filenameFromDisposition, formatBytes, formatMm, modelKey, modelLabel, resolveModel,
  type Axis, type ModelInfo, type PartGroup,
} from '../../lib/model3d';

const API = ((import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string).replace(/\/$/, '');
const REMEMBER = 'model3d.last';

const lbl = { fontSize: 11, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase' } as const;
const rd = (k: string): string | null => { try { return localStorage.getItem(k); } catch { return null; } };
const wr = (k: string, v: string): void => { try { localStorage.setItem(k, v); } catch { /* private window */ } };

const Model3DPanel: React.FC = () => {
  // `ready` = the lazy namespace has arrived: until then t() would print the raw key ("downloadLocked", "nda.sign").
  const { t, ready } = useTranslation('model3d');
  const { selection: openMotor } = useLastMotorState();
  const canvas = useRef<ModelCanvasHandle>(null);
  const [models, setModels] = useState<ModelInfo[] | null>(null);
  const [canDownload, setCanDownload] = useState(false);
  // the applicant verification behind it: signing the NDA opens a case, the tools open when it is VERIFIED (server `access`)
  const [access, setAccess] = useState<AccessInfo>({ state: 'nda', nda_signed: false });
  const [reload, setReload] = useState(0);
  const [ndaOpen, setNdaOpen] = useState(false);
  const [hasPdf, setHasPdf] = useState(false);
  const [key, setKey] = useState<string>('');
  // the motor open in Configure has no built model (only some motors have one yet)
  const [preparing, setPreparing] = useState(false);
  const [nodes, setNodes] = useState<PartGroup[]>([]);
  const [explode, setExplode] = useState(0);
  const [sec, setSec] = useState<{ on: boolean; axis: Axis; position: number }>({ on: false, axis: 'x', position: 0 });
  const [range, setRange] = useState({ min: -1, max: 1 });
  const [measure, setMeasure] = useState(false);
  const [mm, setMm] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [dl, setDl] = useState<'idle' | 'busy'>('idle');

  const model = useMemo(() => models?.find((m) => modelKey(m) === key) ?? null, [models, key]);

  // the list of models this account may open
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const r = await fetch(`${API}/api/model3d/models`);
        if (!r.ok) { if (live) setErr(await detail(r)); return; }
        const j = await r.json();
        if (!live) return;
        setCanDownload(j.can_download === true);
        setAccess(parseAccess(j.access, j.can_download === true));
        setModels(j.models as ModelInfo[]);
      } catch (e: any) { if (live) setErr(e?.message ?? String(e)); }
    })();
    return () => { live = false; };
  }, [reload]);

  // what the tab opens on: the motor open in Configure, until the person picks one here himself
  const pickedHere = useRef(false);
  useEffect(() => {
    if (!models || pickedHere.current) return;
    const pick = resolveModel(models, rd(REMEMBER), openMotor);
    setKey(pick.model ? modelKey(pick.model) : '');
    setPreparing(pick.preparing);
  }, [models, openMotor?.die, openMotor?.config]); // eslint-disable-line react-hooks/exhaustive-deps

  // does this account have a signed NDA PDF on file ("Your signed NDA")
  const loadMine = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/nda/mine`);
      if (r.ok) setHasPdf(((await r.json()) as { has_pdf?: boolean }).has_pdf === true);
    } catch { /* the link just stays hidden */ }
  }, []);
  useEffect(() => { void loadMine(); }, [loadMine]);

  // THE part tree of the open motor (names only, after the NDA) - the same tree as Geometry > Full and Materials > Full
  const { tree } = usePartTreeData(model?.die ?? null, model?.config ?? null, 'model3d', !!model && canDownload, model?.glb_sha256 ?? 0);
  // THE tree: the "Motor Assembly" panel over the viewer (eye toggles, swatches, Coil n / Magnet n N|S ...), only after the NDA
  const { model: assembly, hiddenNodes, highlight } = useAssemblyTree(tree, nodes, { resetKey: model ? modelKey(model) : '' });

  const tools: CanvasTools = useMemo(() => ({ hidden: new Set<string>(), explode, section: sec, measure }), [explode, sec, measure]);

  const vw = useViewportWidth();
  const [infoAnchor, setInfoAnchor] = useState<HTMLElement | null>(null);

  const setAxis = useCallback((axis: Axis) => {
    const rg = canvas.current?.sectionRange(axis) ?? range;
    setRange(rg);
    setSec((s) => ({ ...s, axis, position: (rg.min + rg.max) / 2 }));
  }, [range]);

  const download = async (file = 'model.step') => {
    if (!model) return;
    setDl('busy'); setErr(null);
    try {
      const r = await fetch(`${base(model)}/${file}`);
      if (!r.ok) { setErr(await detail(r)); return; }
      const name = filenameFromDisposition(r.headers.get('Content-Disposition'), `${model.motor_id}${file === 'simplified.step' ? '_simplified' : ''}.step`);
      const href = URL.createObjectURL(await r.blob());
      const a = document.createElement('a');
      a.href = href; a.download = name;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(href), 10_000);
    } catch (e: any) { setErr(e?.message ?? String(e)); } finally { setDl('idle'); }
  };

  const downloadPdf = async () => {
    try {
      const r = await fetch(`${API}/api/nda/mine/pdf`);
      if (!r.ok) { setErr(await detail(r)); return; }
      const href = URL.createObjectURL(await r.blob());
      const a = document.createElement('a');
      a.href = href; a.download = filenameFromDisposition(r.headers.get('Content-Disposition'), 'NDA.pdf');
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(href), 10_000);
    } catch (e: any) { setErr(e?.message ?? String(e)); }
  };

  const picker = (
    <Select size="small" fullWidth value={key} displayEmpty disabled={!models?.length}
      onChange={(e) => { pickedHere.current = true; setKey(String(e.target.value)); setPreparing(false); wr(REMEMBER, String(e.target.value)); }}
      renderValue={(v) => (v ? modelLabel(models?.find((m) => modelKey(m) === v) ?? { die: '', config: '' }) : (openMotor ? `${openMotor.die} · ${openMotor.config}` : ''))}
      sx={{ fontSize: 13 }}>
      {(models ?? []).map((m) => <MenuItem key={modelKey(m)} value={modelKey(m)} sx={{ fontSize: 13 }}>{modelLabel(m)}</MenuItem>)}
    </Select>
  );

  const entries = model3dToolbar({ canDownload, models: models?.length ?? 0, preparing, stepAvailable: !!model?.step_available, hasPdf, simplified: !!model?.simplified_available,
    ndaSigned: lockedKind(access) === 'verification' });
  const has = (id: Model3DItem) => entries.some((e) => e.id === id);
  const enabled = (id: Model3DItem) => entries.find((e) => e.id === id)?.enabled !== false;
  const inline = { display: 'flex', alignItems: 'center', gap: 0.75 } as const;
  const lblInline = { ...lbl, fontSize: 10.5, whiteSpace: 'nowrap' } as const;
  const sw = { m: 0, '& .MuiFormControlLabel-label': { ...lblInline } } as const;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <NdaDialog open={ndaOpen} onClose={() => setNdaOpen(false)} onSigned={() => { setReload((n) => n + 1); void loadMine(); }} />

      {/* ONE compact toolbar on top (it wraps on a narrow screen); the viewer below takes the whole width and height */}
      {ready && (
        <Box data-testid="model3d-toolbar" sx={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 1.25, px: 1.25, py: 0.5, flexShrink: 0,
          borderBottom: '1px solid var(--line-soft)', bgcolor: 'var(--panel)' }}>
          {has('motor') && <Box sx={{ flex: '0 1 250px', minWidth: 170 }}>{picker}</Box>}
          {has('fit') && (
            <Button size="small" onClick={() => canvas.current?.fit()} startIcon={<CenterFocusStrongIcon sx={{ fontSize: 16 }} />}
              sx={{ textTransform: 'none', fontSize: 12, color: 'var(--text-2)' }}>{t('fit')}</Button>
          )}
          {has('explode') && (
            <Box sx={inline} data-testid="tool-explode">
              <Typography sx={lblInline}>{t('explode')}</Typography>
              <Slider size="small" min={0} max={1} step={0.01} value={explode} onChange={(_, v) => setExplode(v as number)} sx={{ width: 90 }} />
            </Box>
          )}
          {has('section') && (
            <Box sx={inline} data-testid="tool-section">
              <FormControlLabel sx={sw} labelPlacement="start" label={t('section')}
                control={<Switch size="small" checked={sec.on} onChange={(_, on) => setSec((s) => ({ ...s, on }))} />} />
              {sec.on && (
                <>
                  <ToggleButtonGroup size="small" exclusive value={sec.axis} onChange={(_, a) => a && setAxis(a as Axis)}>
                    {(['x', 'y', 'z'] as const).map((a) => <ToggleButton key={a} value={a} sx={{ px: 1, py: 0, fontSize: 11 }}>{a.toUpperCase()}</ToggleButton>)}
                  </ToggleButtonGroup>
                  <Slider size="small" min={range.min} max={range.max} step={(range.max - range.min) / 400}
                    value={sec.position} onChange={(_, v) => setSec((s) => ({ ...s, position: v as number }))} sx={{ width: 90 }} />
                </>
              )}
            </Box>
          )}
          {has('measure') && (
            <Box sx={inline} data-testid="tool-measure">
              <FormControlLabel sx={sw} labelPlacement="start" label={t('measure')}
                control={<Switch size="small" checked={measure} onChange={(_, on) => { setMeasure(on); if (!on) setMm(null); }} />} />
              {measure && (
                <Typography sx={{ fontSize: 12, color: 'var(--text-1)', fontWeight: 700 }}>
                  {mm == null ? t('measureHint') : formatMm(mm)}
                </Typography>
              )}
            </Box>
          )}
          {has('download') && (
            <Button size="small" variant="contained" disabled={!enabled('download') || dl === 'busy'} data-testid="tool-download"
              startIcon={dl === 'busy' ? <CircularProgress size={14} /> : <DownloadIcon sx={{ fontSize: 16 }} />}
              onClick={() => void download()} sx={{ textTransform: 'none', fontSize: 12.5 }}>
              {t('downloadStep')}{model?.step_bytes ? ` (${formatBytes(model.step_bytes)})` : ''}
            </Button>
          )}
          {has('simplified') && (
            <Tooltip title={t('downloadSimplifiedTip')}>
              <span>
                <Button size="small" variant="outlined" disabled={!enabled('simplified') || dl === 'busy'} data-testid="tool-download-simplified"
                  startIcon={<DownloadIcon sx={{ fontSize: 16 }} />} onClick={() => void download('simplified.step')}
                  sx={{ textTransform: 'none', fontSize: 12.5 }}>
                  {t('downloadSimplified')}{model?.simplified_step_bytes ? ` (${formatBytes(model.simplified_step_bytes)})` : ''}
                </Button>
              </span>
            </Tooltip>
          )}
          {has('yourNda') && (
            <Button size="small" onClick={() => void downloadPdf()} sx={{ textTransform: 'none', fontSize: 12, color: 'var(--text-2)' }}>
              {t('nda.yourNda')}
            </Button>
          )}
          {has('ndaNote') && (
            <Box sx={inline}>
              <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('downloadLocked')}</Typography>
              <HelpTip title={t('downloadLockedHelp')} />
            </Box>
          )}
          {has('verify') && <VerificationStatus access={access} />}
          {has('sign') && (
            <Button size="small" variant="contained" onClick={() => setNdaOpen(true)} sx={{ textTransform: 'none', fontSize: 12.5 }}>
              {t('nda.sign')}
            </Button>
          )}
          {models && !models.length && <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('none')}</Typography>}
          <Box sx={{ flex: 1 }} />
          {has('info') && (
            <>
              <Tooltip title={t('infoTip')}>
                <IconButton size="small" data-testid="tool-info" onClick={(e) => setInfoAnchor(e.currentTarget)} aria-label={t('infoTip')}>
                  <InfoOutlinedIcon sx={{ fontSize: 18 }} />
                </IconButton>
              </Tooltip>
              <Popover open={!!infoAnchor} anchorEl={infoAnchor} onClose={() => setInfoAnchor(null)}
                anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }} transformOrigin={{ vertical: 'top', horizontal: 'right' }}>
                <Box sx={{ p: 1.5, maxWidth: 320, display: 'flex', flexDirection: 'column', gap: 0.75 }}>
                  {model?.version && (
                    <Typography sx={{ fontSize: 11.5, color: 'var(--text-2)' }}>
                      {t('version', { template: model.version.template ?? '?', built: (model.version.built_at ?? '').slice(0, 10), hash: model.version.param_hash ?? '' })}
                    </Typography>
                  )}
                  <Typography sx={{ fontSize: 11.5, color: 'var(--text-3)' }}>{t('stamped')}</Typography>
                </Box>
              </Popover>
            </>
          )}
        </Box>
      )}

      <Box sx={{ position: 'relative', flex: 1, minHeight: 0, minWidth: 0 }}>
        <ModelCanvas ref={canvas} model={model} tools={tools} canUseTools={canDownload} highlight={highlight} hiddenNodes={hiddenNodes}
          contextLostText={t('contextLost')} onMeasure={setMm}
          onLoaded={({ groups, range: rg }) => {
            setNodes(groups); setExplode(0); setMeasure(false); setMm(null);
            setRange(rg);
            setSec({ on: false, axis: 'x', position: (rg.min + rg.max) / 2 });
          }} />
        {ready && canDownload && assembly.rows.length > 0 && (
          <ComponentTreeView model={assembly} defaultCollapsed={treeStartsCollapsed(vw)} style={{ top: 8, left: 8, zIndex: 4, width: 230 }}
            rowsMaxHeight="calc(100% - 40px)" />
        )}
        {err && <Alert severity="error" sx={{ position: 'absolute', bottom: 8, left: 8, right: 8, fontSize: 12, zIndex: 3 }} onClose={() => setErr(null)}>{err}</Alert>}
        {ready && preparing && !model && (
          <Typography sx={{ position: 'absolute', top: '45%', left: 0, right: 0, textAlign: 'center', fontSize: 13, color: 'var(--text-3)' }}>{t('inPreparation')}</Typography>
        )}
      </Box>
    </Box>
  );
};

export default Model3DPanel;
