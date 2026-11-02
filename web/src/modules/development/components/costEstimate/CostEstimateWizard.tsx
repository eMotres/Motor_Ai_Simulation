/** Project cost estimate wizard (owner 2026-11-02, ADMIN ONLY, simple): Settings -> Iteration 1 -> Iteration 2 ... ->
 *  Review.  One short screen per iteration with the four lines (tooling, motor manufacturing, testing, shipping), each
 *  with an amount, a currency, a source chip and a note.  Autosaves with the server's optimistic revision. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Box, Button, ButtonBase, Chip, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Select,
  Stack, Switch, Table, TableBody, TableCell, TableHead, TableRow, TextField, ToggleButton, ToggleButtonGroup, Tooltip, Typography, useMediaQuery,
} from '@mui/material';
import CheckIcon from '@mui/icons-material/Check';
import HelpTip from '../../../../components/common/HelpTip';
import { BigNumber, StepCard, TOUCH } from '../specWizard/ui';
import { developmentApi, DevelopmentApiError } from '../../api';
import {
  buildCostSteps, defaultDisplayCurrency, displayCurrencyOf, editLine, formatMoney, fxLine, langOf, LINE_IDS, lineConverted, lineNeeded, originChip,
  reviewRows, setDisplayCurrency, setIterationField, setManualRate, setSetting, setToolingMode, snapshotTotalText, sourceCounts, stepIndexOfMissing,
  stepState, todayIso, toolingMode, totalsView, txt,
  type Bench, type CostComputed, type CostEstimate, type CostEstimateAnswer, type CostField, type CostIteration, type CostLang,
  type CostLine, type CostLineDef, type CostSchema, type CostSnapshot, type CostStep, type LineId, type T,
} from '../../costEstimate';

const CHIP_COLOR: Record<string, 'default' | 'primary' | 'secondary' | 'success' | 'warning' | 'info'> = {
  production: 'info', estimate: 'secondary', converted: 'primary', manual: 'success', rule: 'default', missing: 'warning', none: 'default',
};

function SettingField({ field, value, onChange, lang }: { field: CostField; value: unknown; onChange: (v: unknown) => void; lang: CostLang }) {
  const label = txt(field.label, lang);
  const head = <Stack direction="row" spacing={0.5} alignItems="center"><Typography sx={{ fontSize: 13, color: 'var(--text-1)' }}>{label}</Typography><HelpTip title={txt(field.help, lang)} /></Stack>;
  if (field.type === 'bool') return <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ minHeight: TOUCH }}>{head}<Switch checked={!!value} onChange={(e) => onChange(e.target.checked)} inputProps={{ 'aria-label': label }} /></Stack>;
  if (field.type === 'enum') return <Stack spacing={0.6}>{head}<Select size="small" value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} inputProps={{ 'aria-label': label }}>
    {(field.options ?? []).map((o) => <MenuItem key={o} value={o}>{field.option_labels?.[o] ? txt(field.option_labels[o], lang) : o}</MenuItem>)}
  </Select></Stack>;
  return <Stack spacing={0.6}>{head}<BigNumber label="" unit={field.id.endsWith('_pct') ? '%' : ''} size="normal" min={field.min} value={typeof value === 'number' ? value : null}
    ariaLabel={label} onChange={(n) => onChange(n === null ? (field.type === 'int' ? field.min ?? 1 : 0) : field.type === 'int' ? Math.round(n) : n)} /></Stack>;
}

/** One of the four lines: label, source chip, (later tooling: % of iteration 1 or an amount), amount + currency, the
 *  line's money, a note; a missing line shows why. */
