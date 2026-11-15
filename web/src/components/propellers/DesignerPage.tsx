/** Propellers > Designer (owner 2026-11-15): the own-propeller parametric model, its evaluation at the L12 / L20 rated points and the two
 *  objectives side by side.  Everything is a model value (BEMT, estimated polars, not validated) and the page says so.
 *  Evaluation is a deliberate press: every press and every sweep point becomes a run in the Pareto view. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Box, Button, Chip, CircularProgress, MenuItem, Select, TextField, Tooltip, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';

import { frontCost, paretoFront, runLabel, toRun } from '../../lib/propellerDesign';
import type { EvalRecord, RunRecord, Vector } from '../../lib/propellerDesign';
import { oneOf } from '../../lib/viewMemory';
import { useRememberedView } from '../../lib/useRememberedView';
import * as api from '../../lib/propellerDesignApi';
import { CurveChart, Panel, PlanformChart, ProfileChart, ParetoView, ResultTiles, VariablesPanel, ZoneTable } from './DesignerParts';

const MAX_RUNS = 200;

export default function DesignerPage() {
  const { t } = useTranslation('simulation');
  const [diameter, setDiameter] = useRememberedView<string>('propellers.designer.diameter', '10', oneOf(['10', '11', '12', '13']));
  const [blades, setBlades] = useRememberedView<string>('propellers.designer.blades', '2', oneOf(['2', '3']));
  const [op, setOp] = useRememberedView<string>('propellers.designer.op', 'L12', oneOf(['L12', 'L20']));
  const [family, setFamily] = useState<api.Family | null>(null);
  const [vector, setVector] = useState<Vector | null>(null);
  const [geo, setGeo] = useState<api.Geometry | null>(null);
  const [result, setResult] = useState<EvalRecord | null>(null);
  const [curve, setCurve] = useState<api.Curve | null>(null);
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<api.DraftInfo[]>([]);
  const [draftName, setDraftName] = useState('');
  const [sweepVar, setSweepVar] = useState('twist_deg@0.45');
  const [sweepLo, setSweepLo] = useState('');
  const [sweepHi, setSweepHi] = useState('');
  const [sweepN, setSweepN] = useState('5');
  const nextId = useRef(1);
  const lastRunVector = useRef<Vector | null>(null);

  // family (variables, bounds, operating points): once per diameter / blade count; the vector keeps what the user typed
  useEffect(() => {
    const ctl = new AbortController();
    api.getFamily(Number(diameter), Number(blades), ctl.signal)
      .then((f) => {
        setFamily(f); setError(null);
        setVector((v) => (v ? { ...v, diameter_in: Number(diameter), blades: Number(blades) } : { ...f.seed }));
      })
      .catch((e) => { if (e?.name !== 'AbortError') setError(String(e?.message ?? e)); });
    return () => ctl.abort();
  }, [diameter, blades]);

  const refreshDrafts = useCallback(() => { api.listDrafts().then((d) => setDrafts(d.drafts)).catch(() => setDrafts([])); }, []);
  useEffect(() => { refreshDrafts(); }, [refreshDrafts]);

  // the geometry (cheap) follows the vector after a short pause; an invalid vector shows its reasons instead
  useEffect(() => {
    if (!vector) return;
    const h = window.setTimeout(() => {
      api.getGeometry(vector).then((g) => { setGeo(g); setError(null); })
        .catch((e: api.DesignApiError) => {
          setGeo(null);
          setError(e.violations?.length ? t('propInvalid', { rules: e.violations.map((v) => `${v.rule}${v.name ? ' ' + v.name : ''}`).join(', ') }) : t('propError', { error: e.message }));
        });
    }, 400);
    return () => window.clearTimeout(h);
  }, [vector, t]);

  const motor = family?.operating_points[op];
  const motorRadius = motor?.motor_radius_mm ?? 20;
  const numericVars = useMemo(() => (family?.variables ?? []).filter((v) => !v.options).map((v) => v.name), [family]);

  const addRun = useCallback((v: Vector, r: EvalRecord) => {
    const id = nextId.current++;
    const run = toRun(id, runLabel(lastRunVector.current, v), v, r);
    lastRunVector.current = { ...v };
    setRuns((rs) => [...rs, run].slice(-MAX_RUNS));
    setSelected(id);
  }, []);

  const onEvaluate = async () => {
    if (!vector) return;
    setBusy(true);
    try {
      const [r, c] = await Promise.all([api.evaluate(vector, op), api.getCurve(vector, op)]);
      setResult(r); setCurve(c); setError(null); addRun(vector, r);
    } catch (e) {
      const err = e as api.DesignApiError;
      setError(err.violations?.length ? t('propInvalid', { rules: err.violations.map((v) => `${v.rule}${v.name ? ' ' + v.name : ''}`).join(', ') }) : t('propError', { error: err.message }));
    } finally { setBusy(false); }
  };

  const onSweep = async () => {
    if (!vector) return;
    const lo = Number(sweepLo.replace(',', '.')), hi = Number(sweepHi.replace(',', '.')), n = Number(sweepN);
    setBusy(true);
    try {
      const res = await api.sweep(vector, op, sweepVar, lo, hi, n);
      for (const row of res.rows) {
        if (!row.valid || !row.objectives) continue;
        const v = { ...vector, [sweepVar]: row.value };
        const id = nextId.current++;
        setRuns((rs) => [...rs, { id, label: `${sweepVar}=${typeof row.value === 'number' ? Number(row.value.toPrecision(4)) : row.value}`, vector: v, designId: row.design_id ?? '',
          op, rpm: row.rpm ?? 0, thrust_per_shaft_W: row.objectives!.thrust_per_shaft_W, air_ms: row.objectives!.cooling_air_axial_ms,
          constraintsMet: row.constraints_met === true, tipMach: row.tip_mach ?? 0 }].slice(-MAX_RUNS));
      }
      setError(null);
    } catch (e) { setError(t('propError', { error: (e as Error).message })); } finally { setBusy(false); }
  };

  const onChange = (name: string, value: number | string) => setVector((v) => (v ? { ...v, [name]: value } : v));
  const onPick = (r: RunRecord) => {
    setVector({ ...r.vector }); setSelected(r.id);
    if (r.vector.diameter_in != null) setDiameter(String(r.vector.diameter_in));
    if (r.vector.blades != null) setBlades(String(r.vector.blades));
  };

  const saveDraft = async () => {
    if (!vector || !draftName.trim()) return;
    try { await api.putDraft(draftName.trim(), vector, op); refreshDrafts(); setError(null); } catch (e) { setError(t('propError', { error: (e as Error).message })); }
  };
  const openDraft = async (name: string) => {
    try {
      const d = await api.getDraft(name);
      setVector(d.vector); setDraftName(name);
      if (d.operating_point === 'L12' || d.operating_point === 'L20') setOp(d.operating_point);
      if (d.vector.diameter_in != null) setDiameter(String(d.vector.diameter_in));
      if (d.vector.blades != null) setBlades(String(d.vector.blades));
    } catch (e) { setError(t('propError', { error: (e as Error).message })); }
  };
  const removeDraft = async (name: string) => { try { await api.deleteDraft(name); refreshDrafts(); } catch (e) { setError(t('propError', { error: (e as Error).message })); } };

  const front = paretoFront(runs.filter((r) => r.op === op));
  const opRuns = runs.filter((r) => r.op === op);
  const cost = frontCost(opRuns, front);

  if (!family || !vector) return <Box sx={{ p: 2 }}>{error ? <Alert severity="error">{error}</Alert> : <CircularProgress size={20} />}</Box>;

  return (
    <Box sx={{ p: 2, display: 'flex', flexDirection: 'column', gap: 1.25 }} data-testid="prop-designer">
      <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
        <Tooltip title={t('propDesignDiameterTip')}>
          <Select size="small" value={diameter} onChange={(e) => setDiameter(String(e.target.value))} sx={{ fontSize: 13 }} renderValue={(v) => `${t('propDesignDiameter')}: ${v}`} inputProps={{ 'aria-label': t('propDesignDiameter') }}>
            {family.diameters_in.map((d) => <MenuItem key={d} value={String(d)}>{d}</MenuItem>)}
          </Select>
        </Tooltip>
        <Tooltip title={t('propDesignBladesTip')}>
          <Select size="small" value={blades} onChange={(e) => setBlades(String(e.target.value))} sx={{ fontSize: 13 }} renderValue={(v) => `${t('propDesignBlades')}: ${v}`} inputProps={{ 'aria-label': t('propDesignBlades') }}>
            {family.blades_options.map((b) => <MenuItem key={b} value={String(b)}>{b}</MenuItem>)}
          </Select>
        </Tooltip>
        <Tooltip title={t('propDesignMotorTip')}>
          <Select size="small" value={op} onChange={(e) => setOp(String(e.target.value))} sx={{ fontSize: 13 }} renderValue={(v) => `${t('propDesignMotor')}: ${v}`} inputProps={{ 'aria-label': t('propDesignMotor') }}>
            {Object.entries(family.operating_points).map(([k, o]) => <MenuItem key={k} value={k}>{`${o.config} · ${o.torque_limit_Nm} N·m`}</MenuItem>)}
          </Select>
        </Tooltip>
        <Button variant="contained" size="small" onClick={onEvaluate} disabled={busy}>{busy ? t('propDesignEvaluating') : t('propDesignEvaluate')}</Button>
        <Tooltip title={t('propDesignBannerTip')}><Chip size="small" color="warning" label={t('propDesignBanner')} data-testid="prop-banner" /></Tooltip>
      </Box>
      {error && <Alert severity="error" data-testid="prop-error">{error}</Alert>}

      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: 'minmax(0,1fr)', lg: 'minmax(320px, 1fr) minmax(0, 1.4fr) minmax(0, 1.1fr)' }, gap: 1.25, alignItems: 'start' }}>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.25, minWidth: 0 }}>
          <VariablesPanel rows={family.variables} vector={vector} onChange={onChange} onReset={() => setVector({ ...family.seed, diameter_in: Number(diameter), blades: Number(blades) })} />
          <Panel title={t('propSweep')} tip={t('propSweepTip')}>
            <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap', alignItems: 'center' }}>
              <Select size="small" value={sweepVar} onChange={(e) => setSweepVar(String(e.target.value))} sx={{ fontSize: 12, minWidth: 130 }} inputProps={{ 'aria-label': t('propSweepVar') }}>
                {numericVars.map((n) => <MenuItem key={n} value={n} sx={{ fontSize: 12 }}>{n}</MenuItem>)}
              </Select>
              <TextField size="small" label={t('propSweepLo')} value={sweepLo} onChange={(e) => setSweepLo(e.target.value)} sx={{ width: 80 }} />
              <TextField size="small" label={t('propSweepHi')} value={sweepHi} onChange={(e) => setSweepHi(e.target.value)} sx={{ width: 80 }} />
              <TextField size="small" label={t('propSweepN')} value={sweepN} onChange={(e) => setSweepN(e.target.value)} sx={{ width: 64 }} />
              <Button size="small" variant="outlined" onClick={onSweep} disabled={busy || !sweepLo || !sweepHi}>{t('propSweepRun')}</Button>
            </Box>
          </Panel>
          <Panel title={t('propDesignDraftName')} tip={t('propDesignDraftsTip')}>
            <Box sx={{ display: 'flex', gap: 0.75, alignItems: 'center' }}>
              <TextField size="small" value={draftName} onChange={(e) => setDraftName(e.target.value)} sx={{ flex: 1 }} inputProps={{ 'aria-label': t('propDesignDraftName') }} />
              <Button size="small" onClick={saveDraft} disabled={!draftName.trim()}>{t('propDesignDraftSave')}</Button>
            </Box>
            {drafts.length === 0 ? <Typography sx={{ fontSize: 12, color: 'var(--text-3)', mt: 0.5 }}>{t('propDesignDraftNone')}</Typography> : drafts.map((d) => (
              <Box key={d.name} sx={{ display: 'flex', gap: 0.5, alignItems: 'center', mt: 0.25 }}>
                <Typography sx={{ fontSize: 12, flex: 1 }}>{d.name}{d.diameter_in ? ` · ${d.diameter_in}″ · ${d.blades}` : ''}</Typography>
                <Button size="small" onClick={() => openDraft(d.name)}>{t('propDesignDraftOpen')}</Button>
                <Button size="small" color="error" onClick={() => removeDraft(d.name)}>{t('propDesignDraftDelete')}</Button>
              </Box>
            ))}
          </Panel>
        </Box>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.25, minWidth: 0 }}>
          <PlanformChart geo={geo} motorRadiusMm={motorRadius} />
          <ZoneTable geo={geo} />
          <CurveChart c={curve} />
        </Box>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.25, minWidth: 0 }}>
          <ResultTiles r={result} />
          <ProfileChart r={result} motorRadiusMm={motorRadius} />
          <ParetoView runs={opRuns} selectedId={selected} onPick={onPick} onClear={() => { setRuns([]); setSelected(null); lastRunVector.current = null; }} cost={cost} />
        </Box>
      </Box>
    </Box>
  );
}
