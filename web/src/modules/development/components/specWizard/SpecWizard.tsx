import { useEffect, useRef, useState } from 'react';
import { Alert, Box, Button, IconButton, LinearProgress, Stack, Tooltip, Typography, useMediaQuery } from '@mui/material';
import ChatBubbleOutlineIcon from '@mui/icons-material/ChatBubbleOutline';
import CheckIcon from '@mui/icons-material/Check';
import type { Completeness, ProjectSummary, ProjectThreadMessage } from '../../types';
import type { StepEnv } from './env';
import ReviewStep from './ReviewStep';
import StepBody from './StepBody';
import { StepBar, StepList } from './StepNav';
import { overallProgress, stepCommentRefs, stepIndexForField } from './steps';
import { TOUCH } from './ui';

export interface SpecWizardProps {
  project: ProjectSummary;
  env: Omit<StepEnv, 'goToStep' | 'revealField'>;
  step: number;
  onStep: (index: number) => void;
  completeness?: Completeness;
  saving: boolean; saveError: string; onRetrySave: () => void; onReloadDraft: () => void;
  submitError: string[]; submitConflict: boolean; submitBusy: boolean; onSubmit: () => void;
  /** a field to open (a submit error, a review link): the wizard goes to its step, opens its group and focuses it */
  reveal: { field: string; n: number } | null;
  messages: ProjectThreadMessage[];
  onOpenFieldReview: (ref: string) => void;
  history: React.ReactNode;
  banner?: React.ReactNode;
  /** the assistant panel; `docked` = it is the right column (wide screens), otherwise it floats at the edge */
  assistant: (docked: boolean) => React.ReactNode;
  readOnlyLabel?: string;
}

/** The specification wizard (owner 2026-10-27): header with the project and the completeness bar, a vertical stepper on the
 *  left (a top bar on a phone), one step at a time in the centre, the assistant on the right, Back / Next at the bottom. */
