// The "3D model" tab: look at, rotate, section and download the 3D model of a granted motor.
//
// One model for everybody (owner 2026-10-13): every signed-in user who may see the motor can look at it and rotate it.  The
// tools (parts, exploded view, section, measure) and the STEP download come together with the NDA: ONE flag, the server's
// `can_download` (403 on the STEP without it), decides what this panel shows (lib/model3d.ts panelParts).  Without NDA:
// the model, the motor picker, Fit, the "Download after NDA" note and the Sign button.  No bill of materials, no prices.
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Box, Button, Checkbox, CircularProgress, FormControlLabel, MenuItem, Select, Slider, Switch,
  ToggleButton, ToggleButtonGroup, Typography,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import CenterFocusStrongIcon from '@mui/icons-material/CenterFocusStrong';
import { useTranslation } from 'react-i18next';
import HelpTip from '../common/HelpTip';
import NdaDialog from './NdaDialog';
import { ModelViewer } from './modelViewer';
import {
  effectiveSettings, filenameFromDisposition, formatBytes, formatMm, modelKey, modelLabel, panelParts, pickModel, togglesOf,
  type Axis, type ModelInfo, type PartGroup,
} from '../../lib/model3d';

const API = ((import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string).replace(/\/$/, '');
const REMEMBER = 'model3d.last';

const lbl = { fontSize: 11, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase' } as const;
const rd = (k: string): string | null => { try { return localStorage.getItem(k); } catch { return null; } };
const wr = (k: string, v: string): void => { try { localStorage.setItem(k, v); } catch { /* private window */ } };

const base = (m: Pick<ModelInfo, 'die' | 'config'>) =>
  `${API}/api/model3d/${encodeURIComponent(m.die)}/${encodeURIComponent(m.config)}`;

async function detail(r: Response): Promise<string> {
  try { const j = await r.json(); return typeof j?.detail === 'string' ? j.detail : JSON.stringify(j?.detail ?? j); } catch { return String(r.status); }
}

const Model3DPanel: React.FC = () => {
  const { t } = useTranslation('model3d');
  const host = useRef<HTMLDivElement>(null);
  const viewer = useRef<ModelViewer | null>(null);
  const tRef = useRef(t);
  tRef.current = t;
  const [models, setModels] = useState<ModelInfo[] | null>(null);
  const [canDownload, setCanDownload] = useState(false);
  const [ndaOpen, setNdaOpen] = useState(false);
  const [hasPdf, setHasPdf] = useState(false);
  const [key, setKey] = useState<string>('');
  const [groups, setGroups] = useState<PartGroup[]>([]);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());
  const [explode, setExplode] = useState(0);
  const [sec, setSec] = useState<{ on: boolean; axis: Axis; position: number }>({ on: false, axis: 'x', position: 0 });
  const [range, setRange] = useState({ min: -1, max: 1 });
  const [measure, setMeasure] = useState(false);
  const [mm, setMm] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
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
        setModels(j.models as ModelInfo[]);
        const pick = pickModel(j.models as ModelInfo[], rd(REMEMBER));
        setKey(pick ? modelKey(pick) : '');
      } catch (e: any) { if (live) setErr(e?.message ?? String(e)); }
    })();
    return () => { live = false; };
  }, []);

  // does this account have a signed NDA PDF on file ("Your signed NDA")
  const loadMine = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/nda/mine`);
      if (r.ok) setHasPdf(((await r.json()) as { has_pdf?: boolean }).has_pdf === true);
    } catch { /* the link just stays hidden */ }
  }, []);
  useEffect(() => { void loadMine(); }, [loadMine]);

  // the viewer lives as long as the panel
  useEffect(() => {
    if (!host.current) return undefined;
    const v = new ModelViewer(host.current);
    v.onMeasure = setMm;
    v.onContextLost = () => setErr(tRef.current('contextLost'));
    viewer.current = v;
    return () => { v.dispose(); viewer.current = null; };
  }, []);

  // load the chosen model
  useEffect(() => {
    const v = viewer.current;
    if (!model || !v) return undefined;
    let live = true;
    setLoading(true); setErr(null);
    (async () => {
      try {
        const r = await fetch(`${base(model)}/model.glb`);
        if (!r.ok) { if (live) setErr(await detail(r)); return; }
        const { groups: gs } = await v.load(await r.arrayBuffer());
        if (!live) return;
        setGroups(gs); setHidden(new Set()); setExplode(0); setMeasure(false); setMm(null);
        const rg = v.sectionRange('x');
        setRange(rg);
        setSec({ on: false, axis: 'x', position: (rg.min + rg.max) / 2 });
      } catch (e: any) { if (live) setErr(e?.message ?? String(e)); } finally { if (live) setLoading(false); }
    })();
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model]);

  // push the controls into the scene
  useEffect(() => {
    viewer.current?.apply(effectiveSettings(canDownload, { hidden, explode, section: sec, measure }));
  }, [hidden, explode, sec, measure, groups, canDownload]);

  const show = useMemo(() => new Set(panelParts(canDownload)), [canDownload]);

  const toggles = useMemo(() => togglesOf(groups), [groups]);
  const setAxis = useCallback((axis: Axis) => {
    const rg = viewer.current?.sectionRange(axis) ?? range;
    setRange(rg);
    setSec((s) => ({ ...s, axis, position: (rg.min + rg.max) / 2 }));
  }, [range]);

  const download = async () => {
    if (!model) return;
    setDl('busy'); setErr(null);
    try {
      const r = await fetch(`${base(model)}/model.step`);
      if (!r.ok) { setErr(await detail(r)); return; }
      const name = filenameFromDisposition(r.headers.get('Content-Disposition'), `${model.motor_id}.step`);
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
      onChange={(e) => { setKey(String(e.target.value)); wr(REMEMBER, String(e.target.value)); }} sx={{ fontSize: 13 }}>
      {(models ?? []).map((m) => <MenuItem key={modelKey(m)} value={modelKey(m)} sx={{ fontSize: 13 }}>{modelLabel(m)}</MenuItem>)}
    </Select>
  );

  return (
    <Box sx={{ display: 'flex', height: '100%', minHeight: 0, gap: 0 }}>
      <NdaDialog open={ndaOpen} onClose={() => setNdaOpen(false)} onSigned={() => { setCanDownload(true); void loadMine(); }} />
      {show.has('parts') && (
      <Box sx={{ width: 290, flexShrink: 0, p: 1.5, overflowY: 'auto', borderRight: '1px solid var(--line-soft)', bgcolor: 'var(--panel)',
        display: 'flex', flexDirection: 'column', gap: 1.25 }}>
        <Box>
          <Typography sx={lbl}>{t('motor')}</Typography>
          {picker}
          {models && !models.length && <Typography sx={{ fontSize: 12, color: 'var(--text-3)', mt: 0.5 }}>{t('none')}</Typography>}
        </Box>

        <Box>
          <Typography sx={lbl}>{t('parts')}</Typography>
          <Box sx={{ display: 'flex', flexDirection: 'column' }}>
            {toggles.map((row) => (
              <FormControlLabel key={row.family} sx={{ m: 0, '& .MuiFormControlLabel-label': { fontSize: 12.5 } }}
                control={<Checkbox size="small" sx={{ p: 0.5 }} checked={!hidden.has(row.family)}
                  onChange={(_, on) => setHidden((h) => { const n = new Set(h); if (on) n.delete(row.family); else n.add(row.family); return n; })} />}
                label={t(`part.${row.family}`, { defaultValue: row.family.replace(/_/g, ' ') })} />
            ))}
          </Box>
        </Box>

        <Box>
          <Typography sx={lbl}>{t('explode')}</Typography>
          <Slider size="small" min={0} max={1} step={0.01} value={explode} onChange={(_, v) => setExplode(v as number)} />
        </Box>

        <Box>
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <Typography sx={lbl}>{t('section')}</Typography>
            <Switch size="small" checked={sec.on} onChange={(_, on) => setSec((s) => ({ ...s, on }))} />
          </Box>
          <ToggleButtonGroup size="small" exclusive value={sec.axis} disabled={!sec.on}
            onChange={(_, a) => a && setAxis(a as Axis)} sx={{ mb: 0.5 }}>
            {(['x', 'y', 'z'] as const).map((a) => <ToggleButton key={a} value={a} sx={{ px: 1.25, py: 0.1, fontSize: 12 }}>{a.toUpperCase()}</ToggleButton>)}
          </ToggleButtonGroup>
          <Slider size="small" disabled={!sec.on} min={range.min} max={range.max} step={(range.max - range.min) / 400}
            value={sec.position} onChange={(_, v) => setSec((s) => ({ ...s, position: v as number }))} />
        </Box>

        <Box>
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <Typography sx={lbl}>{t('measure')}</Typography>
            <Switch size="small" checked={measure} onChange={(_, on) => { setMeasure(on); if (!on) setMm(null); }} />
          </Box>
          {measure && (
            <Typography sx={{ fontSize: 12, color: 'var(--text-1)', fontWeight: 700 }}>
              {mm == null ? t('measureHint') : formatMm(mm)}
            </Typography>
          )}
        </Box>

        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75, mt: 'auto' }}>
          <Button size="small" variant="contained" disabled={!model?.step_available || dl === 'busy'}
            startIcon={dl === 'busy' ? <CircularProgress size={14} /> : <DownloadIcon sx={{ fontSize: 16 }} />}
            onClick={() => void download()} sx={{ textTransform: 'none', fontSize: 12.5 }}>
            {t('downloadStep')}{model?.step_bytes ? ` (${formatBytes(model.step_bytes)})` : ''}
          </Button>
          {hasPdf && (
            <Button size="small" onClick={() => void downloadPdf()} sx={{ textTransform: 'none', fontSize: 12, color: 'var(--text-2)' }}>
              {t('nda.yourNda')}
            </Button>
          )}
          <Button size="small" onClick={() => viewer.current?.fit()} startIcon={<CenterFocusStrongIcon sx={{ fontSize: 16 }} />}
            sx={{ textTransform: 'none', fontSize: 12, color: 'var(--text-2)' }}>{t('fit')}</Button>
          {model?.version && (
            <Typography sx={{ fontSize: 10.5, color: 'var(--text-4)' }}>
              {t('version', { template: model.version.template ?? '?', built: (model.version.built_at ?? '').slice(0, 10), hash: model.version.param_hash ?? '' })}
            </Typography>
          )}
          <Typography sx={{ fontSize: 10.5, color: 'var(--text-4)' }}>{t('stamped')}</Typography>
        </Box>
      </Box>
      )}

      <Box sx={{ position: 'relative', flex: 1, minWidth: 0 }}>
        <Box ref={host} sx={{ position: 'absolute', inset: 0 }} />
        {!show.has('parts') && (
          <>
            <Box sx={{ position: 'absolute', top: 10, left: 10, display: 'flex', alignItems: 'center', gap: 1, zIndex: 1 }}>
              {models && models.length > 1 && <Box sx={{ minWidth: 220, bgcolor: 'var(--panel)', borderRadius: 1 }}>{picker}</Box>}
              <Button size="small" onClick={() => viewer.current?.fit()} startIcon={<CenterFocusStrongIcon sx={{ fontSize: 16 }} />}
                sx={{ textTransform: 'none', fontSize: 12, color: 'var(--text-2)', bgcolor: 'var(--panel)' }}>{t('fit')}</Button>
            </Box>
            {models && !models.length && <Typography sx={{ position: 'absolute', top: 54, left: 12, fontSize: 12, color: 'var(--text-3)' }}>{t('none')}</Typography>}
            <Box sx={{ position: 'absolute', left: 10, bottom: 10, display: 'flex', alignItems: 'center', gap: 1, p: 0.75, borderRadius: 1,
              bgcolor: 'var(--panel)', zIndex: 1 }}>
              <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('downloadLocked')}</Typography>
              <HelpTip title={t('downloadLockedHelp')} />
              <Button size="small" variant="contained" onClick={() => setNdaOpen(true)} sx={{ textTransform: 'none', fontSize: 12.5 }}>
                {t('nda.sign')}
              </Button>
            </Box>
          </>
        )}
        {loading && <Box sx={{ position: 'absolute', top: 12, left: 12 }}><CircularProgress size={22} /></Box>}
        {err && <Alert severity="error" sx={{ position: 'absolute', top: 8, left: 8, right: 8, fontSize: 12 }} onClose={() => setErr(null)}>{err}</Alert>}
      </Box>
    </Box>
  );
};

export default Model3DPanel;