function LineRow({ def, line, it, total, converted, schema, lang, t, onEdit, onMode }: {
  def: CostLineDef; line: CostLine; it: CostIteration; total: string; converted: string | null; schema: CostSchema; lang: CostLang; t: T;
  onEdit: (patch: Parameters<typeof editLine>[2]) => void; onMode: (mode: 'pct' | 'amount') => void;
}) {
  const label = txt(def.label, lang);
  const chip = originChip(line, it, def.id, t);
  const needed = lineNeeded(it, def.id);
  const pctMode = def.id === 'tooling' && it.index > 1 && toolingMode(line) === 'pct';
  return <Box sx={{ display: 'grid', gap: 1, alignItems: 'end', gridTemplateColumns: { xs: '1fr 1fr', md: 'minmax(180px, 1.4fr) minmax(130px, 1fr) 96px minmax(120px, 1fr) minmax(140px, 1.2fr)' }, py: 1.25, borderTop: '1px solid var(--line-soft)', opacity: needed ? 1 : 0.55 }}>
    <Stack spacing={0.5} sx={{ gridColumn: { xs: '1 / -1', md: 'auto' }, minWidth: 0, alignSelf: 'center' }}>
      <Stack direction="row" spacing={0.5} alignItems="center"><Typography sx={{ fontSize: 14, fontWeight: 600, color: 'var(--text-0)' }}>{label}</Typography><HelpTip title={txt(def.help, lang)} /></Stack>
      <Stack direction="row" spacing={0.5} alignItems="center" flexWrap="wrap" useFlexGap>
        <Tooltip title={chip.tip} enterTouchDelay={0}><Chip size="small" color={CHIP_COLOR[chip.kind] ?? 'default'} variant={chip.kind === 'none' ? 'outlined' : 'filled'} label={chip.label} sx={{ height: 22, fontSize: 11 }} /></Tooltip>
        {def.id === 'tooling' && it.index > 1 && <ToggleButtonGroup size="small" exclusive value={toolingMode(line)} onChange={(_e, v) => v && onMode(v)} aria-label={label}>
          <ToggleButton value="pct" sx={{ py: 0, px: 1, fontSize: 11, textTransform: 'none' }}>{t('costEstimate.pctMode')}</ToggleButton>
          <ToggleButton value="amount" sx={{ py: 0, px: 1, fontSize: 11, textTransform: 'none' }}>{t('costEstimate.amountMode')}</ToggleButton>
        </ToggleButtonGroup>}
      </Stack>
      {line.status === 'missing' && needed && line.hint && <Typography sx={{ fontSize: 11.5, color: 'warning.main' }}>{line.hint}</Typography>}
    </Stack>
    {pctMode ? <>
      <BigNumber label={t('costEstimate.ofIterationOne')} unit="%" size="normal" min={0} value={line.pct} ariaLabel={`${label}: ${t('costEstimate.ofIterationOne')}`} onChange={(n) => onEdit({ pct: n })} />
      <Box sx={{ display: { xs: 'none', md: 'block' } }} />
    </> : <>
      <BigNumber label={def.per_motor ? t('costEstimate.perMotor') : t('costEstimate.amount')} unit="" size="normal" min={0} value={line.amount} disabled={!needed}
        ariaLabel={`${label}: ${def.per_motor ? t('costEstimate.perMotor') : t('costEstimate.amount')}`} onChange={(n) => onEdit({ amount: n })} />
      <Stack spacing={0.6}><Typography sx={{ fontSize: 12.5, color: 'var(--text-2)' }}>{t('costEstimate.currency')}</Typography>
        <Select size="small" value={line.currency} disabled={!needed} onChange={(e) => onEdit({ currency: String(e.target.value) })} inputProps={{ 'aria-label': `${label}: ${t('costEstimate.currency')}` }}>
          {schema.currencies.map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}
        </Select></Stack>
    </>}
    <Stack spacing={0.6} sx={{ minWidth: 0 }}><Typography sx={{ fontSize: 12.5, color: 'var(--text-2)' }}>{def.per_motor ? t('costEstimate.timesMotors', { n: it.motors }) : t('costEstimate.lineTotal')}</Typography>
      <Typography sx={{ fontSize: 15, fontWeight: 600, fontVariantNumeric: 'tabular-nums', minHeight: TOUCH, display: 'flex', alignItems: 'center', color: 'var(--text-0)', overflowWrap: 'anywhere' }}>{total}</Typography>
      {converted && <Typography data-testid="line-converted" sx={{ fontSize: 12.5, fontVariantNumeric: 'tabular-nums', color: 'var(--text-3)', mt: -0.5, overflowWrap: 'anywhere' }}>{converted}</Typography>}</Stack>
    <TextField size="small" value={line.note} placeholder={t('costEstimate.note')} onChange={(e) => onEdit({ note: e.target.value })} inputProps={{ 'aria-label': `${label}: ${t('costEstimate.note')}`, maxLength: 500 }} sx={{ gridColumn: { xs: '1 / -1', md: 'auto' } }} />
  </Box>;
}

