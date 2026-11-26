/** Gears page (gear module stage 5, owner 2026-11-26): stage editor, point and duty-spectrum evaluation, 3-D assembly.
 *
 *  Staff only (the routes are). Everything shown is the core's schema-v1 answer; the page computes nothing.  The core does not
 *  accept hand-built request JSON, so every call first turns the plain editor fields into a complete record with
 *  POST /api/gears/template.  The editor state is remembered across reloads (viewMemory). */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Box, Button, CircularProgress, Paper, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';

import SubTabs from '../../components/common/SubTabs';
import { oneOf } from '../../lib/viewMemory';
import { useRememberedView } from '../../lib/useRememberedView';
import { GearsApiError, gearsApi } from './api';
import { errorMap, humanoidJoint, toPlainBody, validateEditor, type EditorState } from './plain';
import type { AssemblyResult, BoxRecord, CataloguesAnswer, EvaluationResult, FieldError, GearboxResponse } from './types';
import AssemblyView from './components/AssemblyView';
import Limitations from './components/Limitations';
import OperatingEditor from './components/OperatingEditor';
import Results from './components/Results';
import SpectrumEditor from './components/SpectrumEditor';
import MotorPanel, { type SavedRefLite } from './components/MotorPanel';
import SavedPanel from './components/SavedPanel';
import StageEditor from './components/StageEditor';
import { EditorFromRequestError, editorFromRequest, responseToShow } from './saved';
import { Lbl, StateChip } from './components/parts';

const TABS = ['point', 'spectrum', 'assembly', 'motor'] as const;
type Tab = typeof TABS[number];

interface Ran<R> { response: GearboxResponse<R>; key: string }