export default function SpecWizard(p: SpecWizardProps) {
  const { env, step, onStep, project, completeness, saving, saveError } = p;
  const { tr, draft, steps, canEditSpec } = env;
  const wide = useMediaQuery('(min-width:900px)');
  const docked = useMediaQuery('(min-width:1400px)');
  const current = steps[Math.min(step, steps.length - 1)];
  const fieldSteps = steps.filter((s) => s.kind !== 'review');
  const isReview = current.kind === 'review';
  const root = useRef<HTMLDivElement | null>(null);
  const [revealField, setRevealField] = useState<string | null>(null);

  // a new step starts at its top (not on the first render: the page may have restored it)
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    root.current?.scrollIntoView?.({ block: 'start' });
  }, [step]);

  // reveal a field: go to its step, open its optional group, scroll to it and focus its first input
  const pending = useRef<string | null>(null);
  const reveal = (fieldId: string) => {
    const index = stepIndexForField(steps, fieldId);
    if (index < 0) return;
    pending.current = fieldId; setRevealField(fieldId); onStep(index);
  };
  useEffect(() => { if (p.reveal) reveal(p.reveal.field); }, [p.reveal?.n]);
  useEffect(() => {
    const id = pending.current;
    if (!id) return;
    const handle = window.setTimeout(() => {
      const el = document.getElementById(`spec-field-${id}`);
      el?.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
      el?.querySelector<HTMLElement>('input,textarea,[role="combobox"],button')?.focus();
      pending.current = null; setRevealField(null);
    }, 60);
    return () => window.clearTimeout(handle);
  }, [step, revealField]);

  const progress = overallProgress(completeness);
  const sectionComments = p.messages.filter((m) => !m.resolved && stepCommentRefs(current).includes(m.field_ref ?? ''));
  const stepEnv: StepEnv = { ...env, goToStep: onStep, revealField };
  const openComments = p.messages.filter((m) => !m.resolved && !!m.field_ref);
  const next = steps[step + 1];
  const back = step > 0;

  const header = <Stack direction={{ xs: 'column', md: 'row' }} spacing={{ xs: 1.5, md: 3 }} alignItems={{ md: 'center' }} sx={{ bgcolor: 'var(--panel)', border: '1px solid var(--line)', borderRadius: '14px', px: { xs: 2, md: 3 }, py: 1.5 }}>
    <Box sx={{ flex: 1, minWidth: 0 }}>
      <Tooltip title={project.customer_org || ''}><Typography sx={{ fontSize: 12, color: 'var(--text-3)', overflowWrap: 'anywhere' }}>{tr('wizard.projectLabel', { title: project.title })}</Typography></Tooltip>
      <Typography component="h1" sx={{ fontSize: 18, fontWeight: 600, color: 'var(--text-0)' }}>{tr('wizard.title')}{!canEditSpec && <Tooltip title={tr('specReadOnlyTip')}><Typography component="span" sx={{ ml: 1.5, fontSize: 12, fontWeight: 400, color: 'var(--text-3)' }}>{tr('specReadOnly')}</Typography></Tooltip>}</Typography>
    </Box>
    <Tooltip title={tr('wizard.completenessBuckets', { answered: completeness?.answered ?? 0, unknown: completeness?.unknown ?? 0, missingInvalid: (completeness?.missing ?? 0) + (completeness?.invalid ?? 0) })}>
      <Box tabIndex={0} sx={{ width: { xs: '100%', md: 280 } }} aria-label={tr('wizard.progress', { answered: progress.answered, total: progress.total })}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5, color: 'var(--text-2)', mb: 0.6 }}><span>{tr('wizard.progress', { answered: progress.answered, total: progress.total })}</span><Box component="span" sx={{ fontWeight: 600, color: 'var(--text-0)' }}>{progress.pct} %</Box></Box>
        <LinearProgress variant="determinate" value={progress.pct} aria-hidden sx={{ height: 8, borderRadius: 4, bgcolor: 'var(--app-bg)' }} />
      </Box>
    </Tooltip>
    <Typography role="status" sx={{ fontSize: 12.5, display: 'flex', alignItems: 'center', gap: 0.6, color: saveError ? 'error.main' : saving ? 'var(--text-3)' : 'success.main', whiteSpace: 'nowrap' }}>
      {!saveError && !saving && <CheckIcon sx={{ fontSize: 15 }} />}{saveError ? tr('saveFailedShort') : saving ? tr('saving') : tr('wizard.saved')}
    </Typography>
  </Stack>;

  return <Box ref={root} data-testid="spec-wizard" sx={{
    display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0, scrollMarginTop: 8,
    '& .MuiInputBase-root': { bgcolor: 'var(--input-bg)', borderRadius: '10px', fontSize: 15, minHeight: TOUCH },
    '& .MuiOutlinedInput-notchedOutline': { borderColor: 'var(--line)' },
    '& .MuiInputBase-input': { boxSizing: 'border-box', px: '12px', py: '10px', height: 'auto' },
    '& .MuiSelect-select': { pr: '32px !important' },
    '& .MuiToggleButton-root': { minHeight: TOUCH },
    '@media (pointer: coarse)': { '& .MuiIconButton-root': { minWidth: TOUCH, minHeight: TOUCH } },
  }}>
    {header}
    <Box sx={{ display: 'flex', gap: 3, alignItems: 'flex-start', minWidth: 0 }}>
      {wide && <Box sx={{ width: 260, flex: '0 0 260px', position: 'sticky', top: 8 }}><StepList steps={steps} draft={draft} current={step} onSelect={onStep} tr={tr} /></Box>}
      <Box sx={{ flex: 1, minWidth: 0, maxWidth: 980, display: 'flex', flexDirection: 'column', gap: 2 }}>
        {!wide && <StepBar steps={steps} draft={draft} current={step} onSelect={onStep} tr={tr} />}
        <Box id={`spec-card-${current.id}`} sx={{ scrollMarginTop: 12 }}>
          {!isReview && <Stack direction="row" alignItems="flex-start" spacing={1} sx={{ mb: 2 }}>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography component="h1" sx={{ fontSize: { xs: 22, md: 26 }, fontWeight: 700, color: 'var(--text-0)', lineHeight: 1.25 }}>{tr(current.title)}</Typography>
              <Typography sx={{ fontSize: 14.5, color: 'var(--text-2)', mt: 0.5 }}>{tr(current.summary)}</Typography>
            </Box>
            <Tooltip title={tr('sectionReview')}><IconButton aria-label={`${tr('sectionReview')}: ${tr(current.title)}`} onClick={() => p.onOpenFieldReview(`section:${current.id}`)} sx={{ color: sectionComments.length ? 'primary.main' : 'var(--text-4)', minWidth: TOUCH, minHeight: TOUCH }}><ChatBubbleOutlineIcon sx={{ fontSize: 18 }} />{sectionComments.length > 0 && <Box component="span" sx={{ fontSize: 11, ml: 0.3 }}>{sectionComments.length}</Box>}</IconButton></Tooltip>
          </Stack>}
          {isReview
            ? <ReviewStep env={stepEnv} completeness={completeness} submitErrors={p.submitError} submitConflict={p.submitConflict} onReload={p.onReloadDraft} onSubmit={p.onSubmit} submitBusy={p.submitBusy || saving}
              openComments={openComments} onOpenComment={p.onOpenFieldReview} onReveal={reveal} history={p.history} />
            : <StepBody env={stepEnv} step={current} />}
        </Box>
        {p.banner}
        {!isReview && p.submitError.length > 0 && <Alert severity="error"><Typography sx={{ fontWeight: 700, fontSize: 12 }}>{tr('submitBlocked')}</Typography>{p.submitError.map((x, i) => <Typography key={`${i}-${x}`} sx={{ fontSize: 12 }}>• {x}</Typography>)}</Alert>}
        {saveError && <Alert severity="error" action={<><Button color="inherit" size="small" onClick={p.onRetrySave}>{tr('retrySave')}</Button><Button color="inherit" size="small" onClick={p.onReloadDraft}>{tr('reloadDraft')}</Button></>}>{tr('saveFailed', { reason: saveError })}</Alert>}
        <Stack direction="row" alignItems="center" spacing={1.5} sx={{ position: 'sticky', bottom: 0, zIndex: 2, bgcolor: 'var(--panel)', border: '1px solid var(--line)', borderRadius: '14px', px: 1.5, py: 1, boxShadow: '0 -4px 12px rgba(0,0,0,.06)' }}>
          <Button variant="outlined" disabled={!back} onClick={() => onStep(step - 1)} sx={{ minHeight: 48, px: 2.5, textTransform: 'none', borderRadius: '12px' }}>{tr('previous')}</Button>
          <Typography sx={{ flex: 1, textAlign: 'center', fontSize: 13, color: 'var(--text-3)' }}>{isReview ? tr(current.title) : tr('wizard.stepOf', { step: step + 1, total: fieldSteps.length })}</Typography>
          {!isReview && next && <Button variant="contained" onClick={() => onStep(step + 1)} sx={{ minHeight: 48, px: 3, textTransform: 'none', borderRadius: '12px', fontWeight: 600 }}>{tr(next.kind === 'review' ? 'wizard.toReview' : 'wizard.nextTo', { title: tr(next.title) })}</Button>}
        </Stack>
      </Box>
      {docked && p.assistant(true)}
    </Box>
    {!docked && p.assistant(false)}
  </Box>;
}
