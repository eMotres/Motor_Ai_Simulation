/** Project cost estimate wizard (owner 2026-11-02, ADMIN ONLY): Project settings -> Iteration 1 Manufacturing / Testing /
 *  Logistics -> Iteration 2 ... -> Project-wide -> Review.  Same look as the specification wizard (StepCard, BigNumber,
 *  touch sizes); every line shows where its number came from.  Autosaves with the server's optimistic revision. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Box, Button, ButtonBase, Chip, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Select,
  Stack, Switch, Table, TableBody, TableCell, TableHead, TableRow, TextField, Tooltip, Typography, useMediaQuery,
} from '@mui/material';
import CheckIcon from '@mui/icons-material/Check';
import HelpTip from '../../../../components/common/HelpTip';
import { BigNumber, StepCard, TOUCH } from '../specWizard/ui';
import { developmentApi, DevelopmentApiError } from '../../api';
import {
  buildCostSteps, editLine, formatMoney, langOf, originChip, reviewRows, setIterationSetting, setLineStatus, setSetting,
  sourceCounts, stepIndexOfMissing, stepLines, stepState, todayIso, txt,
  type CostComputed, type CostEstimate, type CostEstimateAnswer, type CostField, type CostItem, type CostLang, type CostLine,
  type CostSchema, type CostSnapshot, type CostStep, type LineRef, type T,
} from '../../costEstimate';

const CHIP_COLOR: Record<string, 'default' | 'primary' | 'secondary' | 'success' | 'warning' | 'info'> = {
  analog: 'info', test_history: 'secondary', model: 'primary', manual: 'success', rule: 'default', missing: 'warning', not_applicable: 'default',
};

function SourceChip({ line, schema, lang, t }: { line: CostLine; schema: CostSchema; lang: CostLang; t: T }) {
  const chip = originChip(line, schema, lang, t);
  return <Tooltip title={chip.tip} enterTouchDelay={0}><Chip size="small" variant={chip.kind === 'not_applicable' ? 'outlined' : 'filled'} color={CHIP_COLOR[chip.kind] ?? 'default'} label={chip.label} sx={{ height: 22, fontSize: 11 }} /></Tooltip>;
}

function SettingField({ field, value, onChange, lang }: { field: CostField; value: unknown; onChange: (v: unknown) => void; lang: CostLang }) {
  const label = txt(field.label, lang);
  const head = <Stack direction="row" spacing={0.5} alignItems="center"><Typography sx={{ fontSize: 13, color: 'var(--text-1)' }}>{label}</Typography><HelpTip title={txt(field.help, lang)} /></Stack>;
  if (field.type === 'bool') return <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ minHeight: TOUCH }}>{head}<Switch checked={!!value} onChange={(e) => onChange(e.target.checked)} inputProps={{ 'aria-label': label }} /></Stack>;
  if (field.type === 'enum') return <Stack spacing={0.6}>{head}<Select size="small" value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} inputProps={{ 'aria-label': label }}>
    {(field.options ?? []).map((o) => <MenuItem key={o} value={o}>{field.option_labels?.[o] ? txt(field.option_labels[o], lang) : o}</MenuItem>)}
  </Select></Stack>;
  return <Stack spacing={0.6}>{head}<BigNumber label="" unit={field.id.endsWith('_pct') ? '%' : ''} size="normal" min={field.min} value={typeof value === 'number' ? value : null}
    ariaLabel={label} onChange={(n) => onChange(n === null ? (field.type === 'int' ? field.min ?? 1 : null) : field.type === 'int' ? Math.round(n) : n)} /></Stack>;
}

/** One line of a step: label + help, quantity x unit cost in a currency (or the % of a derived / percentage line), the amount,
 *  the source chip with where the number came from, and Not applicable. */