export default function GearsPage() {
  const { t } = useTranslation('gears');
  const [state, setState] = useRememberedView<EditorState>('gears.editor', humanoidJoint(), validateEditor);
  const [tab, setTab] = useRememberedView<Tab>('gears.tab', 'point', oneOf(TABS));
  const [catalogues, setCatalogues] = useState<CataloguesAnswer | null>(null);
  const [point, setPoint] = useState<Ran<EvaluationResult> | null>(null);
  const [spectrum, setSpectrum] = useState<Ran<EvaluationResult> | null>(null);
  const [assembly, setAssembly] = useState<Ran<AssemblyResult> | null>(null);
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const [openRecord_, setOpenRecord] = useState<SavedRefLite | null>(null);

  useEffect(() => {
    const ctl = new AbortController();
    gearsApi.catalogues(ctl.signal).then(setCatalogues).catch((e) => { if (e?.name !== 'AbortError') setFailure(e instanceof Error ? e.message : String(e)); });
    return () => ctl.abort();
  }, []);
  useEffect(() => () => abort.current?.abort(), []);

  const keyOf = useCallback((op: Exclude<Tab, 'motor'>) => JSON.stringify(toPlainBody(state, op === 'assembly' ? 'assembly' : op)), [state]);
  const fieldErrors = useMemo(() => errorMap(errors), [errors]);

  const fail = (e: unknown) => {
    if ((e as { name?: string })?.name === 'AbortError') return;
    if (e instanceof GearsApiError && e.errors.length) { setErrors(e.errors); setFailure(null); }
    else { setErrors([]); setFailure(e instanceof Error ? e.message : String(e)); }
  };

  const run = async () => {
    if (tab === 'motor') return;
    abort.current?.abort();
    const ctl = new AbortController(); abort.current = ctl;
    setBusy(true); setErrors([]); setFailure(null);
    try {
      if (tab === 'assembly') {
        const key = keyOf('assembly');
        const rec = await gearsApi.template(toPlainBody(state, 'assembly', { outline_once: true, outline_stride: 4 }), ctl.signal);
        const response = await gearsApi.assembly(rec, ctl.signal) as GearboxResponse<AssemblyResult>;
        setAssembly({ response, key });
      } else {
        const key = keyOf(tab);
        const rec = await gearsApi.template(toPlainBody(state, tab), ctl.signal);
        const response = await gearsApi.evaluate(rec, ctl.signal) as GearboxResponse<EvaluationResult>;
        (tab === 'point' ? setPoint : setSpectrum)({ response, key });
      }
    } catch (e) { fail(e); } finally { if (abort.current === ctl) setBusy(false); }
  };

  // Save: the request of the open point / spectrum tab (the assembly tab saves the point request) with its response when it is current.
  const buildSave = useCallback(async () => {
    const op = tab === 'spectrum' ? 'spectrum' : 'point';
    const request = await gearsApi.template(toPlainBody(state, op));
    const ran = op === 'spectrum' ? spectrum : point;
    return { request, response: ran && ran.key === keyOf(op) ? ran.response : null };
  }, [tab, state, spectrum, point, keyOf]);
  const openRecord = (rec: BoxRecord) => {
    try {
      const next = { ...editorFromRequest(rec.request), name: rec.name };
      setState(next); setErrors([]); setFailure(null);
      const response = responseToShow(rec);
      const op = rec.request.operation === 'spectrum' ? 'spectrum' : 'point';
      const keyFor = JSON.stringify(toPlainBody(next, op));
      setPoint(null); setSpectrum(null);
      if (response) (op === 'spectrum' ? setSpectrum : setPoint)({ response: response as GearboxResponse<EvaluationResult>, key: keyFor });
      setTab(op);
    } catch (e) {
      if (e instanceof EditorFromRequestError) { setErrors([{ path: e.path, code: 'not_editable', message: e.message }]); setFailure(null); }
      else throw e;
    }
  };

  const shown = tab === 'point' ? point : tab === 'spectrum' ? spectrum : null;
  const stale = (ran: Ran<unknown> | null) => !!ran && tab !== 'motor' && ran.key !== keyOf(tab);

  return (
    <Box data-testid="gears-page" sx={{ height: '100%', overflow: 'auto', p: 2 }}>
      <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', mb: 1.5, flexWrap: 'wrap' }}>
        <Typography variant="h6" sx={{ fontSize: 18, fontWeight: 600 }}><Lbl k="title" /></Typography>
        <StateChip state="estimated" label={t('state.screening')} />
      </Box>
      {failure && <Typography role="alert" data-testid="gears-failure" sx={{ color: 'error.main', fontSize: 13, mb: 1 }}>{failure}</Typography>}
      {errors.length > 0 && (
        <Paper variant="outlined" role="alert" data-testid="gears-errors" sx={{ p: 1, mb: 1.5, borderColor: 'error.main' }}>
          {errors.map((e, i) => <Typography key={i} sx={{ color: 'error.main', fontSize: 13 }}>{e.path || '/'}: {e.message}</Typography>)}
        </Paper>
      )}
      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', lg: 'minmax(460px, 5fr) 6fr' }, alignItems: 'start' }}>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, minWidth: 0 }}>
          <SavedPanel name={state.name} onName={(name) => setState({ ...state, name })} buildSave={buildSave} onOpen={openRecord} onRef={setOpenRecord} />
          <OperatingEditor state={state} onChange={setState} errors={fieldErrors} catalogues={catalogues} />
          <StageEditor state={state} onChange={setState} errors={fieldErrors} catalogues={catalogues} />
        </Box>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, minWidth: 0 }}>
          <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
            <SubTabs<Tab> value={tab} onChange={setTab} ariaLabel={t('l.title')} testId="gears-tabs"
              items={TABS.map((id) => ({ id, label: t(`tab.${id}`), tip: t(`tip.tab_${id}`) }))} />
            {tab !== 'motor' && <Button size="small" variant="contained" onClick={run} disabled={busy} data-testid="gears-run">{t(tab === 'assembly' ? 'l.build' : 'l.calculate')}</Button>}
            {busy && <CircularProgress size={16} />}
            {tab !== 'motor' && (tab === 'assembly' ? stale(assembly) : stale(shown)) && <StateChip state="estimated" label={t('state.stale')} />}
          </Box>
          {tab === 'spectrum' && <SpectrumEditor value={state.spectrum} onChange={(v) => setState({ ...state, spectrum: v })} errors={fieldErrors} />}
          {tab === 'motor'
            ? <MotorPanel buildInline={() => gearsApi.template(toPlainBody(state, 'point'))} saved={openRecord_} />
            : tab === 'assembly'
            ? <AssemblyView assembly={assembly?.response.result ?? null} sourceKey={`gears:${assembly?.response.request_hash ?? 'none'}`} busy={busy} />
            : shown ? <Results result={shown.response.result} isPoint={tab === 'point'} />
              : <Typography sx={{ fontSize: 13, color: 'var(--text-3)' }}>{t('noResult')}</Typography>}
          <Limitations />
        </Box>
      </Box>
    </Box>
  );
}
