import { Box, Button, Stack, Typography } from '@mui/material';
import { readLoadDraft } from '../../loadDraftAdapter';
import { cardColumnMin } from '../../specGrid';
import type { SpecificationField } from '../../specificationLayout';
import type { StepEnv } from './env';
import OperatingStep from './OperatingStep';
import { STEP_CARDS, type WizardStep, type WizardStepId } from './steps';
import { OptionalGroup, StepCard, TOUCH } from './ui';

/** The body of one wizard step: its cards of fields, then the collapsed optional group. */
export default function StepBody({ env, step }: { env: StepEnv; step: WizardStep }) {
  if (step.id === 'operating') return <OperatingStep env={env} step={step} />;
  const { tr, fields, draft, steps } = env;
  const find = (id: string): SpecificationField | undefined => step.fields.find((f) => f.id === id);
  const loadType = readLoadDraft(draft).load_type;
  const coolingField = steps.flatMap((s) => s.fields).find((f) => f.id === '3_1');

  const renderById = (f: SpecificationField) => {
    switch (f.id) {
      case 'load_type': return <Box key={f.id}>{fields.renderLoadType(coolingField)}</Box>;
      case '2_1': return <Box key={f.id}>{fields.renderBattery(f)}</Box>;
      case '3_1': return <Box key={f.id}>{fields.renderCooling(f, loadType)}</Box>;
      case '3_2': return <Box key={f.id}>{fields.renderAmbient(f)}</Box>;
      case '4_6': return <Box key={f.id}>{fields.renderLoads(f, loadType)}</Box>;
      case 'mission_profile': return <Box key={f.id}>{fields.renderMission(f)}</Box>;
      default: return <Box key={f.id}>{fields.renderField(f)}</Box>;
    }
  };
  const cards = STEP_CARDS[step.id as WizardStepId] ?? [];
  const optional = step.optionalIds.map(find).filter((f): f is SpecificationField => !!f);
  return <Stack spacing={2} sx={{ minWidth: 0 }}>
    {cards.map((card) => {
      const list = card.ids.map(find).filter((f): f is SpecificationField => !!f);
      if (!list.length) return null;
      return <StepCard key={card.id} title={list.length > 1 && !card.untitled ? tr(`wizard.card.${card.id}`) : undefined} cardColumn={cardColumnMin(list)}>
        {step.id === 'cooling' && card.id === 'cooling' && <LoadNote env={env} />}
        {list.map(renderById)}
      </StepCard>;
    })}
    {optional.length > 0 && <OptionalGroup label={tr('wizard.optional')} count={optional.length} defaultOpen={step.coreIds.length === 0} forceOpen={!!env.revealField && step.optionalIds.includes(env.revealField)}>
      {optional.map(renderById)}
    </OptionalGroup>}
  </Stack>;
}

/** Cooling follows the load: one quiet line names the load with a way back to where it is chosen. */
function LoadNote({ env }: { env: StepEnv }) {
  const { tr, draft } = env;
  const load = readLoadDraft(draft).load_type;
  if (!load || load === 'unknown') return <Typography sx={{ fontSize: 13, color: 'var(--text-3)' }}>{tr('wizard.loadNone')}</Typography>;
  return <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap" useFlexGap>
    <Typography sx={{ fontSize: 13, color: 'var(--text-2)' }}>{tr('wizard.loadNote', { load: tr(`load.types.${load}`, { defaultValue: load }) })}</Typography>
    <Button size="small" onClick={() => env.goToStep(0)} sx={{ minHeight: TOUCH, textTransform: 'none' }}>{tr('wizard.change')}</Button>
  </Stack>;
}