export default function CostEstimateWizard({ projectId, language, t, who }: { projectId: string; language: string; t: T; who: string }) {
  const lang = langOf(language);
  const wide = useMediaQuery('(min-width:900px)');
  const [answer, setAnswer] = useState<CostEstimateAnswer | null>(null);
  const [estimate, setEstimate] = useState<CostEstimate | null>(null);
  const [computed, setComputed] = useState<CostComputed | null>(null);
  const [step, setStep] = useState(0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState(false);
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<CostEstimateAnswer['report'] | null>(null);
  const [snapOpen, setSnapOpen] = useState(false);
  const [snapNote, setSnapNote] = useState('');
  const [snapshots, setSnapshots] = useState<CostSnapshot[]>([]);
  const edits = useRef(0);
  const timer = useRef<number | null>(null);
  const latest = useRef<CostEstimate | null>(null);
  const inflight = useRef(false);
  latest.current = estimate;
  // the totals are shown in the display currency: the owner's pick, else the UI language's (en -> EUR, zh-CN -> CNY)
  const displayOf = (e: CostEstimate | null | undefined) => displayCurrencyOf(e?.settings, lang);

  const apply = (a: CostEstimateAnswer) => { setAnswer(a); setEstimate(a.estimate); setComputed(a.computed); setConflict(false); };
  const load = useCallback(async () => {
    setError('');
    try { apply(await developmentApi.costEstimate(projectId, defaultDisplayCurrency(lang))); setSnapshots(await developmentApi.costEstimateSnapshots(projectId)); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }, [projectId, lang]);
  useEffect(() => { void load(); return () => { if (timer.current) window.clearTimeout(timer.current); }; }, [load]);

  const save = async () => {
    const cur = latest.current;
    if (!cur) return;
    const mark = edits.current;
    setSaving(true);
    inflight.current = true;
    try {
      const a = await developmentApi.saveCostEstimate(projectId, cur, displayOf(cur));
      setAnswer(a); setComputed(a.computed);
      if (edits.current === mark) setEstimate(a.estimate);
      else { setEstimate((e) => (e ? { ...e, revision: a.estimate.revision } : e)); schedule(); }
      setError('');
    } catch (e) {
      if (e instanceof DevelopmentApiError && e.status === 409) setConflict(true);
      else setError(e instanceof Error ? e.message : String(e));
    } finally { inflight.current = false; setSaving(false); }
  };
  const schedule = () => { if (timer.current) window.clearTimeout(timer.current); timer.current = window.setTimeout(() => { timer.current = null; void save(); }, 700); };
  const change = (next: CostEstimate) => { edits.current += 1; setEstimate(next); schedule(); };
  /** The display currency changes the numbers on screen: save at once so they follow. */
  const changeNow = (next: CostEstimate) => {
    edits.current += 1; latest.current = next; setEstimate(next);
    if (inflight.current) { schedule(); return; }          // a save is running: the next one follows it (revision order)
    if (timer.current) { window.clearTimeout(timer.current); timer.current = null; }
    void save();
  };

  const schema = answer?.schema ?? null;
  const steps = useMemo(() => (estimate ? buildCostSteps(estimate) : []), [estimate?.iterations.length]);
  if (error && !estimate) return <Alert severity="error" action={<Button color="inherit" size="small" onClick={() => void load()}>{t('costEstimate.retry')}</Button>}>{error}</Alert>;
  if (!schema || !estimate || !computed || !answer) return <CircularProgress size={18} />;
  const current: CostStep = steps[Math.min(step, steps.length - 1)];
  const today = todayIso();
  const tv = totalsView(computed);
  const laterPct = schema.lines.find((l) => l.id === 'tooling')?.later_pct_default ?? 20;

  const suggest = async (refresh: boolean) => {
    if (timer.current) { window.clearTimeout(timer.current); timer.current = null; await save(); }
    setBusy(true);
    try { const a = await developmentApi.suggestCostEstimate(projectId, latest.current?.revision ?? estimate.revision, refresh, displayOf(latest.current)); apply(a); setReport(a.report ?? null); }
    catch (e) { if (e instanceof DevelopmentApiError && e.status === 409) setConflict(true); else setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  const freeze = async () => {
    setBusy(true);
    try { await developmentApi.snapshotCostEstimate(projectId, estimate.revision, snapNote, displayOf(estimate)); setSnapOpen(false); setSnapNote(''); setSnapshots(await developmentApi.costEstimateSnapshots(projectId)); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  const stepTitle = (s: CostStep) => s.kind === 'settings' ? t('costEstimate.steps.settings') : s.kind === 'review' ? t('costEstimate.steps.review') : t('costEstimate.iteration', { n: s.iteration });
  const lineTotal = (index: number, id: LineId): string => {
    const r = computed.iterations.find((it) => it.index === index)?.lines[id];
    return r?.amount ? formatMoney(r.amount) : '—';
  };

  const header = <Stack direction={{ xs: 'column', md: 'row' }} spacing={{ xs: 1, md: 2 }} alignItems={{ md: 'center' }} sx={{ bgcolor: 'var(--panel)', border: '1px solid var(--line)', borderRadius: '14px', px: { xs: 2, md: 3 }, py: 1.5 }}>
    <Box sx={{ flex: 1, minWidth: 0 }}>
      <Stack direction="row" spacing={1} alignItems="center"><Typography component="h1" sx={{ fontSize: 18, fontWeight: 600, color: 'var(--text-0)' }}>{t('costEstimate.title')}</Typography>
        <Tooltip title={t('costEstimate.adminOnlyTip')}><Chip size="small" label={t('costEstimate.adminOnly')} sx={{ height: 20, fontSize: 10.5 }} /></Tooltip></Stack>
      <Tooltip title={tv.converted ? `${t('costEstimate.totalTip')} ${t('costEstimate.originalsTip', { list: tv.original })}` : t('costEstimate.totalTip')}><Typography sx={{ fontSize: 13, color: 'var(--text-2)', fontVariantNumeric: 'tabular-nums' }}>{t('costEstimate.total')}: {tv.total} · {t('costEstimate.missingCount', { count: computed.missing_count })}</Typography></Tooltip>
    </Box>
    <Tooltip title={t('costEstimate.displayCurrencyTip')}><Stack direction="row" spacing={0.75} alignItems="center">
      <Typography component="span" sx={{ fontSize: 12.5, color: 'var(--text-2)', whiteSpace: 'nowrap' }}>{t('costEstimate.displayCurrency')}</Typography>
      <Select size="small" displayEmpty value={estimate.settings.display_currency || ''} onChange={(e) => changeNow(setDisplayCurrency(estimate, String(e.target.value) || null))}
        inputProps={{ 'aria-label': t('costEstimate.displayCurrency') }} sx={{ minWidth: 118, '& .MuiSelect-select': { py: 0.75 } }}>
        <MenuItem value="">{t('costEstimate.displayAuto', { currency: defaultDisplayCurrency(lang, schema) })}</MenuItem>
        {schema.currencies.map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}
      </Select>
    </Stack></Tooltip>
    <Tooltip title={t('costEstimate.suggestTip')}><span><Button variant="contained" disabled={busy} onClick={() => void suggest(false)} sx={{ textTransform: 'none', borderRadius: '10px', minHeight: TOUCH }}>{busy ? <CircularProgress size={16} /> : t('costEstimate.suggest')}</Button></span></Tooltip>
    <Tooltip title={t('costEstimate.refreshTip')}><span><Button disabled={busy} onClick={() => void suggest(true)} sx={{ textTransform: 'none', minHeight: TOUCH }}>{t('costEstimate.refresh')}</Button></span></Tooltip>
    <Tooltip title={t('costEstimate.snapshotTip')}><span><Button variant="outlined" disabled={busy || !answer.stored} onClick={() => setSnapOpen(true)} sx={{ textTransform: 'none', borderRadius: '10px', minHeight: TOUCH }}>{t('costEstimate.snapshot')}</Button></span></Tooltip>
    <Typography role="status" sx={{ fontSize: 12.5, display: 'flex', alignItems: 'center', gap: 0.6, whiteSpace: 'nowrap', color: error ? 'error.main' : saving ? 'var(--text-3)' : 'success.main' }}>
      {!error && !saving && <CheckIcon sx={{ fontSize: 15 }} />}{error ? t('costEstimate.saveFailed') : saving ? t('costEstimate.saving') : t('costEstimate.saved')}
    </Typography>
  </Stack>;

  const nav = <Box component="nav" aria-label={t('costEstimate.stepsNav')} sx={{ display: 'flex', flexDirection: wide ? 'column' : 'row', gap: 0.5, overflowX: wide ? 'visible' : 'auto' }}>
    {steps.map((s, i) => {
      const st = s.kind === 'iteration' ? stepState(s, estimate) : null;
      const active = i === step;
      return <ButtonBase key={s.id} onClick={() => setStep(i)} aria-current={active ? 'step' : undefined}
        sx={{ display: 'flex', gap: 1, alignItems: 'center', justifyContent: 'flex-start', textAlign: 'left', minHeight: 44, px: 1.25, borderRadius: '10px', flex: wide ? undefined : '0 0 auto', bgcolor: active ? 'var(--panel)' : 'transparent', boxShadow: active ? '0 1px 2px rgba(0,0,0,.12)' : 'none', '&:hover': { bgcolor: 'var(--panel)' } }}>
        <Box component="span" aria-hidden sx={{ width: 24, height: 24, flex: '0 0 24px', borderRadius: '50%', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 600,
          bgcolor: active ? 'primary.main' : st?.state === 'done' ? 'success.main' : 'transparent', color: active || st?.state === 'done' ? '#fff' : 'var(--text-3)', border: active || st?.state === 'done' ? 'none' : '2px solid var(--line)' }}>{st?.state === 'done' && !active ? <CheckIcon sx={{ fontSize: 14 }} /> : i + 1}</Box>
        <Typography component="span" sx={{ fontSize: 13.5, fontWeight: active ? 600 : 500, color: 'var(--text-0)', whiteSpace: 'nowrap' }}>{stepTitle(s)}</Typography>
        {st && st.missing > 0 && <Typography component="span" title={t('costEstimate.missingCount', { count: st.missing })} sx={{ fontSize: 11, color: 'warning.main', ml: 'auto' }}>{st.missing}</Typography>}
      </ButtonBase>;
    })}
  </Box>;

  const settingsStep = <StepCard title={t('costEstimate.steps.settings')} tip={t('costEstimate.settingsTip')}>
    <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' } }}>
      {schema.settings.map((f) => <SettingField key={f.id} field={f} value={estimate.settings[f.id]} lang={lang} onChange={(v) => change(setSetting(estimate, schema, f.id, v))} />)}
      <Stack spacing={0.6}><Stack direction="row" spacing={0.5} alignItems="center"><Typography sx={{ fontSize: 13, color: 'var(--text-1)' }}>{t('costEstimate.targetMotor')}</Typography><HelpTip title={t('costEstimate.targetMotorTip')} /></Stack>
        <Select size="small" displayEmpty value={estimate.settings.target_motor ? `${estimate.settings.target_motor.die}/${estimate.settings.target_motor.config}` : ''}
          onChange={(e) => { const v = String(e.target.value); const m = answer.linked_motors.find((x) => `${x.die}/${x.config}` === v); change(setSetting(estimate, schema, 'target_motor', m ? { die: m.die, config: m.config } : null)); }} inputProps={{ 'aria-label': t('costEstimate.targetMotor') }}>
          <MenuItem value="">{t('costEstimate.noMotor')}</MenuItem>
          {answer.linked_motors.map((m) => <MenuItem key={`${m.die}/${m.config}`} value={`${m.die}/${m.config}`}>{m.die} / {m.config}</MenuItem>)}
        </Select></Stack>
    </Box>
    <Stack spacing={1} data-testid="fx-settings" sx={{ borderTop: '1px solid var(--line-soft)', pt: 1.5 }}>
      <Stack direction="row" spacing={0.5} alignItems="center"><Typography sx={{ fontSize: 14, fontWeight: 600, color: 'var(--text-0)' }}>{t('costEstimate.fx.settingsTitle')}</Typography><HelpTip title={t('costEstimate.fx.settingsTip')} /></Stack>
      {answer.fx_status?.state === 'stale' && <Alert severity="warning" sx={{ py: 0 }}>{t('costEstimate.fx.stale', { date: answer.fx_status.date || '' })}</Alert>}
      {!estimate.fx && <Typography sx={{ fontSize: 12.5, color: 'var(--text-3)' }}>{t('costEstimate.fx.none')}</Typography>}
      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' } }}>
        {schema.currencies.filter((c) => c !== 'EUR').map((cur) => {
          const manual = cur in (estimate.fx?.manual ?? {});
          return <Stack key={cur} spacing={0.5}>
            <BigNumber label={t('costEstimate.fx.rateLabel')} unit={cur} size="normal" min={0} value={estimate.fx?.rates?.[cur] ?? null}
              ariaLabel={`${t('costEstimate.fx.rateLabel')} ${cur}`} onChange={(n) => change(setManualRate(estimate, cur, n))} />
            <Stack direction="row" spacing={0.75} alignItems="center">
              {manual ? <Chip size="small" color="success" label={t('costEstimate.fx.manualChip')} sx={{ height: 22, fontSize: 11 }} />
                : estimate.fx?.ecb ? <Chip size="small" label={t('costEstimate.fx.ecbChip', { date: estimate.fx.ecb.date || '' })} sx={{ height: 22, fontSize: 11 }} />
                : <Chip size="small" color="warning" label={t('costEstimate.fx.noRate')} sx={{ height: 22, fontSize: 11 }} />}
              {manual && <Button size="small" sx={{ textTransform: 'none', minHeight: 28 }} onClick={() => change(setManualRate(estimate, cur, null))}>{t('costEstimate.fx.useEcb')}</Button>}
            </Stack>
          </Stack>;
        })}
      </Box>
    </Stack>
  </StepCard>;

  const iterationStep = (index: number) => {
    const it = estimate.iterations.find((x) => x.index === index);
    if (!it) return null;
    const res = computed.iterations.find((x) => x.index === index);
    return <StepCard title={t('costEstimate.iteration', { n: index })} action={<Typography sx={{ fontSize: 13, fontWeight: 600, fontVariantNumeric: 'tabular-nums', color: 'var(--text-1)' }}>{reviewRows(computed).find((r) => r.iteration === index)?.subtotal ?? formatMoney(res?.subtotal)}</Typography>}>
      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' } }}>
        {schema.iteration_fields.map((f) => <SettingField key={f.id} field={f} value={it[f.id as 'motors' | 'bench']} lang={lang}
          onChange={(v) => change(setIterationField(estimate, index, f.id as 'motors' | 'bench', v as number | Bench))} />)}
      </Box>
      <Box>{LINE_IDS.map((id) => { const def = schema.lines.find((l) => l.id === id)!; return <LineRow key={id} def={def} line={it.lines[id]} it={it} total={lineTotal(index, id)} converted={lineConverted(computed, index, id)} schema={schema} lang={lang} t={t}
        onEdit={(patch) => change(editLine(estimate, { iteration: index, line: id }, patch, who, today))}
        onMode={(mode) => change(setToolingMode(estimate, index, mode, laterPct))} />; })}</Box>
    </StepCard>;
  };

  const counts = sourceCounts(estimate);
  const lineLabel = (id: LineId) => txt(schema.lines.find((l) => l.id === id)?.label, lang);
  const missingWord = t('costEstimate.status.missing');
  const reviewStep = <Stack spacing={2}>
    {estimate.migration && <Alert severity="info" sx={{ py: 0.5 }}>
      <Typography sx={{ fontSize: 13 }}>{t('costEstimate.migrated')}</Typography>
      {estimate.migration.dropped.length > 0 && <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{t('costEstimate.dropped')}: {estimate.migration.dropped.join('; ')}</Typography>}
    </Alert>}
    <StepCard title={t('costEstimate.byIteration')} tip={t('costEstimate.totalTip')}>
      <Box sx={{ overflowX: 'auto' }}><Table size="small">
        <TableHead><TableRow><TableCell />{LINE_IDS.map((id) => <TableCell key={id}>{lineLabel(id)}</TableCell>)}<TableCell>{t('costEstimate.subtotal')}</TableCell></TableRow></TableHead>
        <TableBody>
          {reviewRows(computed, missingWord).map((r) => <TableRow key={r.key}>
            <TableCell sx={{ fontWeight: 600, whiteSpace: 'nowrap' }}>{t('costEstimate.iteration', { n: r.iteration })}</TableCell>
            {LINE_IDS.map((id) => <TableCell key={id} sx={{ color: r[id] === missingWord ? 'warning.main' : undefined }}>{r[id]}</TableCell>)}
            <TableCell sx={{ fontWeight: 600 }}>{r.original ? <Tooltip title={t('costEstimate.originalsTip', { list: r.original })}><span>{r.subtotal}</span></Tooltip> : r.subtotal}</TableCell>
          </TableRow>)}
          <TableRow><TableCell sx={{ fontWeight: 600 }}>{t('costEstimate.subtotal')}</TableCell><TableCell colSpan={4} /><TableCell sx={{ fontWeight: 600 }}>{tv.converted ? <Tooltip title={t('costEstimate.originalsTip', { list: formatMoney(computed.subtotal) })}><span>{tv.subtotal}</span></Tooltip> : tv.subtotal}</TableCell></TableRow>
          <TableRow><TableCell sx={{ fontWeight: 600 }}>{t('costEstimate.contingency', { pct: computed.contingency_pct })}</TableCell><TableCell colSpan={4} /><TableCell>{tv.converted ? <Tooltip title={t('costEstimate.originalsTip', { list: formatMoney(computed.contingency) })}><span>{tv.contingency}</span></Tooltip> : tv.contingency}</TableCell></TableRow>
          <TableRow><TableCell sx={{ fontWeight: 700 }}>{t('costEstimate.total')}</TableCell><TableCell colSpan={4} /><TableCell sx={{ fontWeight: 700 }}>{tv.converted ? <Tooltip title={t('costEstimate.originalsTip', { list: tv.original })}><span data-testid="review-total">{tv.total}</span></Tooltip> : <span data-testid="review-total">{tv.total}</span>}</TableCell></TableRow>
        </TableBody>
      </Table></Box>
      {tv.converted && fxLine(estimate.fx, t) && <Typography data-testid="fx-note" sx={{ fontSize: 12.5, color: 'var(--text-2)' }}>{fxLine(estimate.fx, t)}</Typography>}
      {answer.fx_status?.state === 'stale' && <Alert severity="warning" sx={{ py: 0 }}>{t('costEstimate.fx.stale', { date: answer.fx_status.date || '' })}</Alert>}
      {!tv.converted && (tv.missingRates.length > 0
        ? <Alert severity="info" sx={{ py: 0 }}>{estimate.fx ? t('costEstimate.fx.noneFor', { list: tv.missingRates.join(', ') }) : t('costEstimate.fx.none')}</Alert>
        : computed.currencies.length > 1 && <Alert severity="info" sx={{ py: 0 }}>{t('costEstimate.multiCurrency', { list: computed.currencies.join(', ') })}</Alert>)}
      <Stack direction="row" spacing={0.6} flexWrap="wrap" useFlexGap>{Object.entries(counts).map(([k, n]) => <Chip key={k} size="small" color={CHIP_COLOR[k] ?? 'default'} label={`${k === 'missing' ? missingWord : t(`costEstimate.source.${k}`)} · ${n}`} />)}</Stack>
    </StepCard>
    <StepCard title={t('costEstimate.missingTitle', { count: computed.missing_count })} tip={t('costEstimate.missingTip')}>
      {computed.missing.length === 0 ? <Typography sx={{ fontSize: 13, color: 'success.main' }}>{t('costEstimate.noneMissing')}</Typography>
        : <Stack spacing={0.5}>{computed.missing.map((m) => <ButtonBase key={`${m.iteration}-${m.line}`} onClick={() => { const i = stepIndexOfMissing(steps, m); if (i >= 0) setStep(i); }}
          sx={{ justifyContent: 'flex-start', textAlign: 'left', minHeight: 32, px: 1, borderRadius: '8px', gap: 1, '&:hover': { bgcolor: 'var(--panel-2, rgba(0,0,0,.04))' } }}>
          <Chip size="small" variant="outlined" color="warning" label={`${t('costEstimate.iterationShort', { n: m.iteration })} · ${lineLabel(m.line)}`} />
          <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{m.reason === 'not filled' ? '' : m.reason}</Typography>
        </ButtonBase>)}</Stack>}
    </StepCard>
    <StepCard title={t('costEstimate.snapshots')} tip={t('costEstimate.snapshotTip')}>
      {snapshots.length === 0 ? <Typography sx={{ fontSize: 13, color: 'var(--text-3)' }}>{t('costEstimate.noSnapshots')}</Typography>
        : <Stack spacing={0.6}>{snapshots.map((s) => <Tooltip key={s.id} title={s.diff.slice(0, 12).map((d) => d.path).join('\n') || t('costEstimate.firstSnapshot')}><Typography sx={{ fontSize: 12.5, color: 'var(--text-1)' }}>
          #{s.n} · {s.created_at.slice(0, 16).replace('T', ' ')} · {s.note || '—'} · {snapshotTotalText(s)} · {t('costEstimate.changes', { count: s.diff.length })}</Typography></Tooltip>)}</Stack>}
    </StepCard>
  </Stack>;

  return <Box data-testid="cost-estimate" sx={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0, '& .MuiInputBase-root': { bgcolor: 'var(--input-bg)', borderRadius: '10px' } }}>
    {header}
    {conflict && <Alert severity="warning" action={<Button color="inherit" size="small" onClick={() => void load()}>{t('costEstimate.reload')}</Button>}>{t('costEstimate.conflict')}</Alert>}
    {error && estimate && <Alert severity="error">{error}</Alert>}
    {report && <Alert severity="info" onClose={() => setReport(null)}>{t('costEstimate.suggested', { count: report.filled_count, analog: report.analogs.manufacturing.ref ?? '—' })}{report.notes.length > 0 ? ` ${report.notes.join('; ')}` : ''}</Alert>}
    <Box sx={{ display: 'flex', flexDirection: wide ? 'row' : 'column', gap: wide ? 3 : 1.5, alignItems: 'flex-start', minWidth: 0 }}>
      <Box sx={{ width: wide ? 200 : '100%', flex: wide ? '0 0 200px' : undefined, position: wide ? 'sticky' : 'static', top: 8 }}>{nav}</Box>
      <Box sx={{ flex: 1, minWidth: 0, maxWidth: 1000, display: 'flex', flexDirection: 'column', gap: 2, width: '100%' }}>
        {current.kind === 'settings' ? settingsStep : current.kind === 'review' ? reviewStep : iterationStep(current.iteration ?? 1)}
        <Stack direction="row" alignItems="center" spacing={1.5} sx={{ position: 'sticky', bottom: 0, zIndex: 2, bgcolor: 'var(--panel)', border: '1px solid var(--line)', borderRadius: '14px', px: 1.5, py: 1 }}>
          <Button variant="outlined" disabled={step === 0} onClick={() => setStep(step - 1)} sx={{ minHeight: 48, textTransform: 'none', borderRadius: '12px' }}>{t('previous')}</Button>
          <Typography sx={{ flex: 1, textAlign: 'center', fontSize: 13, color: 'var(--text-3)' }}>{t('costEstimate.stepOf', { step: step + 1, total: steps.length })}</Typography>
          {step < steps.length - 1 && <Button variant="contained" onClick={() => setStep(step + 1)} sx={{ minHeight: 48, textTransform: 'none', borderRadius: '12px', fontWeight: 600 }}>{t('costEstimate.nextTo', { title: stepTitle(steps[step + 1]) })}</Button>}
        </Stack>
      </Box>
    </Box>
    <Dialog open={snapOpen} onClose={() => !busy && setSnapOpen(false)} fullWidth maxWidth="sm">
      <DialogTitle>{t('costEstimate.snapshot')}</DialogTitle>
      <DialogContent><TextField autoFocus fullWidth multiline minRows={2} sx={{ mt: 1 }} label={t('costEstimate.snapshotNote')} value={snapNote} onChange={(e) => setSnapNote(e.target.value)} inputProps={{ maxLength: 500 }} /></DialogContent>
      <DialogActions><Button disabled={busy} onClick={() => setSnapOpen(false)}>{t('cancel')}</Button><Button variant="contained" disabled={busy || saving} onClick={() => void freeze()}>{t('costEstimate.freeze')}</Button></DialogActions>
    </Dialog>
  </Box>;
}