function LineRow({ item, line, amount, schema, lang, t, onEdit, onStatus }: {
  item: CostItem; line: CostLine; amount: string; schema: CostSchema; lang: CostLang; t: T;
  onEdit: (patch: Parameters<typeof editLine>[2]) => void; onStatus: (s: CostLine['status']) => void;
}) {
  const na = line.status === 'not_applicable';
  const pct = item.unit === 'pct';
  const unit = txt(schema.units[line.unit] ?? schema.units[item.unit], lang);
  return <Box sx={{ display: 'grid', gap: 1, alignItems: 'end', gridTemplateColumns: { xs: '1fr 1fr', lg: 'minmax(170px, 1.6fr) minmax(120px, 1fr) minmax(120px, 1fr) 92px minmax(110px, 1fr) minmax(130px, 1fr)' }, py: 1, borderTop: '1px solid var(--line-soft)', opacity: na ? 0.55 : 1 }}>
    <Stack spacing={0.4} sx={{ gridColumn: { xs: '1 / -1', lg: 'auto' }, minWidth: 0, alignSelf: 'center' }}>
      <Stack direction="row" spacing={0.5} alignItems="center"><Typography sx={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text-0)' }}>{txt(item.label, lang)}</Typography><HelpTip title={txt(item.help, lang)} /></Stack>
      <Stack direction="row" spacing={0.5} alignItems="center" flexWrap="wrap" useFlexGap><SourceChip line={line} schema={schema} lang={lang} t={t} />
        <ButtonBase onClick={() => onStatus(na ? 'missing' : 'not_applicable')} sx={{ fontSize: 11, color: 'var(--text-3)', px: 0.5, minHeight: 24, borderRadius: '6px' }}>{na ? t('costEstimate.applicable') : t('costEstimate.notApplicable')}</ButtonBase></Stack>
    </Stack>
    {line.derive ? <>
      <BigNumber label={t('costEstimate.ofIterationOne')} unit="%" size="normal" min={0} value={line.derive.pct} disabled={na} ariaLabel={`${txt(item.label, lang)}: ${t('costEstimate.ofIterationOne')}`} onChange={(n) => onEdit({ derivePct: n ?? 0 })} />
      <Box sx={{ display: { xs: 'none', lg: 'block' } }} /><Box sx={{ display: { xs: 'none', lg: 'block' } }} />
    </> : pct ? <>
      <BigNumber label={t('costEstimate.percent')} unit="%" size="normal" min={0} value={line.qty} disabled={na} placeholder={item.hint !== undefined ? String(item.hint) : undefined} ariaLabel={`${txt(item.label, lang)}: %`} onChange={(n) => onEdit({ qty: n })} />
      <Box sx={{ display: { xs: 'none', lg: 'block' } }} /><Box sx={{ display: { xs: 'none', lg: 'block' } }} />
    </> : <>
      <BigNumber label={t('costEstimate.qty')} unit={unit} size="normal" min={0} value={line.qty} disabled={na} ariaLabel={`${txt(item.label, lang)}: ${t('costEstimate.qty')}`} onChange={(n) => onEdit({ qty: n })} />
      <BigNumber label={t('costEstimate.unitCost')} unit="" size="normal" min={0} value={line.unit_cost} disabled={na} ariaLabel={`${txt(item.label, lang)}: ${t('costEstimate.unitCost')}`} onChange={(n) => onEdit({ unit_cost: n })} />
      <Stack spacing={0.6}><Typography sx={{ fontSize: 12.5, color: 'var(--text-2)' }}>{t('costEstimate.currency')}</Typography>
        <Select size="small" value={line.currency} disabled={na} onChange={(e) => onEdit({ currency: String(e.target.value) })} inputProps={{ 'aria-label': `${txt(item.label, lang)}: ${t('costEstimate.currency')}` }}>
          {schema.currencies.map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}
        </Select></Stack>
    </>}
    <Stack spacing={0.6} sx={{ minWidth: 0 }}><Typography sx={{ fontSize: 12.5, color: 'var(--text-2)' }}>{t('costEstimate.amount')}</Typography>
      <Typography sx={{ fontSize: 15, fontWeight: 600, fontVariantNumeric: 'tabular-nums', minHeight: TOUCH, display: 'flex', alignItems: 'center', color: 'var(--text-0)', overflowWrap: 'anywhere' }}>{amount}</Typography></Stack>
    <Tooltip title={t('costEstimate.noteTip')}><TextField size="small" value={line.note} disabled={na} placeholder={t('costEstimate.note')} onChange={(e) => onEdit({ note: e.target.value })} inputProps={{ 'aria-label': `${txt(item.label, lang)}: ${t('costEstimate.note')}`, maxLength: 500 }} sx={{ gridColumn: { xs: '1 / -1', lg: 'auto' } }} /></Tooltip>
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
  latest.current = estimate;

  const apply = (a: CostEstimateAnswer) => { setAnswer(a); setEstimate(a.estimate); setComputed(a.computed); setConflict(false); };
  const load = useCallback(async () => {
    setError('');
    try { apply(await developmentApi.costEstimate(projectId)); setSnapshots(await developmentApi.costEstimateSnapshots(projectId)); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }, [projectId]);
  useEffect(() => { void load(); return () => { if (timer.current) window.clearTimeout(timer.current); }; }, [load]);

  const save = async () => {
    const cur = latest.current;
    if (!cur) return;
    const mark = edits.current;
    setSaving(true);
    try {
      const a = await developmentApi.saveCostEstimate(projectId, cur);
      setAnswer(a); setComputed(a.computed);
      if (edits.current === mark) setEstimate(a.estimate);
      else { setEstimate((e) => (e ? { ...e, revision: a.estimate.revision } : e)); schedule(); }
      setError('');
    } catch (e) {
      if (e instanceof DevelopmentApiError && e.status === 409) setConflict(true);
      else setError(e instanceof Error ? e.message : String(e));
    } finally { setSaving(false); }
  };
  const schedule = () => { if (timer.current) window.clearTimeout(timer.current); timer.current = window.setTimeout(() => { timer.current = null; void save(); }, 700); };
  const change = (next: CostEstimate) => { edits.current += 1; setEstimate(next); schedule(); };

  const schema = answer?.schema ?? null;
  const steps = useMemo(() => (schema && estimate ? buildCostSteps(schema, estimate) : []), [schema, estimate?.iterations.length]);
  if (error && !estimate) return <Alert severity="error" action={<Button color="inherit" size="small" onClick={() => void load()}>{t('costEstimate.retry')}</Button>}>{error}</Alert>;
  if (!schema || !estimate || !computed || !answer) return <CircularProgress size={18} />;
  const current: CostStep = steps[Math.min(step, steps.length - 1)];
  const today = todayIso();

  const suggest = async (refresh: boolean) => {
    if (timer.current) { window.clearTimeout(timer.current); timer.current = null; await save(); }
    setBusy(true);
    try { const a = await developmentApi.suggestCostEstimate(projectId, latest.current?.revision ?? estimate.revision, refresh); apply(a); setReport(a.report ?? null); }
    catch (e) { if (e instanceof DevelopmentApiError && e.status === 409) setConflict(true); else setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  const freeze = async () => {
    setBusy(true);
    try { await developmentApi.snapshotCostEstimate(projectId, estimate.revision, snapNote); setSnapOpen(false); setSnapNote(''); setSnapshots(await developmentApi.costEstimateSnapshots(projectId)); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  const stepTitle = (s: CostStep) => s.kind === 'settings' ? t('costEstimate.steps.settings') : s.kind === 'review' ? t('costEstimate.steps.review')
    : s.iteration ? t('costEstimate.steps.iterationGroup', { n: s.iteration, group: txt(s.group!.label, lang) }) : txt(s.group!.label, lang);
  const amountOf = (ref: LineRef): string => {
    const r = ref.iteration === null ? computed.project.lines[ref.item] : computed.iterations.find((it) => it.index === ref.iteration)?.lines[ref.item];
    return r?.amount ? formatMoney(r.amount) : '—';
  };

  const header = <Stack direction={{ xs: 'column', md: 'row' }} spacing={{ xs: 1, md: 2 }} alignItems={{ md: 'center' }} sx={{ bgcolor: 'var(--panel)', border: '1px solid var(--line)', borderRadius: '14px', px: { xs: 2, md: 3 }, py: 1.5 }}>
    <Box sx={{ flex: 1, minWidth: 0 }}>
      <Stack direction="row" spacing={1} alignItems="center"><Typography component="h1" sx={{ fontSize: 18, fontWeight: 600, color: 'var(--text-0)' }}>{t('costEstimate.title')}</Typography>
        <Tooltip title={t('costEstimate.adminOnlyTip')}><Chip size="small" label={t('costEstimate.adminOnly')} sx={{ height: 20, fontSize: 10.5 }} /></Tooltip></Stack>
      <Tooltip title={t('costEstimate.totalTip')}><Typography sx={{ fontSize: 13, color: 'var(--text-2)', fontVariantNumeric: 'tabular-nums' }}>{t('costEstimate.total')}: {formatMoney(computed.total)} · {t('costEstimate.missingCount', { count: computed.missing_count })}</Typography></Tooltip>
    </Box>
    <Tooltip title={t('costEstimate.suggestTip')}><span><Button variant="contained" disabled={busy} onClick={() => void suggest(false)} sx={{ textTransform: 'none', borderRadius: '10px', minHeight: TOUCH }}>{busy ? <CircularProgress size={16} /> : t('costEstimate.suggest')}</Button></span></Tooltip>
    <Tooltip title={t('costEstimate.refreshTip')}><span><Button disabled={busy} onClick={() => void suggest(true)} sx={{ textTransform: 'none', minHeight: TOUCH }}>{t('costEstimate.refresh')}</Button></span></Tooltip>
    <Tooltip title={t('costEstimate.snapshotTip')}><span><Button variant="outlined" disabled={busy || !answer.stored} onClick={() => setSnapOpen(true)} sx={{ textTransform: 'none', borderRadius: '10px', minHeight: TOUCH }}>{t('costEstimate.snapshot')}</Button></span></Tooltip>
    <Typography role="status" sx={{ fontSize: 12.5, display: 'flex', alignItems: 'center', gap: 0.6, whiteSpace: 'nowrap', color: error ? 'error.main' : saving ? 'var(--text-3)' : 'success.main' }}>
      {!error && !saving && <CheckIcon sx={{ fontSize: 15 }} />}{error ? t('costEstimate.saveFailed') : saving ? t('costEstimate.saving') : t('costEstimate.saved')}
    </Typography>
  </Stack>;

  const nav = <Box component="nav" aria-label={t('costEstimate.stepsNav')} sx={{ display: 'flex', flexDirection: wide ? 'column' : 'row', gap: 0.5, overflowX: wide ? 'visible' : 'auto' }}>
    {steps.map((s, i) => {
      const st = s.kind === 'group' ? stepState(s, estimate) : null;
      const active = i === step;
      return <Tooltip key={s.id} title={st ? t('costEstimate.stepMissing', { count: st.missing }) : ''} placement="right"><ButtonBase onClick={() => setStep(i)} aria-current={active ? 'step' : undefined}
        sx={{ display: 'flex', gap: 1, alignItems: 'center', justifyContent: 'flex-start', textAlign: 'left', minHeight: 44, px: 1.25, borderRadius: '10px', flex: wide ? undefined : '0 0 auto', bgcolor: active ? 'var(--panel)' : 'transparent', boxShadow: active ? '0 1px 2px rgba(0,0,0,.12)' : 'none', '&:hover': { bgcolor: 'var(--panel)' } }}>
        <Box component="span" aria-hidden sx={{ width: 24, height: 24, flex: '0 0 24px', borderRadius: '50%', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 600,
          bgcolor: active ? 'primary.main' : st?.state === 'done' ? 'success.main' : 'transparent', color: active || st?.state === 'done' ? '#fff' : 'var(--text-3)', border: active || st?.state === 'done' ? 'none' : '2px solid var(--line)' }}>{st?.state === 'done' && !active ? <CheckIcon sx={{ fontSize: 14 }} /> : i + 1}</Box>
        <Typography component="span" sx={{ fontSize: 13.5, fontWeight: active ? 600 : 500, color: 'var(--text-0)', whiteSpace: wide ? 'normal' : 'nowrap' }}>{stepTitle(s)}</Typography>
        {st && st.missing > 0 && <Typography component="span" sx={{ fontSize: 11, color: 'var(--text-3)', ml: 'auto' }}>{st.missing}</Typography>}
      </ButtonBase></Tooltip>;
    })}
  </Box>;

  const settingsStep = <Stack spacing={2}>
    <StepCard title={t('costEstimate.projectSettings')} tip={t('costEstimate.projectSettingsTip')}>
      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' } }}>
        {schema.settings.map((f) => <SettingField key={f.id} field={f} value={estimate.settings[f.id]} lang={lang} onChange={(v) => change(setSetting(estimate, f.id, v))} />)}
      </Box>
    </StepCard>
    <StepCard title={t('costEstimate.basis')} tip={t('costEstimate.basisTip')}>
      <Stack spacing={0.6}><Typography sx={{ fontSize: 13, color: 'var(--text-1)' }}>{t('costEstimate.targetMotor')}</Typography>
        <Select size="small" displayEmpty value={estimate.settings.target_motor ? `${estimate.settings.target_motor.die}/${estimate.settings.target_motor.config}` : ''}
          onChange={(e) => { const v = String(e.target.value); const m = answer.linked_motors.find((x) => `${x.die}/${x.config}` === v); change(setSetting(estimate, 'target_motor', m ? { die: m.die, config: m.config } : null)); }} inputProps={{ 'aria-label': t('costEstimate.targetMotor') }}>
          <MenuItem value="">{t('costEstimate.noMotor')}</MenuItem>
          {answer.linked_motors.map((m) => <MenuItem key={`${m.die}/${m.config}`} value={`${m.die}/${m.config}`}>{m.die} / {m.config}</MenuItem>)}
        </Select></Stack>
      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(4, 1fr)' } }}>
        {schema.basis_fields.map((k) => { const from = estimate.basis[k]; return <BigNumber key={k} label={t(`costEstimate.basisField.${k}`)} unit={k.endsWith('_kg') ? 'kg' : k.endsWith('_mm') ? 'mm' : 'kW'} size="normal" min={0}
          value={estimate.settings.basis_overrides[k] ?? null} placeholder={typeof from === 'number' ? String(from) : undefined} ariaLabel={t(`costEstimate.basisField.${k}`)}
          onChange={(n) => { const o = { ...estimate.settings.basis_overrides }; if (n === null) delete o[k]; else o[k] = n; change(setSetting(estimate, 'basis_overrides', o)); }} />; })}
      </Box>
      <Tooltip title={t('costEstimate.analogsTip')}><TextField size="small" label={t('costEstimate.analogs')} value={estimate.settings.analogs.join(', ')} placeholder="DIE/config, DIE/config"
        onChange={(e) => change(setSetting(estimate, 'analogs', e.target.value.split(',').map((x) => x.trim()).filter((x) => x.includes('/'))))} /></Tooltip>
    </StepCard>
  </Stack>;

  const groupStep = (s: CostStep) => {
    const rows = stepLines(s, estimate);
    const it = s.iteration ? estimate.iterations.find((x) => x.index === s.iteration) : undefined;
    const sections = Array.from(new Set(rows.map((r) => r.section)));
    return <Stack spacing={2}>
      {it && s.group?.id === 'testing' && <StepCard title={t('costEstimate.iterationSettings', { n: it.index })}>
        {schema.iteration_settings.filter((f) => f.id === 'bench').map((f) => <SettingField key={f.id} field={f} value={it.settings[f.id]} lang={lang} onChange={(v) => change(setIterationSetting(estimate, it.index, f.id, v))} />)}
      </StepCard>}
      {sections.map((sec) => <StepCard key={sec.id} title={txt(sec.label, lang)}>
        <Box>{rows.filter((r) => r.section === sec).map(({ item, line }) => {
          const ref: LineRef = { iteration: s.iteration ?? null, item: item.id };
          return <LineRow key={item.id} item={item} line={line} amount={amountOf(ref)} schema={schema} lang={lang} t={t}
            onEdit={(patch) => change(editLine(estimate, ref, patch, who, today, item.unit === 'pct'))}
            onStatus={(st) => change(setLineStatus(estimate, ref, st, who, today))} />;
        })}</Box>
      </StepCard>)}
    </Stack>;
  };

  const counts = sourceCounts(estimate);
  const reviewStep = <Stack spacing={2}>
    <StepCard title={t('costEstimate.byIteration')} tip={t('costEstimate.byIterationTip')}>
      <Box sx={{ overflowX: 'auto' }}><Table size="small">
        <TableHead><TableRow><TableCell /><TableCell>{t('costEstimate.group.manufacturing')}</TableCell><TableCell>{t('costEstimate.group.testing')}</TableCell><TableCell>{t('costEstimate.group.logistics')}</TableCell><TableCell>{t('costEstimate.subtotal')}</TableCell><TableCell>{t('costEstimate.milestone')}</TableCell></TableRow></TableHead>
        <TableBody>
          {reviewRows(computed).map((r) => { const ms = r.iteration ? computed.milestones.find((m) => m.iteration === r.iteration) : undefined; const it = r.iteration ? estimate.iterations.find((x) => x.index === r.iteration) : undefined; return <TableRow key={r.key}>
            <TableCell sx={{ fontWeight: 600, whiteSpace: 'nowrap' }}>{r.iteration ? t('costEstimate.iteration', { n: r.iteration }) : t('costEstimate.group.project')}</TableCell>
            <TableCell>{r.manufacturing}</TableCell><TableCell>{r.testing}</TableCell><TableCell>{r.logistics}</TableCell><TableCell sx={{ fontWeight: 600 }}>{r.subtotal}</TableCell>
            <TableCell sx={{ minWidth: 120 }}>{it && <Tooltip title={t('costEstimate.milestoneTip', { pct: ms?.suggested_pct ?? '—' })}><TextField size="small" type="number" value={typeof it.settings.milestone_pct === 'number' ? it.settings.milestone_pct : ''} placeholder={ms?.suggested_pct !== null && ms?.suggested_pct !== undefined ? String(ms.suggested_pct) : ''}
              onChange={(e) => change(setIterationSetting(estimate, it.index, 'milestone_pct', e.target.value === '' ? null : Number(e.target.value)))} inputProps={{ 'aria-label': `${t('costEstimate.milestone')} ${it.index}`, min: 0, max: 100 }} sx={{ width: 90 }} /></Tooltip>}</TableCell>
          </TableRow>; })}
          <TableRow><TableCell sx={{ fontWeight: 600 }}>{t('costEstimate.subtotal')}</TableCell><TableCell colSpan={3} /><TableCell sx={{ fontWeight: 600 }}>{formatMoney(computed.subtotal)}</TableCell><TableCell /></TableRow>
          <TableRow><TableCell sx={{ fontWeight: 600 }}>{t('costEstimate.contingency', { pct: computed.contingency_pct })}</TableCell><TableCell colSpan={3} /><TableCell>{formatMoney(computed.contingency)}</TableCell><TableCell /></TableRow>
          <TableRow><TableCell sx={{ fontWeight: 700 }}>{t('costEstimate.total')}</TableCell><TableCell colSpan={3} /><TableCell sx={{ fontWeight: 700 }}>{formatMoney(computed.total)}</TableCell><TableCell /></TableRow>
        </TableBody>
      </Table></Box>
      {computed.currencies.length > 1 && <Alert severity="info" sx={{ py: 0 }}>{t('costEstimate.multiCurrency', { list: computed.currencies.join(', ') })}</Alert>}
      {(() => { const set = computed.milestones.filter((m) => m.pct !== null); const sum = set.reduce((a, m) => a + (m.pct ?? 0), 0); return set.length > 0 && Math.abs(sum - 100) > 0.05 ? <Alert severity="warning" sx={{ py: 0 }}>{t('costEstimate.milestoneSum', { sum: Math.round(sum * 10) / 10 })}</Alert> : null; })()}
      <Stack direction="row" spacing={0.6} flexWrap="wrap" useFlexGap>{Object.entries(counts).map(([k, n]) => <Chip key={k} size="small" color={CHIP_COLOR[k] ?? 'default'} label={`${k === 'missing' || k === 'not_applicable' ? t(`costEstimate.status.${k}`) : t(`costEstimate.source.${k}`)} · ${n}`} />)}</Stack>
    </StepCard>
    <StepCard title={t('costEstimate.missingTitle', { count: computed.missing_count })} tip={t('costEstimate.missingTip')}>
      {computed.missing.length === 0 ? <Typography sx={{ fontSize: 13, color: 'success.main' }}>{t('costEstimate.noneMissing')}</Typography>
        : <Stack direction="row" spacing={0.6} flexWrap="wrap" useFlexGap>{computed.missing.slice(0, 200).map((m) => { const item = schema.groups.flatMap((g) => g.sections.flatMap((x) => x.items)).find((x) => x.id === m.item); return <Chip key={`${m.iteration}-${m.item}`} size="small" variant="outlined" color="warning"
          label={`${m.iteration ? `${t('costEstimate.iterationShort', { n: m.iteration })} · ` : ''}${item ? txt(item.label, lang) : m.item}`} onClick={() => { const i = stepIndexOfMissing(steps, m); if (i >= 0) setStep(i); }} />; })}</Stack>}
    </StepCard>
    <StepCard title={t('costEstimate.snapshots')} tip={t('costEstimate.snapshotTip')}>
      {snapshots.length === 0 ? <Typography sx={{ fontSize: 13, color: 'var(--text-3)' }}>{t('costEstimate.noSnapshots')}</Typography>
        : <Stack spacing={0.6}>{snapshots.map((s) => <Tooltip key={s.id} title={s.diff.slice(0, 12).map((d) => d.path).join('\n') || t('costEstimate.firstSnapshot')}><Typography sx={{ fontSize: 12.5, color: 'var(--text-1)' }}>
          #{s.n} · {s.created_at.slice(0, 16).replace('T', ' ')} · {s.note || '—'} · {formatMoney(s.totals.total)} · {t('costEstimate.changes', { count: s.diff.length })}</Typography></Tooltip>)}</Stack>}
    </StepCard>
  </Stack>;

  return <Box data-testid="cost-estimate" sx={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0, '& .MuiInputBase-root': { bgcolor: 'var(--input-bg)', borderRadius: '10px' } }}>
    {header}
    {conflict && <Alert severity="warning" action={<Button color="inherit" size="small" onClick={() => void load()}>{t('costEstimate.reload')}</Button>}>{t('costEstimate.conflict')}</Alert>}
    {error && estimate && <Alert severity="error">{error}</Alert>}
    {report && <Alert severity="info" onClose={() => setReport(null)}>{t('costEstimate.suggested', { count: report.filled_count, analogs: report.analogs.map((a) => a.ref).join(', ') || '—' })}</Alert>}
    <Box sx={{ display: 'flex', flexDirection: wide ? 'row' : 'column', gap: wide ? 3 : 1.5, alignItems: 'flex-start', minWidth: 0 }}>
      <Box sx={{ width: wide ? 250 : '100%', flex: wide ? '0 0 250px' : undefined, position: wide ? 'sticky' : 'static', top: 8 }}>{nav}</Box>
      <Box sx={{ flex: 1, minWidth: 0, maxWidth: 1100, display: 'flex', flexDirection: 'column', gap: 2, width: '100%' }}>
        <Typography component="h2" sx={{ fontSize: { xs: 20, md: 24 }, fontWeight: 700, color: 'var(--text-0)' }}>{stepTitle(current)}</Typography>
        {current.kind === 'settings' ? settingsStep : current.kind === 'review' ? reviewStep : groupStep(current)}
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
