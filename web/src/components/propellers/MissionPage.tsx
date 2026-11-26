/** Propellers > Mission (owner 2026-11-26): the best propeller and the motor turns (Kv) for a flight.  Choose a catalogue motor, the battery,
 *  the airframe and the flight, press Run: stage 1 ranks a listed set of propellers (catalogue props as reference rows, the own family on a grid)
 *  with the Kv chosen per propeller, stage 2 refines the best own propeller with the Designer's optimiser against the mission objective.
 *  A run is a job of the server's queue; the page polls its state.  Everything is an estimate and the page says so. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Box, Button, Chip, CircularProgress, LinearProgress, MenuItem, Select, Table, TableBody, TableCell, TableHead, TableRow, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';

import { Panel } from './DesignerParts';
import { BestCard, KvGrid, MCheck, MField, MotorPicker, ObjectiveChart, RefinedCard, ResultTable, RowDetails } from './MissionParts';
import { DEFAULT_FORM, DRAFT_PREFIX, OBJECTIVES, buildBody, draftName, errorKey, motorKey, pickRow, sanitizeForm, serverFieldKey, sortRows, statusKey, validRunId } from '../../lib/propellerMission';
import type { MissionForm, SortKey } from '../../lib/propellerMission';
import type { Vector } from '../../lib/propellerDesign';
import * as api from '../../lib/propellerMissionApi';
import * as design from '../../lib/propellerDesignApi';
import { readView, writeView } from '../../lib/viewMemory';

const FORM_KEY = 'propellers.mission.form';
const RUN_KEY = 'propellers.mission.run';
const ROW_KEY = 'propellers.mission.row';
const CELL_S = { fontSize: 11, py: 0.25, whiteSpace: 'nowrap' } as const;
const ACTIVE = ['queued', 'running'];

export default function MissionPage() {
  const { t } = useTranslation('simulation');
  const [form, setForm] = useState<MissionForm>(() => sanitizeForm(readView<unknown>(FORM_KEY, (v) => (v && typeof v === 'object' ? v : null))));
  const [opts, setOpts] = useState<api.MissionOptions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [run, setRun] = useState<api.MissionRun | null>(null);
  const [runs, setRuns] = useState<api.RunSummary[]>([]);
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' }>({ key: 'objective', dir: 'desc' });
  const [selected, setSelected] = useState<string | null>(() => readView<string>(ROW_KEY, (v) => (typeof v === 'string' ? v : null)));
  const [geo, setGeo] = useState<design.Geometry | null>(null);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const timer = useRef<number | null>(null);
  const seq = useRef(0);                                        // a poll answer of an older request is ignored
  const ctl = useRef<AbortController | null>(null);

  const set = <K extends keyof MissionForm>(k: K, v: MissionForm[K]) => { setForm((f) => ({ ...f, [k]: v })); setFieldErrors((e) => (e[k] ? { ...e, [k]: '' } : e)); };
  useEffect(() => { writeView(FORM_KEY, form); }, [form]);
  useEffect(() => { writeView(ROW_KEY, selected); }, [selected]);

  const showError = useCallback((e: unknown) => {
    const err = e as { status?: number; message?: string };
    const text = String(err?.message ?? e);
    setError(`${t(errorKey(err?.status ?? 0))}${text ? `: ${text}` : ''}`);
    const key = serverFieldKey(text);
    if (key) setFieldErrors((f) => ({ ...f, [key]: 'range' }));
  }, [t]);

  const refreshRuns = useCallback(() => { api.listRuns().then((r) => setRuns(r.runs)).catch(() => setRuns([])); }, []);
  const stopPolling = () => { if (timer.current !== null) { window.clearInterval(timer.current); timer.current = null; } ctl.current?.abort(); };
  useEffect(() => stopPolling, []);

  /** Follow one run: every poll carries the sequence number of the run being followed; an answer for an older one is dropped. */
  const follow = useCallback((id: string, silent = false) => {
    stopPolling();
    const mine = ++seq.current;
    const tick = () => {
      ctl.current?.abort();
      const c = new AbortController();
      ctl.current = c;
      api.getRun(id, c.signal).then((r) => {
        if (mine !== seq.current) return;
        setRun(r);
        writeView(RUN_KEY, id);
        if (!ACTIVE.includes(r.status)) { stopPolling(); refreshRuns(); }
      }).catch((e) => {
        if (mine !== seq.current || e?.name === 'AbortError') return;
        stopPolling();
        if (silent) { writeView(RUN_KEY, null); setRun(null); return; }           // a remembered run that is gone: back to the empty page, no message
        showError(e);
      });
    };
    tick();
    timer.current = window.setInterval(tick, 1500);
  }, [refreshRuns, showError]);

  useEffect(() => {
    const c = new AbortController();
    api.getOptions(c.signal).then((o) => { setOpts(o); setError(null); }).catch((e) => { if (e?.name !== 'AbortError') showError(e); });
    refreshRuns();
    const id = validRunId(readView<string>(RUN_KEY, (v) => (typeof v === 'string' ? v : null)));
    if (id) follow(id, true);
    return () => c.abort();
  }, [refreshRuns, follow, showError]);

  const motor = useMemo(() => opts?.motors.find((m) => motorKey(m) === form.motorKey) ?? null, [opts, form.motorKey]);
  const preset = opts?.batteries[form.chemistry];

  const onRun = async () => {
    if (!opts) return;
    const b = buildBody(form, opts.motors);
    if (!b.ok) { setFieldErrors(b.errors); setError(t('propMissionBadFields', { n: Object.keys(b.errors).length })); return; }
    setFieldErrors({}); setBusy(true); setError(null); setSelected(null); setGeo(null); setSaveMsg(null);
    try {
      const r = await api.startRun(b.body);
      follow(r.run_id);
    } catch (e) { showError(e); } finally { setBusy(false); }
  };
  const onStop = () => { if (run) api.stopRun(run.run_id).catch(showError); };
  const onDelete = async (id: string) => {
    try {
      await api.deleteRun(id);
      if (run?.run_id === id) { stopPolling(); setRun(null); writeView(RUN_KEY, null); setSelected(null); }
      refreshRuns();
    } catch (e) { showError(e); }
  };

  const res = run?.result ?? null;
  const objective = (run?.inputs?.objective as typeof form.objective | undefined) ?? form.objective;
  const rows = useMemo(() => sortRows(res?.results ?? [], sort.key, sort.dir), [res, sort]);
  const best = res?.results.find((r) => r.passed) ?? null;
  const rowId = pickRow(rows, selected);                         // a remembered row the run no longer has: the best row, silently
  const picked = rows.find((r) => r.id === rowId) ?? best;
  const refined = res?.refined?.[0] ?? null;

  useEffect(() => {
    if (!refined?.best_vector) { setGeo(null); return; }
    design.getGeometry(refined.best_vector as Vector).then(setGeo).catch(() => setGeo(null));
  }, [refined]);

  const onSort = (k: SortKey) => setSort((s) => (s.key === k ? { key: k, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key: k, dir: k === 'name' ? 'asc' : 'desc' }));

  const onSaveDraft = async () => {
    if (!refined?.best_vector || !run) return;
    setBusy(true);
    const name = draftName(DRAFT_PREFIX, run.run_id, 1);
    try {
      await design.putDraft(name, refined.best_vector as Vector, 'L12');
      setSaveMsg(t('propMissionSaved', { name }));
    } catch (e) { setSaveMsg(t('propMissionSaveFailed', { error: (e as Error).message })); } finally { setBusy(false); }
  };

  const running = !!run && ACTIVE.includes(run.status);
  const pct = run && run.total ? Math.min(100, (100 * (run.done ?? 0)) / run.total) : 0;
  const fe = (k: keyof MissionForm) => fieldErrors[k] || null;

  if (!opts) return <Box sx={{ p: 2 }}>{error ? <Alert severity="error">{error}</Alert> : <CircularProgress size={20} />}</Box>;

  return (
    <Box sx={{ p: 2, display: 'flex', flexDirection: 'column', gap: 1.25 }} data-testid="prop-mission">
      <Alert severity="info" sx={{ py: 0, fontSize: 12 }} data-testid="mission-note">{t('propMissionNote')}</Alert>
      {error && <Alert severity="error" data-testid="mission-error" onClose={() => setError(null)}>{error}</Alert>}
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: 'minmax(0,1fr)', lg: 'minmax(340px, 0.9fr) minmax(0, 2fr)' }, gap: 1.25, alignItems: 'start' }}>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.25, minWidth: 0 }}>
          <Panel title={t('propMissionMotor')} tip={t('propMissionMotorTip')}>
            <MotorPicker motors={opts.motors} value={form.motorKey} onChange={(k) => set('motorKey', k)} />
            {fe('motorKey') && <Alert severity="error" sx={{ py: 0, mt: 0.5, fontSize: 11 }}>{t('propMissionFieldErr_motor')}</Alert>}
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mt: 1, alignItems: 'flex-start' }}>
              <MField label={t('propMissionRotors')} tip={t('propMissionRotorsTip')} value={form.rotors} onChange={(v) => set('rotors', v)} width={80} error={fe('rotors')} />
              <MField label={t('propMissionBoardCap')} tip={t('propMissionBoardCapTip')} value={form.boardCap} onChange={(v) => set('boardCap', v)} width={140} error={fe('boardCap')} />
              {motor?.drives && motor.drives.length > 1 && (
                <Select size="small" value={form.drive} onChange={(e) => set('drive', String(e.target.value))} sx={{ fontSize: 12 }} renderValue={(v) => `${t('propMissionDrive')}: ${v}`}>
                  {motor.drives.map((d) => <MenuItem key={d} value={d} sx={{ fontSize: 12 }}>{d}</MenuItem>)}
                </Select>
              )}
            </Box>
          </Panel>
          <Panel title={t('propMissionKv')} tip={t('propMissionKvTip')}>
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'flex-start' }}>
              <Select size="small" value={form.kvMode} onChange={(e) => set('kvMode', e.target.value as MissionForm['kvMode'])} sx={{ fontSize: 12 }} renderValue={(v) => t(`propMissionKvMode_${v}`)}>
                {['free', 'as_built'].map((m) => <MenuItem key={m} value={m} sx={{ fontSize: 12 }}>{t(`propMissionKvMode_${m}`)}</MenuItem>)}
              </Select>
              <MField label={t('propMissionHeadroom')} tip={t('propMissionHeadroomTip')} value={form.headroom} onChange={(v) => set('headroom', v)} width={130} error={fe('headroom')} />
              <MCheck label={t('propMissionAllowUnverified')} tip={t('propMissionAllowUnverifiedTip')} checked={form.allowUnverified} onChange={(v) => set('allowUnverified', v)} />
            </Box>
            {form.kvMode === 'free' && (
              <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mt: 1, alignItems: 'flex-start' }}>
                <MField label={t('propMissionTurns0')} tip={t('propMissionTurns0Tip', { model: motor?.winding?.turns ?? '-' })} value={form.turns0} onChange={(v) => set('turns0', v)} width={110} error={fe('turns0')} />
                <MField label={t('propMissionStrip0')} tip={t('propMissionStrip0Tip')} value={form.strip0} onChange={(v) => set('strip0', v)} width={120} error={fe('strip0')} />
                <MField label={t('propMissionStripWidth')} tip={t('propMissionStripWidthTip')} value={form.stripWidth} onChange={(v) => set('stripWidth', v)} width={120} error={fe('stripWidth')} />
                <MField label={t('propMissionTurnsBelow')} tip={t('propMissionTurnsRangeTip')} value={form.turnsBelow} onChange={(v) => set('turnsBelow', v)} width={80} error={fe('turnsBelow')} />
                <MField label={t('propMissionTurnsAbove')} tip={t('propMissionTurnsRangeTip')} value={form.turnsAbove} onChange={(v) => set('turnsAbove', v)} width={80} error={fe('turnsAbove')} />
                <MField label={t('propMissionCoilTol')} tip={t('propMissionCoilTolTip')} value={form.coilTol} onChange={(v) => set('coilTol', v)} width={130} error={fe('coilTol')} />
              </Box>
            )}
          </Panel>
          <Panel title={t('propMissionBattery')} tip={t('propMissionBatteryTip')}>
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'flex-start' }}>
              <Select size="small" value={form.chemistry} onChange={(e) => set('chemistry', String(e.target.value))} sx={{ fontSize: 12 }} renderValue={(v) => opts.batteries[String(v)]?.label ?? String(v)}>
                {Object.entries(opts.batteries).map(([k, b]) => <MenuItem key={k} value={k} sx={{ fontSize: 12 }}>{b.label}</MenuItem>)}
              </Select>
              <MField label={t('propMissionCells')} tip={t('propMissionCellsTip')} value={form.cells} onChange={(v) => set('cells', v)} width={80} error={fe('cells')} />
              <MField label={t('propMissionCapacity')} value={form.capacity} onChange={(v) => set('capacity', v)} width={100} error={fe('capacity')} />
              <MField label={t('propMissionUsable')} tip={t('propMissionUsableTip')} value={form.usable} onChange={(v) => set('usable', v)} width={100} error={fe('usable')} />
            </Box>
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mt: 1, alignItems: 'flex-start' }}>
              <MField label={t('propMissionVMin')} tip={t('propMissionVMinTip', { nom: preset?.v_nom, max: preset?.v_max, min: preset?.v_min })} value={form.vMin} onChange={(v) => set('vMin', v)} width={120} error={fe('vMin')} />
              <MField label={t('propMissionWhKg')} tip={t('propMissionPresetTip', { v: preset?.wh_per_kg })} value={form.whKg} onChange={(v) => set('whKg', v)} width={96} error={fe('whKg')} />
              <MField label={t('propMissionRCell')} tip={t('propMissionRCellTip', { v: preset?.r_cell_ohm_ah })} value={form.rCell} onChange={(v) => set('rCell', v)} width={110} error={fe('rCell')} />
              <MField label={t('propMissionBattMass')} tip={t('propMissionBattMassTip')} value={form.battMass} onChange={(v) => set('battMass', v)} width={120} error={fe('battMass')} />
              <MField label={t('propMissionBattCurrent')} tip={t('propMissionBattCurrentTip')} value={form.battCurrent} onChange={(v) => set('battCurrent', v)} width={140} error={fe('battCurrent')} />
            </Box>
          </Panel>
          <Panel title={t('propMissionAirframe')} tip={t('propMissionAirframeTip')}>
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'flex-start' }}>
              <MField label={t('propMissionDry')} tip={t('propMissionDryTip')} value={form.dryMass} onChange={(v) => set('dryMass', v)} width={110} error={fe('dryMass')} />
              <MField label={t('propMissionPayload')} value={form.payload} onChange={(v) => set('payload', v)} width={100} error={fe('payload')} />
              <MField label={t('propMissionDrag')} tip={t('propMissionDragTip')} value={form.dragArea} onChange={(v) => set('dragArea', v)} width={120} error={fe('dragArea')} />
              <MField label={t('propMissionMaxD')} tip={t('propMissionMaxDTip', { min: form.minD })} value={form.maxD} onChange={(v) => set('maxD', v)} width={120} error={fe('maxD')} />
              <MField label={t('propMissionAirTemp')} value={form.airTemp} onChange={(v) => set('airTemp', v)} width={90} error={fe('airTemp')} />
              <MField label={t('propMissionAltitude')} value={form.altitude} onChange={(v) => set('altitude', v)} width={100} error={fe('altitude')} />
              <MField label={t('propMissionAvionics')} tip={t('propMissionAvionicsTip')} value={form.avionics} onChange={(v) => set('avionics', v)} width={110} error={fe('avionics')} />
            </Box>
          </Panel>
          <Panel title={t('propMissionFlight')} tip={t('propMissionFlightTip')}>
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'flex-start' }}>
              <MField label={t('propMissionSpeed')} tip={t('propMissionSpeedTip')} value={form.speedKmh} onChange={(v) => set('speedKmh', v)} width={120} error={fe('speedKmh')} />
              <MCheck label={t('propMissionBestSpeed')} tip={t('propMissionBestSpeedTip')} checked={form.bestSpeed} onChange={(v) => set('bestSpeed', v)} />
              <MField label={t('propMissionHoverShare')} tip={t('propMissionHoverShareTip')} value={form.hoverShare} onChange={(v) => set('hoverShare', v)} width={140} error={fe('hoverShare')} />
              <MField label={t('propMissionTwCont')} tip={t('propMissionTwContTip')} value={form.twCont} onChange={(v) => set('twCont', v)} width={110} error={fe('twCont')} />
              <MField label={t('propMissionTwPeak')} tip={t('propMissionTwPeakTip')} value={form.twPeak} onChange={(v) => set('twPeak', v)} width={110} error={fe('twPeak')} />
              <MField label={t('propMissionTipMach')} value={form.tipMach} onChange={(v) => set('tipMach', v)} width={110} error={fe('tipMach')} />
              <Select size="small" value={form.enduranceMode} onChange={(e) => set('enduranceMode', e.target.value as MissionForm['enduranceMode'])} sx={{ fontSize: 12 }}
                renderValue={(v) => `${t('propMissionEnduranceMode')}: ${t(`propMissionEnd_${v}`)}`}>
                {['hover', 'best_speed'].map((m) => <MenuItem key={m} value={m} sx={{ fontSize: 12 }}>{t(`propMissionEnd_${m}`)}</MenuItem>)}
              </Select>
            </Box>
          </Panel>
          <Panel title={t('propMissionCandidates')} tip={t('propMissionCandidatesTip')}>
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'flex-start' }}>
              <MCheck label={t('propMissionCatalogue')} tip={t('propMissionCatalogueTip')} checked={form.catalogue} onChange={(v) => set('catalogue', v)} />
              <MCheck label={t('propMissionOwn')} tip={t('propMissionOwnTip')} checked={form.own} onChange={(v) => set('own', v)} />
              <MField label={t('propMissionPdRange')} tip={t('propMissionPdRangeTip')} value={form.pdRange} onChange={(v) => set('pdRange', v)} width={110} error={fe('pdRange')} />
              <MField label={t('propMissionPdStep')} value={form.pdStep} onChange={(v) => set('pdStep', v)} width={90} error={fe('pdStep')} />
              <MField label={t('propMissionChords')} tip={t('propMissionChordsTip')} value={form.chords} onChange={(v) => set('chords', v)} width={120} error={fe('chords')} />
              <MField label={t('propMissionBlades')} tip={t('propMissionBladesTip')} value={form.blades} onChange={(v) => set('blades', v)} width={90} error={fe('blades')} />
            </Box>
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'flex-start', mt: 1 }}>
              <MCheck label={t('propMissionRefine')} tip={t('propMissionRefineTip')} checked={form.refine} onChange={(v) => set('refine', v)} />
              {form.refine && (
                <>
                  <MField label={t('propMissionTopN')} value={form.topN} onChange={(v) => set('topN', v)} width={90} error={fe('topN')} />
                  <MField label={t('propMissionPop')} value={form.pop} onChange={(v) => set('pop', v)} width={100} error={fe('pop')} />
                  <MField label={t('propMissionGen')} value={form.generations} onChange={(v) => set('generations', v)} width={110} error={fe('generations')} />
                  <MField label={t('propMissionSeed')} value={form.seed} onChange={(v) => set('seed', v)} width={90} error={fe('seed')} />
                </>
              )}
            </Box>
          </Panel>
          <Panel title={t('propMissionObjective')} tip={t('propMissionObjectiveTip')}>
            <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
              <Select size="small" value={form.objective} onChange={(e) => set('objective', e.target.value as MissionForm['objective'])} sx={{ fontSize: 12, minWidth: 220 }}
                renderValue={(v) => t(`propMissionObj_${v}`)} inputProps={{ 'aria-label': t('propMissionObjective') }}>
                {OBJECTIVES.map((o) => <MenuItem key={o} value={o} sx={{ fontSize: 12 }}>{t(`propMissionObj_${o}`)}</MenuItem>)}
              </Select>
              <Button variant="contained" size="small" onClick={onRun} disabled={busy || running || !form.motorKey || motor?.kind === 'none'} data-testid="mission-run">{t('propMissionRun')}</Button>
              {running && <Button size="small" color="error" onClick={onStop} data-testid="mission-stop">{t('propMissionStop')}</Button>}
              <Button size="small" onClick={() => { setForm({ ...DEFAULT_FORM, motorKey: form.motorKey }); setFieldErrors({}); }}>{t('propMissionReset')}</Button>
            </Box>
            {running && (
              <Box sx={{ mt: 1 }} data-testid="mission-progress">
                <LinearProgress variant="determinate" value={pct} />
                <KvGrid items={[[t('propMissionColStatus'), t(statusKey(run?.status ?? 'running'))], [t('propMissionDone'), `${run?.done ?? 0} / ${run?.total ?? 0}`],
                  [t('propMissionPosition'), run?.status === 'queued' ? (run as { position?: number }).position ?? null : null]]} />
              </Box>
            )}
          </Panel>
        </Box>

        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.25, minWidth: 0 }}>
          {run && run.status === 'failed' && <Alert severity="error">{t('propMissionRunFailed')}: {run.error}</Alert>}
          {run && run.status === 'interrupted' && <Alert severity="warning">{t('propMissionInterrupted')}</Alert>}
          {run && run.status === 'stopped' && !res && <Alert severity="info">{t('propMissionStoppedEarly')}</Alert>}
          {res && run && (
            <>
              <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }} data-testid="mission-motor-used">
                <Chip size="small" color={run.motor.kind === 'estimate' ? 'warning' : 'success'} variant="outlined" label={`${run.motor.name} · ${t(`propMissionKind_${run.motor.kind}`)}`} />
                <Chip size="small" variant="outlined" label={t(statusKey(run.status))} />
                {res.stopped && <Chip size="small" color="warning" label={`${t('propMissionStopped')}${res.complete ? '' : `: ${t(`propMissionStage_${res.stopped_stage ?? 'stage1'}`, { defaultValue: res.stopped_stage ?? '' })}`}`} />}
              </Box>
              <KvGrid items={[['id', run.run_id], [t('propMissionMass'), `${res.mission.mass_kg.toFixed(2)} kg`], [t('propMissionEnergy'), `${res.mission.pack.E_usable_Wh.toFixed(0)} Wh`],
                [t('propMissionPassing'), `${res.counts.passed} / ${res.counts.total}`]]} testId="mission-run-cells" />
              {run.motor.kind === 'estimate' && <Alert severity="warning" sx={{ py: 0, fontSize: 12 }}>{t('propMissionEstimateResult')}</Alert>}
              {refined ? <RefinedCard refined={refined} objective={objective} geo={geo} motorRadiusMm={20} onSave={onSaveDraft} saveMsg={saveMsg} busy={busy} /> : null}
              <BestCard best={best} objective={objective} seed={!!refined} />
              <ObjectiveChart rows={res.results} objective={objective} />
              <Panel title={t('propMissionTable')} tip={t('propMissionTableTip')}>
                <ResultTable rows={rows} sortKey={sort.key} dir={sort.dir} onSort={onSort} selected={picked?.id ?? null} onSelect={setSelected} />
              </Panel>
              <RowDetails row={picked} alternatives={picked ? res.alternatives[picked.id] : undefined} />
            </>
          )}
          {!res && !running && !run && <Typography sx={{ fontSize: 13, color: 'var(--text-3)' }}>{t('propMissionIdle')}</Typography>}
        </Box>
      </Box>
      <Panel title={t('propMissionStored')} tip={t('propMissionStoredTip')}>
        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', mb: 0.5 }}>
          <Button size="small" onClick={refreshRuns}>{t('propRunsRefresh')}</Button>
          <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('propRunsCount', { n: runs.length })}</Typography>
        </Box>
        {runs.length > 0 && (
          <Box sx={{ maxHeight: 220, overflow: 'auto' }}>
            <Table size="small" data-testid="mission-stored">
              <TableHead><TableRow>{['propMissionColWhen', 'propMissionMotor', 'propMissionKindCol', 'propMissionObjective', 'propMissionColBest', 'propMissionColGain', 'propMissionColStatus', ''].map((k) => (
                <TableCell key={k} sx={{ fontSize: 11, py: 0.25 }}>{k ? t(k) : ''}</TableCell>))}</TableRow></TableHead>
              <TableBody>
                {runs.map((r) => (
                  <TableRow key={r.run_id} selected={run?.run_id === r.run_id}>
                    <TableCell sx={CELL_S}>{r.created_utc?.replace('T', ' ').replace('Z', '')}</TableCell>
                    <TableCell sx={CELL_S}>{r.motor?.name}</TableCell>
                    <TableCell sx={CELL_S}>{r.motor?.kind ? t(`propMissionKind_${r.motor.kind}`) : '-'}</TableCell>
                    <TableCell sx={CELL_S}>{r.objective ? t(`propMissionObj_${r.objective}`) : '-'}</TableCell>
                    <TableCell sx={CELL_S}>{r.best ? `${r.best.name}${r.best.value != null ? ` ${r.best.value.toFixed(1)}` : ''}` : '-'}</TableCell>
                    <TableCell sx={CELL_S}>{r.refined?.gain_pct != null ? `${r.refined.gain_pct >= 0 ? '+' : ''}${r.refined.gain_pct.toFixed(1)} %` : '-'}</TableCell>
                    <TableCell sx={CELL_S}>{t(statusKey(r.status))}</TableCell>
                    <TableCell sx={CELL_S}>
                      <Button size="small" onClick={() => follow(r.run_id)}>{t('propRunsOpen')}</Button>
                      {!ACTIVE.includes(r.status) && <Button size="small" color="error" onClick={() => onDelete(r.run_id)} data-testid="mission-delete">{t('propMissionDelete')}</Button>}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
        )}
      </Panel>
    </Box>
  );
}
