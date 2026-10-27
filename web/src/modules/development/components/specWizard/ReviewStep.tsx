import { Alert, Box, Button, Paper, Stack, Tooltip, Typography } from '@mui/material';
import type { Completeness, ProjectThreadMessage } from '../../types';
import { canSubmitSpec } from '../../submitFailure';
import type { StepEnv } from './env';
import { reviewItems, localized } from './reviewSummary';
import { stillOpen } from './steps';
import DeliveryConflict from './DeliveryConflict';
import { TOUCH } from './ui';

/** The review before submit: a summary card per step with large values and an Edit link, a soft "Still open" card for what
 *  is missing or marked "answer later", the open comments, the version history and the submit button. */
export default function ReviewStep({ env, completeness, submitErrors, submitConflict, onReload, onSubmit, submitBusy, openComments, onOpenComment, onReveal, history }: {
  env: StepEnv; completeness?: Completeness; submitErrors: string[]; submitConflict: boolean; onReload: () => void; onSubmit: () => void; submitBusy: boolean;
  openComments: ProjectThreadMessage[]; onOpenComment: (ref: string) => void; onReveal: (fieldId: string) => void; history: React.ReactNode;
}) {
  const { tr, lang, draft, steps, canEditSpec, profile } = env;
  const fieldSteps = steps.filter((s) => s.kind !== 'review');
  const open = stillOpen(fieldSteps, draft);
  const fieldOf = (id: string) => fieldSteps.flatMap((s) => s.fields).find((f) => f.id === id);
  const openCount = (completeness?.unknown ?? 0) + (completeness?.missing ?? 0) + (completeness?.invalid ?? 0);
  const cards = fieldSteps.map((step) => ({ step, index: steps.indexOf(step), items: reviewItems(step, draft, { lang, t: tr, profile }) })).filter((c) => c.items.length > 0);
  return <Stack spacing={2.5} sx={{ minWidth: 0 }}>
    <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} alignItems={{ md: 'flex-end' }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography component="h1" tabIndex={-1} sx={{ fontSize: { xs: 22, md: 26 }, fontWeight: 700, color: 'var(--text-0)', outline: 'none' }}>{tr(openCount ? 'wizard.review.headingOpen' : 'wizard.review.headingReady')}</Typography>
        <Typography role="status" sx={{ fontSize: 14, color: 'var(--text-2)', mt: 0.5 }}>{tr('wizard.reviewCounts', { answered: completeness?.answered ?? 0, unknown: completeness?.unknown ?? 0, missing: completeness?.missing ?? 0, invalid: completeness?.invalid ?? 0 })}</Typography>
      </Box>
      {canSubmitSpec(canEditSpec) && <Tooltip title={<>{tr('wizard.submitTip')}<br />{tr('wizard.reviewHint')}</>}><span style={{ alignSelf: 'flex-start' }}><Button variant="contained" disabled={submitBusy} onClick={onSubmit} sx={{ minHeight: 52, px: 3.5, fontSize: 16, fontWeight: 600, textTransform: 'none', borderRadius: '12px' }}>{tr(openCount ? 'wizard.review.submitOpen' : 'wizard.review.submit', { count: openCount })}</Button></span></Tooltip>}
    </Stack>
    <DeliveryConflict env={env} />
    {submitErrors.map((x, i) => <Alert key={i} severity="error" action={i === 0 && submitConflict ? <Button color="inherit" size="small" onClick={onReload}>{tr('reloadDraft')}</Button> : undefined}>{x}</Alert>)}
    <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 320px), 1fr))', gap: 2, alignItems: 'start' }}>
      {cards.map(({ step, index, items }) => <Paper key={step.id} component="section" elevation={0} aria-label={tr(step.title)} sx={{ p: '20px', bgcolor: 'var(--panel)', border: '1px solid var(--line)', borderRadius: '14px', boxShadow: 'none', minWidth: 0 }}>
        <Stack direction="row" alignItems="center" sx={{ mb: 1.5 }}>
          <Typography component="h2" sx={{ flex: 1, fontSize: 15, fontWeight: 600, color: 'var(--text-0)' }}>{tr(step.title)}</Typography>
          <Button size="small" onClick={() => env.goToStep(index)} aria-label={`${tr('wizard.review.edit')}: ${tr(step.title)}`} sx={{ minHeight: TOUCH, textTransform: 'none' }}>{tr('wizard.review.edit')}</Button>
        </Stack>
        <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '14px 12px' }}>
          {items.map((item) => <Box key={item.id} sx={{ minWidth: 0, gridColumn: item.wide ? '1 / -1' : undefined }}>
            <Typography sx={{ fontSize: 12, color: 'var(--text-3)', overflowWrap: 'anywhere' }}>{item.label}</Typography>
            <Tooltip title={item.title ? <span style={{ whiteSpace: 'pre-line' }}>{item.title}</span> : ''}><Typography tabIndex={item.title ? 0 : undefined} sx={{ fontSize: item.wide ? 15 : 20, fontWeight: item.wide ? 500 : 700, color: item.unknown ? 'var(--text-3)' : 'var(--text-0)', overflowWrap: 'anywhere', fontVariantNumeric: 'tabular-nums' }}>{item.value}</Typography></Tooltip>
            {item.note && <Typography sx={{ fontSize: 13, color: 'var(--text-2)' }}>{item.note}</Typography>}
          </Box>)}
        </Box>
      </Paper>)}
      {open.length > 0 && <Paper component="section" elevation={0} data-testid="still-open" aria-label={tr('wizard.review.stillOpen')} sx={{ p: '20px', bgcolor: 'var(--panel-2)', border: '1px dashed var(--line)', borderRadius: '14px', boxShadow: 'none', minWidth: 0 }}>
        <Typography component="h2" sx={{ fontSize: 15, fontWeight: 600, color: 'var(--text-0)', mb: 1 }}>{tr('wizard.review.stillOpen')}</Typography>
        <Stack spacing={0.2}>
          {open.map((o) => { const f = fieldOf(o.fieldId); const name = f ? localized(f.short_label ?? f.label, lang) : o.fieldId;
            return <Button key={`${o.stepId}:${o.fieldId}`} onClick={() => onReveal(o.fieldId)} data-field={o.fieldId} sx={{ justifyContent: 'space-between', minHeight: TOUCH, textTransform: 'none', textAlign: 'left', color: 'var(--text-1)', gap: 1 }}>
              <span>{name}</span><Typography component="span" sx={{ fontSize: 12, color: 'var(--text-3)' }}>{tr(`wizard.review.state.${o.state}`)}</Typography>
            </Button>; })}
        </Stack>
      </Paper>}
    </Box>
    {openComments.length > 0 && <Stack direction="row" useFlexGap flexWrap="wrap" gap={0.5}>{openComments.map((message) => <Button key={message.id ?? message.created_at} size="small" sx={{ minHeight: TOUCH, textTransform: 'none', fontSize: 12 }} onClick={() => onOpenComment(message.field_ref!)}>{tr('openComment')}: {message.field_ref}</Button>)}</Stack>}
    {history}
  </Stack>;
}
