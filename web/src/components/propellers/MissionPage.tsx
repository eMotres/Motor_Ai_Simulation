/** Propellers > Mission (owner 2026-11-26): the best propeller and the motor turns (Kv) for a flight.  Choose a catalogue motor, the battery,
 *  the airframe and the flight, press Run: stage 1 ranks a listed set of propellers (catalogue props as reference rows, the own family on a grid)
 *  with the Kv chosen per propeller, stage 2 refines the best own propeller with the Designer's optimiser against the mission objective.
 *  Everything is an estimate (forward flight by the momentum / energy method) and the page says so. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Box, Button, Chip, CircularProgress, LinearProgress, MenuItem, Select, Table, TableBody, TableCell, TableHead, TableRow, Tooltip, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';

import { Panel } from './DesignerParts';
import { BestCard, MCheck, MField, MotorPicker, ObjectiveChart, RefinedCard, ResultTable, RowDetails } from './MissionParts';
import { DEFAULT_FORM, DRAFT_PREFIX, OBJECTIVES, buildBody, draftName, motorKey, sortRows } from '../../lib/propellerMission';
import type { MissionForm, SortKey } from '../../lib/propellerMission';
import type { Vector } from '../../lib/propellerDesign';
import * as api from '../../lib/propellerMissionApi';
import * as design from '../../lib/propellerDesignApi';
import { readView, writeView } from '../../lib/viewMemory';

const FORM_KEY = 'propellers.mission.form';

function loadForm(): MissionForm {
  const raw = readView<Partial<MissionForm>>(FORM_KEY, (v) => (v && typeof v === 'object' ? (v as Partial<MissionForm>) : null));
  return { ...DEFAULT_FORM, ...(raw ?? {}) };
}

export default function MissionPage() {
  const { t } = useTranslation('simulation');
  const [form, setForm] = useState<MissionForm>(loadForm);
  const [opts, setOpts] = useState<api.MissionOptions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [run, setRun] = useState<api.MissionRun | null>(null);
  const [runs, setRuns] = useState<api.RunSummary[]>([]);
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' }>({ key: 'objective', dir: 'desc' });
  const [selected, setSelected] = useState<string | null>(null);
  const [geo, setGeo] = useState<design.Geometry | null>(null);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const poll = useRef<number | null>(null);

  const set = <K extends keyof MissionForm>(k: K, v: MissionForm[K]) => setForm((f) => ({ ...f, [k]: v }));
  useEffect(() => { writeView(FORM_KEY, form); }, [form]);

  const refreshRuns = useCallback(() => { api.listRuns().then((r) => setRuns(r.runs)).catch(() => setRuns([])); }, []);
  useEffect(() => {
    const ctl = new AbortController();
    api.getOptions(ctl.signal).then((o) => { setOpts(o); setError(null); }).catch((e) => { if (e?.name !== 'AbortError') setError(String(e?.message ?? e)); });
    refreshRuns();
    return () => ctl.abort();
  }, [refreshRuns]);

  const stopPolling = () => { if (poll.current !== null) { window.clearInterval(poll.current); poll.current = null; } };
  useEffect(() => stopPolling, []);

  const follow = useCallback((id: string) => {
    stopPolling();
    const tick = () => api.getRun(id).then((r) => {
      setRun(r);
      if (r.status !== 'running') { stopPolling(); refreshRuns(); }
    }).catch((e) => { setError(String(e?.message ?? e)); stopPolling(); });
    tick();
    poll.current = window.setInterval(tick, 1500);
  }, [refreshRuns]);

  const motor = useMemo(() => opts?.motors.find((m) => motorKey(m) === form.motorKey) ?? null, [opts, form.motorKey]);
  const preset = opts?.batteries[form.chemistry];

  const onRun = async () => {
    if (!opts) return;
    const b = buildBody(form, opts.motors);
    if (!b.ok) { setError(t('propMissionBadField', { field: b.field })); return; }
    setBusy(true); setError(null); setSelected(null); setGeo(null); setSaveMsg(null);
    try {
      const r = await api.startRun(b.body);
      setRun({ run_id: r.run_id, status: 'running', created_utc: '', motor: { kind: 's1', name: '' }, inputs: {}, version: '', done: 0, total: r.total });
      follow(r.run_id);
    } catch (e) { setError(String((e as Error).message)); } finally { setBusy(false); }
  };
  const onStop = () => { if (run) api.stopRun(run.run_id).catch((e) => setError(String(e?.message ?? e))); };

  const res = run?.result ?? null;
  const objective = (run?.inputs?.objective as typeof form.objective | undefined) ?? form.objective;
  const rows = useMemo(() => sortRows(res?.results ?? [], sort.key, sort.dir), [res, sort]);
  const best = res?.results.find((r) => r.passed) ?? null;
  const picked = rows.find((r) => r.id === selected) ?? best;
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

  const running = run?.status === 'running';
  const pct = run && run.total ? Math.min(100, (100 * (run.done ?? 0)) / run.total) : 0;
  const bad = (k: keyof MissionForm) => error !== null && error.includes(String(k));

  if (!opts) return <Box sx={{ p: 2 }}>{error ? <Alert severity="error">{error}</Alert> : <CircularProgress size={20} />}</Box>;

  return (
    <Box sx={{ p: 2, display: 'flex', flexDirection: 'column', gap: 1.25 }} data-testid="prop-mission">
      <Alert severity="info" sx={{ py: 0, fontSize: 12 }} data-testid="mission-note">{t('propMissionNote')}</Alert>
      {error && <Alert severity="error" data-testid="mission-error">{error}</Alert>}
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: 'minmax(0,1fr)', lg: 'minmax(340px, 0.9fr) minmax(0, 2fr)' }, gap: 1.25, alignItems: 'start' }}>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.25, minWidth: 0 }}>
          <Panel title={t('propMissionMotor')} tip={t('propMissionMotorTip')}>
            <MotorPicker motors={opts.motors} value={form.motorKey} onChange={(k) => set('motorKey', k)} />
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mt: 1, alignItems: 'center' }}>
              <MField label={t('propMissionRotors')} tip={t('propMissionRotorsTip')} value={form.rotors} onChange={(v) => set('rotors', v)} width={80} error={bad('rotors')} />
              <MField label={t('propMissionBoardCap')} tip={t('propMissionBoardCapTip')} value={form.boardCap} onChange={(v) => set('boardCap', v)} width={120} />
              {motor?.drives && motor.drives.length > 1 && (
                <Select size="small" value={form.drive} onChange={(e) => set('drive', String(e.target.value))} sx={{ fontSize: 12 }} renderValue={(v) => `${t('propMissionDrive')}: ${v}`}>
                  {motor.drives.map((d) => <MenuItem key={d} value={d} sx={{ fontSize: 12 }}>{d}</MenuItem>)}
                </Select>
              )}
            </Box>
          </Panel>
          <Panel title={t('propMissionKv')} tip={t('propMissionKvTip')}>
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
              <Select size="small" value={form.kvMode} onChange={(e) => set('kvMode', e.target.value as MissionForm['kvMode'])} sx={{ fontSize: 12 }} renderValue={(v) => t(`propMissionKvMode_${v}`)}>
                {['free', 'as_built'].map((m) => <MenuItem key={m} value={m} sx={{ fontSize: 12 }}>{t(`propMissionKvMode_${m}`)}</MenuItem>)}
              </Select>
              <MField label={t('propMissionHeadroom')} tip={t('propMissionHeadroomTip')} value={form.headroom} onChange={(v) => set('headroom', v)} width={90} error={bad('headroom')} />
            </Box>
            {form.kvMode === 'free' && (
              <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mt: 1 }}>
                <MField label={t('propMissionTurns0')} tip={t('propMissionTurns0Tip', { model: motor?.winding?.turns ?? '-' })} value={form.turns0} onChange={(v) => set('turns0', v)} width={96} />
                <MField label={t('propMissionStrip0')} tip={t('propMissionStrip0Tip')} value={form.strip0} onChange={(v) => set('strip0', v)} width={110} />
                <MField label={t('propMissionStripWidth')} tip={t('propMissionStripWidthTip')} value={form.stripWidth} onChange={(v) => set('stripWidth', v)} width={110} />
                <MField label={t('propMissionTurnsBelow')} value={form.turnsBelow} onChange={(v) => set('turnsBelow', v)} width={80} />
                <MField label={t('propMissionTurnsAbove')} value={form.turnsAbove} onChange={(v) => set('turnsAbove', v)} width={80} />
                <MField label={t('propMissionCoilTol')} tip={t('propMissionCoilTolTip')} value={form.coilTol} onChange={(v) => set('coilTol', v)} width={96} />
              </Box>
            )}
          </Panel>
          <Panel title={t('propMissionBattery')} tip={t('propMissionBatteryTip')}>
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
              <Select size="small" value={form.chemistry} onChange={(e) => set('chemistry', String(e.target.value))} sx={{ fontSize: 12 }} renderValue={(v) => opts.batteries[String(v)]?.label ?? String(v)}>
                {Object.entries(opts.batteries).map(([k, b]) => <MenuItem key={k} value={k} sx={{ fontSize: 12 }}>{b.label}</MenuItem>)}
              </Select>
              <MField label={t('propMissionCells')} tip={t('propMissionCellsTip')} value={form.cells} onChange={(v) => set('cells', v)} width={70} error={bad('cells')} />
              <MField label={t('propMissionCapacity')} value={form.capacity} onChange={(v) => set('capacity', v)} width={90} error={bad('capacity')} />
              <MField label={t('propMissionUsable')} tip={t('propMissionUsableTip')} value={form.usable} onChange={(v) => set('usable', v)} width={90} />
            </Box>
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mt: 1 }}>
              <MField label={t('propMissionVNom')} tip={t('propMissionPresetTip', { v: preset?.v_nom })} value={form.vNom} onChange={(v) => set('vNom', v)} width={92} />
              <MField label={t('propMissionVMin')} tip={t('propMissionPresetTip', { v: preset?.v_min })} value={form.vMin} onChange={(v) => set('vMin', v)} width={92} />
              <MField label={t('propMissionVMax')} tip={t('propMissionPresetTip', { v: preset?.v_max })} value={form.vMax} onChange={(v) => set('vMax', v)} width={92} />
              <MField label={t('propMissionWhKg')} tip={t('propMissionPresetTip', { v: preset?.wh_per_kg })} value={form.whKg} onChange={(v) => set('whKg', v)} width={92} />
              <MField label={t('propMissionRCell')} tip={t('propMissionRCellTip', { v: preset?.r_cell_ohm_ah })} value={form.rCell} onChange={(v) => set('rCell', v)} width={110} />
              <MField label={t('propMissionBattMass')} tip={t('propMissionBattMassTip')} value={form.battMass} onChange={(v) => set('battMass', v)} width={110} />
            </Box>
          </Panel>
          <Panel title={t('propMissionAirframe')} tip={t('propMissionAirframeTip')}>
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
              <MField label={t('propMissionDry')} tip={t('propMissionDryTip')} value={form.dryMass} onChange={(v) => set('dryMass', v)} width={100} error={bad('dryMass')} />
              <MField label={t('propMissionPayload')} value={form.payload} onChange={(v) => set('payload', v)} width={100} />
              <MField label={t('propMissionDrag')} tip={t('propMissionDragTip')} value={form.dragArea} onChange={(v) => set('dragArea', v)} width={110} />
              <MField label={t('propMissionMaxD')} tip={t('propMissionMaxDTip')} value={form.maxD} onChange={(v) => set('maxD', v)} width={96} />
              <MField label={t('propMissionMinD')} value={form.minD} onChange={(v) => set('minD', v)} width={96} />
              <MField label={t('propMissionAirTemp')} value={form.airTemp} onChange={(v) => set('airTemp', v)} width={90} />
              <MField label={t('propMissionAltitude')} value={form.altitude} onChange={(v) => set('altitude', v)} width={90} />
              <MField label={t('propMissionAvionics')} tip={t('propMissionAvionicsTip')} value={form.avionics} onChange={(v) => set('avionics', v)} width={96} />
            </Box>
          </Panel>
          <Panel title={t('propMissionFlight')} tip={t('propMissionFlightTip')}>
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
              <MField label={t('propMissionSpeed')} tip={t('propMissionSpeedTip')} value={form.speedKmh} onChange={(v) => set('speedKmh', v)} width={100} error={bad('speedKmh')} />
              <MCheck label={t('propMissionBestSpeed')} tip={t('propMissionBestSpeedTip')} checked={form.bestSpeed} onChange={(v) => set('bestSpeed', v)} />
              <MField label={t('propMissionHoverShare')} tip={t('propMissionHoverShareTip')} value={form.hoverShare} onChange={(v) => set('hoverShare', v)} width={110} />
              <MField label={t('propMissionTwCont')} tip={t('propMissionTwContTip')} value={form.twCont} onChange={(v) => set('twCont', v)} width={96} />
              <MField label={t('propMissionTwPeak')} tip={t('propMissionTwPeakTip')} value={form.twPeak} onChange={(v) => set('twPeak', v)} width={96} />
              <MField label={t('propMissionTipMach')} value={form.tipMach} onChange={(v) => set('tipMach', v)} width={90} />
              <Select size="small" value={form.enduranceMode} onChange={(e) => set('enduranceMode', e.target.value as MissionForm['enduranceMode'])} sx={{ fontSize: 12 }}
                renderValue={(v) => `${t('propMissionEnduranceMode')}: ${t(`propMissionEnd_${v}`)}`}>
                {['hover', 'best_speed'].map((m) => <MenuItem key={m} value={m} sx={{ fontSize: 12 }}>{t(`propMissionEnd_${m}`)}</MenuItem>)}
              </Select>
            </Box>
          </Panel>
          <Panel title={t('propMissionCandidates')} tip={t('propMissionCandidatesTip')}>
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
              <MCheck label={t('propMissionCatalogue')} tip={t('propMissionCatalogueTip')} checked={form.catalogue} onChange={(v) => set('catalogue', v)} />
              <MCheck label={t('propMissionOwn')} tip={t('propMissionOwnTip')} checked={form.own} onChange={(v) => set('own', v)} />
              <MField label={t('propMissionPdMin')} value={form.pdMin} onChange={(v) => set('pdMin', v)} width={80} />
              <MField label={t('propMissionPdMax')} value={form.pdMax} onChange={(v) => set('pdMax', v)} width={80} />
              <MField label={t('propMissionPdStep')} value={form.pdStep} onChange={(v) => set('pdStep', v)} width={80} />
              <MField label={t('propMissionChords')} tip={t('propMissionChordsTip')} value={form.chords} onChange={(v) => set('chords', v)} width={110} />
              <MField label={t('propMissionBlades')} tip={t('propMissionBladesTip')} value={form.blades} onChange={(v) => set('blades', v)} width={80} />
            </Box>
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center', mt: 1 }}>
              <MCheck label={t('propMissionRefine')} tip={t('propMissionRefineTip')} checked={form.refine} onChange={(v) => set('refine', v)} />
              {form.refine && (
                <>
                  <MField label={t('propMissionTopN')} value={form.topN} onChange={(v) => set('topN', v)} width={80} />
                  <MField label={t('propMissionPop')} value={form.pop} onChange={(v) => set('pop', v)} width={80} />
                  <MField label={t('propMissionGen')} value={form.generations} onChange={(v) => set('generations', v)} width={90} />
                  <MField label={t('propMissionSeed')} value={form.seed} onChange={(v) => set('seed', v)} width={70} />
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
            </Box>
            {running && (
              <Box sx={{ mt: 1 }} data-testid="mission-progress">
                <LinearProgress variant="determinate" value={pct} />
                <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{t('propMissionProgress', { done: run?.done ?? 0, total: run?.total ?? 0, line: run?.line ?? '' })}</Typography>
              </Box>
            )}
          </Panel>
        </Box>

        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.25, minWidth: 0 }}>
          {run && run.status === 'failed' && <Alert severity="error">{run.error}</Alert>}
          {run && run.status === 'interrupted' && <Alert severity="warning">{t('propMissionInterrupted')}</Alert>}
          {res && run && (
            <>
              <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }} data-testid="mission-motor-used">
                <Chip size="small" color={run.motor.kind === 'estimate' ? 'warning' : 'success'} variant="outlined"
                  label={`${run.motor.name} · ${t(`propMissionKind_${run.motor.kind}`)}`} />
                <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
                  {t('propMissionRunInfo', { id: run.run_id, mass: res.mission.mass_kg.toFixed(2), energy: res.mission.pack.E_usable_Wh.toFixed(0), passed: res.counts.passed, total: res.counts.total })}
                </Typography>
                {res.stopped && <Chip size="small" color="warning" label={t('propMissionStopped')} />}
              </Box>
              {run.motor.kind === 'estimate' && <Alert severity="warning" sx={{ py: 0, fontSize: 12 }}>{t('propMissionEstimateResult')}</Alert>}
              {refined ? (
                <RefinedCard refined={refined} objective={objective} geo={geo} motorRadiusMm={20} onSave={onSaveDraft} saveMsg={saveMsg} busy={busy} />
              ) : null}
              <BestCard best={best} objective={objective} seed={!!refined} />
              <ObjectiveChart rows={res.results} objective={objective} />
              <Panel title={t('propMissionTable')} tip={t('propMissionTableTip')}>
                <ResultTable rows={rows} sortKey={sort.key} dir={sort.dir} onSort={onSort} selected={picked?.id ?? null} onSelect={setSelected} />
              </Panel>
              <RowDetails row={picked} alternatives={picked ? res.alternatives[picked.id] : undefined} />
            </>
          )}
          {!res && !running && <Typography sx={{ fontSize: 13, color: 'var(--text-3)' }}>{t('propMissionIdle')}</Typography>}
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
                    <TableCell sx={CELL_S}>{r.best ? `${r.best.name}${r.best.value != null ? ` · ${r.best.value.toFixed(1)}` : ''}` : '-'}</TableCell>
                    <TableCell sx={CELL_S}>{r.refined?.gain_pct != null ? `${r.refined.gain_pct >= 0 ? '+' : ''}${r.refined.gain_pct.toFixed(1)} %` : '-'}</TableCell>
                    <TableCell sx={CELL_S}>{r.status}</TableCell>
                    <TableCell sx={CELL_S}><Tooltip title={t('propRunsOpen')}><Button size="small" onClick={() => follow(r.run_id)}>{t('propRunsOpen')}</Button></Tooltip></TableCell>
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

const CELL_S = { fontSize: 11, py: 0.25, whiteSpace: 'nowrap' } as const;
