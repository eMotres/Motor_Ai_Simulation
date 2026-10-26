import { Box, Button, IconButton, Stack, TextField, Tooltip, Typography, useMediaQuery } from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import { useTheme } from '@mui/material/styles';
import { CONTINUOUS, blankKeyPoint, keyPointErrors, keyPointsSlotState, keyPointsStored, withKeyPoints, type KeyPoint, type KeyPointProp } from '../../keyPoints';
import { fieldRequired } from '../../wizardState';
import { storedOverspeedRpm } from '../../speedLimits';
import type { StepEnv } from './env';
import type { WizardStep } from './steps';
import { OptionalGroup, StepCard, BigNumber, DurationChips, TOUCH } from './ui';
import OperatingChart from './OperatingChart';
import { buildChartModel, chartMaxSpeed, maxSpeedPlacement, chartPoints, formatPower, missionChartPoints, operatingRows, pointRoles, readableRange, retainedS1Range, shaftPowerOf } from './operatingPoints';
import { cardColumnMin } from '../../specGrid';

/** Step 2 - Operating points: one card per named point (torque, speed, duration chips, derived power), the maximum speed,
 *  the live torque-speed chart, the ripple requirement and the mission profile. */
export default function OperatingStep({ env, step }: { env: StepEnv; step: WizardStep }) {
  const { tr, draft, profile, fields, updateDraft, canEditSpec, busy, migrationBusy } = env;
  const theme = useTheme();
  const narrow = useMediaQuery('(max-width:640px)');
  const field = (id: string) => step.fields.find((f) => f.id === id);
  const kp = field('key_points'), maxField = field('1_6');
  const nameOf = (key: string) => tr(`wizard.op.default.${key}`);
  const rows = operatingRows(draft, { legacy: { continuous: tr('keyPoints.s1'), peak: tr('keyPoints.peak') }, profile, nameOf });
  const roles = pointRoles(rows);
  const generator = profile === 'generator';

  const answer = kp ? fields.storedAnswer(kp, false) : {};
  const unknown = answer.status === 'not_known_yet';
  const locked = !canEditSpec || busy || migrationBusy || unknown;
  const stored = keyPointsStored(draft);
  const required = kp ? fieldRequired(kp, draft) : false;
  const retained = retainedS1Range(draft);
  const setRows = (next: KeyPoint[]) => updateDraft((d) => withKeyPoints(d, next));
  const patch = (id: string, change: Partial<KeyPoint>) => setRows(rows.map((row) => row.row_id === id ? { ...row, ...change } : row));

  const model = buildChartModel(chartPoints(rows), chartMaxSpeed(draft), missionChartPoints(draft), narrow ? { width: 380, height: 300 } : undefined);
  const legacyTip = retained ? `${tr('migration.retainedRange')}: ${tr('field.torque')} ${readableRange(retained.torque_Nm)} N·m · ${tr('field.speed')} ${readableRange(retained.speed_rpm)} rpm` : '';
  const dotColor = (row: KeyPoint) => row.duration_s === CONTINUOUS ? theme.palette.primary.main : typeof row.duration_s === 'number' ? theme.palette.warning.main : 'var(--text-4)';

  const placement = maxSpeedPlacement(step.fields.map((f) => f.id));
  const optional = step.optionalIds.map((id) => field(id)).filter((f): f is NonNullable<typeof f> => !!f);
  return <Stack spacing={2} sx={{ minWidth: 0 }}>
    {kp && <Box id={`spec-field-${kp.id}`}>
      {fields.labelRow(kp, false, required, unknown, tr('wizard.op.pointsTitle'), legacyTip ? [legacyTip] : [])}
      <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 340px), 1fr))', gap: 2, alignItems: 'start' }}>
        {rows.map((row) => {
          const bad = (prop: KeyPointProp) => stored && !unknown && keyPointErrors(row).includes(prop);
          const role = roles.get(row.row_id);
          const power = shaftPowerOf(row.torque_Nm, row.speed_rpm);
          const aria = row.name || tr('keyPoints.name');
          return <StepCard key={row.row_id} id={`spec-point-${row.row_id}`}>
            <Stack direction="row" alignItems="center" spacing={1}>
              <Box aria-hidden component="span" sx={{ width: 10, height: 10, borderRadius: '50%', bgcolor: dotColor(row), flex: '0 0 10px' }} />
              <TextField variant="standard" fullWidth value={row.name} error={bad('name')} disabled={locked} placeholder={tr('keyPoints.name')}
                slotProps={{ htmlInput: { 'aria-label': `${tr('keyPoints.name')}: ${aria}`, maxLength: 80 } }}
                sx={{ '& .MuiInputBase-root': { minHeight: TOUCH, fontSize: 15, fontWeight: 600, bgcolor: 'transparent' }, '& .MuiInputBase-input': { px: 0 } }}
                onChange={(e) => patch(row.row_id, { name: e.target.value })} />
              {role && tr(`wizard.op.role.${role}`) !== row.name && <Typography component="span" sx={{ fontSize: 12, color: 'var(--text-3)', whiteSpace: 'nowrap' }}>{tr(`wizard.op.role.${role}`)}</Typography>}
              {canEditSpec && <Tooltip title={tr('keyPoints.remove')}><span><IconButton aria-label={`${tr('keyPoints.remove')}: ${aria}`} disabled={locked} onClick={() => setRows(rows.filter((r) => r.row_id !== row.row_id))} sx={{ width: TOUCH, height: TOUCH }}><DeleteOutlineIcon sx={{ fontSize: 20 }} /></IconButton></span></Tooltip>}
            </Stack>
            <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 1.5 }}>
              <BigNumber label={generator ? tr('wizard.op.torqueGenerator') : tr('keyPoints.torque')} unit="N·m" ariaLabel={`${aria}: ${tr('keyPoints.torque')} (N·m)`} value={row.torque_Nm} error={bad('torque_Nm')} disabled={locked} onChange={(n) => patch(row.row_id, { torque_Nm: n })} />
              <BigNumber label={tr('keyPoints.speed')} unit="rpm" min={0} ariaLabel={`${aria}: ${tr('keyPoints.speed')} (rpm)`} value={row.speed_rpm} error={bad('speed_rpm')} disabled={locked} onChange={(n) => patch(row.row_id, { speed_rpm: n })} />
            </Box>
            <Typography aria-live="polite" sx={{ fontSize: 13.5, color: 'var(--text-2)', minHeight: 20, fontVariantNumeric: 'tabular-nums' }}>{power === null ? '' : tr(generator ? 'wizard.op.derivedGenerator' : 'wizard.op.derived', { power: formatPower(power) })}</Typography>
            <DurationChips duration={row.duration_s} onChange={(next) => patch(row.row_id, { duration_s: next })} disabled={locked} error={bad('duration_s')} label={tr('keyPoints.duration')} t={tr} ariaPrefix={aria} />
            <BigNumber size="normal" label={tr('wizard.op.voltage')} unit="V" min={0} ariaLabel={`${aria}: ${tr('wizard.op.voltage')}`} value={row.voltage_V} disabled={locked} onChange={(n) => patch(row.row_id, { voltage_V: n })} />
          </StepCard>;
        })}
        {maxField && placement === 'card' && renderMaxSpeed()}
      </Box>
      <Stack spacing={0.5} sx={{ mt: 1 }}>
        {canEditSpec && <Button startIcon={<AddIcon />} disabled={locked || rows.length >= 50} onClick={() => setRows([...rows, blankKeyPoint()])} sx={{ alignSelf: 'flex-start', minHeight: TOUCH, textTransform: 'none' }}>{tr('keyPoints.add')}</Button>}
        {env.fieldErrors[kp.id] && <Typography role="alert" sx={{ color: 'error.main', fontSize: 12 }}>{tr('validationError')}</Typography>}
        {required && !['answered', 'unknown'].includes(keyPointsSlotState(draft)) && <Typography role="alert" sx={{ color: 'error.main', fontSize: 12 }}>{tr('requiredMissing')}</Typography>}
        {fields.answerLater(kp, unknown, false)}
      </Stack>
    </Box>}
    {maxField && placement === 'field' && <StepCard cardColumn={cardColumnMin([maxField])}>{fields.renderField(maxField)}</StepCard>}
    <StepCard><OperatingChart model={model} t={tr} generator={generator} /></StepCard>
    {field('1_8') && <StepCard cardColumn={cardColumnMin([field('1_8')!])}>{fields.renderField(field('1_8')!)}</StepCard>}
    {field('mission_profile') && <StepCard>{fields.renderField(field('mission_profile')!)}</StepCard>}
    {optional.length > 0 && <OptionalGroup label={tr('wizard.optional')} count={optional.length} open={env.groupOpen.includes(step.id)} onOpenChange={(o) => env.onGroupOpen(step.id, o)} forceOpen={!!env.revealField && step.optionalIds.includes(env.revealField)}>
      {optional.map((f) => fields.renderField(f))}
    </OptionalGroup>}
  </Stack>;

  /** The maximum speed as the third card of the row: the single highest speed the motor ever sees. */
  function renderMaxSpeed() {
    if (!maxField) return null;
    const a = fields.storedAnswer(maxField, false), isUnknown = a.status === 'not_known_yet', obj = fields.objectOf(a.value);
    const prop = maxField.properties?.find((p) => p.id === 'maximum_rpm');
    const req = fieldRequired(maxField, draft);
    const stale = storedOverspeedRpm(draft);
    return <StepCard id="spec-field-1_6" title={fields.label(maxField.short_label ?? maxField.label)} tip={fields.label(maxField.help ?? '') || undefined} accent="var(--text-3)" action={<>{req && <Box component="span" sx={{ color: 'error.main' }} aria-hidden>*</Box>}{fields.commentIcon('fields.1_6')}</>}>
      {prop && <Box sx={{ '&& .MuiInputBase-root': { minHeight: 52, borderRadius: '10px' }, '&& .MuiInputBase-input': { fontSize: 22, fontWeight: 600 } }}>
        {fields.renderTypedValue(prop, obj[prop.id], (value) => fields.setProperty(maxField, false, obj, prop.id, value), `${maxField.id}-${prop.id}`, isUnknown)}
      </Box>}
      {stale && <Typography sx={{ fontSize: 12.5, color: 'var(--text-3)' }}>{tr('wizard.storedOverspeed', { value: stale })}</Typography>}
      {fields.fieldMessages(maxField, a, req)}
      {fields.answerLater(maxField, isUnknown, false)}
    </StepCard>;
  }
}
