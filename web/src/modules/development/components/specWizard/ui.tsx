/** Small presentational pieces of the specification wizard: the card, the optional-details group, a large number input with
 *  a unit and the duration chips.  Touch targets are at least 44 px; colours come from the app's CSS variables. */
import { useEffect, useState } from 'react';
import { Box, ButtonBase, Collapse, InputAdornment, Paper, Stack, TextField, Typography } from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import HelpTip from '../../../../components/common/HelpTip';
import { DURATION_PRESETS, durationMode } from './operatingPoints';
import { CONTINUOUS, type KeyPointDuration } from '../../keyPoints';

export const TOUCH = 44;

/** One card of a step: an optional title (with a tooltip and a trailing action) above its content. */
export function StepCard({ id, title, tip, action, accent, children, cardColumn }: { id?: string; title?: string; tip?: string; action?: React.ReactNode; accent?: string; children: React.ReactNode; cardColumn?: string }) {
  return <Paper component="section" elevation={0} id={id} aria-label={title} sx={{ '--spec-col': cardColumn, p: { xs: '14px', md: '20px' }, minWidth: 0, scrollMarginTop: 12, bgcolor: 'var(--panel)', border: '1px solid var(--line)', borderRadius: '14px', boxShadow: 'none' }}>
    {(title || action) && <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1.5, minHeight: 28 }}>
      {accent && <Box aria-hidden component="span" sx={{ width: 10, height: 10, borderRadius: '50%', bgcolor: accent, flex: '0 0 10px' }} />}
      <Typography component="h2" sx={{ flex: 1, fontSize: 15, fontWeight: 600, color: 'var(--text-0)', minWidth: 0 }}>{title}</Typography>
      {tip && <HelpTip title={tip} />}
      {action}
    </Stack>}
    <Stack spacing={2} sx={{ minWidth: 0 }}>{children}</Stack>
  </Paper>;
}

/** The collapsed "Optional details" group of a step. */
export function OptionalGroup({ label, count, defaultOpen = false, forceOpen = false, children }: { label: string; count: number; defaultOpen?: boolean; forceOpen?: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  useEffect(() => { if (forceOpen) setOpen(true); }, [forceOpen]);
  const shown = open || forceOpen;
  return <Paper component="section" elevation={0} sx={{ bgcolor: 'var(--panel)', border: '1px dashed var(--line)', borderRadius: '14px', boxShadow: 'none', minWidth: 0 }}>
    <ButtonBase aria-expanded={shown} onClick={() => setOpen(!shown)} sx={{ width: '100%', minHeight: TOUCH, px: { xs: '14px', md: '20px' }, justifyContent: 'flex-start', gap: 1, textAlign: 'left', borderRadius: '14px', fontSize: 14, fontWeight: 600, color: 'var(--text-1)' }}>
      <span style={{ flex: 1 }}>{label} · {count}</span>
      <ExpandMoreIcon sx={{ transition: 'transform .15s', transform: shown ? 'rotate(180deg)' : 'none', color: 'var(--text-3)' }} />
    </ButtonBase>
    <Collapse in={shown}><Stack spacing={2} sx={{ px: { xs: '14px', md: '20px' }, pb: { xs: '14px', md: '20px' }, pt: 0.5 }}>{children}</Stack></Collapse>
  </Paper>;
}

/** A large labelled number input with a unit: the label above, the value large, the unit as a quiet suffix. */
export function BigNumber({ label, unit, value, onChange, disabled, error, ariaLabel, placeholder, min, step = 'any', size = 'large' }: { label: string; unit: string; value: number | null | undefined; onChange: (next: number | null) => void; disabled?: boolean; error?: boolean; ariaLabel: string; placeholder?: string; min?: number; step?: string; size?: 'large' | 'normal' }) {
  const big = size === 'large';
  return <Box component="label" sx={{ display: 'flex', flexDirection: 'column', gap: 0.6, minWidth: 0, fontSize: 12.5, color: 'var(--text-2)' }}>
    <span>{label}</span>
    <TextField type="number" size="small" fullWidth value={value ?? ''} error={error} disabled={disabled} placeholder={placeholder}
      onChange={(e) => onChange(e.target.value.trim() === '' || !Number.isFinite(Number(e.target.value)) ? null : Number(e.target.value))}
      slotProps={{
        htmlInput: { 'aria-label': ariaLabel, min, step, inputMode: 'decimal' },
        input: { endAdornment: unit ? <InputAdornment position="end" sx={{ '& .MuiTypography-root': { fontSize: 12.5, color: 'var(--text-3)' } }}>{unit}</InputAdornment> : undefined },
      }}
      sx={{ '& .MuiInputBase-root': { minHeight: big ? 52 : TOUCH, borderRadius: '10px' }, '& .MuiInputBase-input': { fontSize: big ? 20 : 16, fontWeight: 600, fontVariantNumeric: 'tabular-nums', pr: '4px' } }} />
  </Box>;
}

const chipSx = (selected: boolean) => ({
  minHeight: TOUCH, px: 2, borderRadius: '22px', fontSize: 14, fontWeight: selected ? 600 : 400, whiteSpace: 'nowrap',
  border: '1px solid', borderColor: selected ? 'primary.main' : 'var(--line)', color: selected ? 'primary.main' : 'var(--text-1)',
  bgcolor: selected ? 'action.selected' : 'var(--panel)', '&:focus-visible': { outline: '2px solid', outlineColor: 'primary.main', outlineOffset: 1 },
}) as const;

/** Duration of a point: Continuous, the presets (10 / 30 / 60 s) and "Other" with its own number. */
export function DurationChips({ duration, onChange, disabled, error, label, t, ariaPrefix }: { duration: KeyPointDuration; onChange: (next: KeyPointDuration) => void; disabled?: boolean; error?: boolean; label: string; t: (key: string) => string; ariaPrefix: string }) {
  const [otherOpen, setOtherOpen] = useState(false);
  const mode = durationMode(duration, otherOpen);
  const choose = (next: KeyPointDuration) => { setOtherOpen(false); onChange(next); };
  return <Box role="group" aria-label={`${ariaPrefix}: ${label}`} sx={{ display: 'flex', flexDirection: 'column', gap: 0.8 }}>
    <Typography component="span" sx={{ fontSize: 12.5, color: error ? 'error.main' : 'var(--text-2)' }}>{label}</Typography>
    <Stack direction="row" useFlexGap flexWrap="wrap" gap={1}>
      <ButtonBase disabled={disabled} aria-pressed={mode === 'continuous'} onClick={() => choose(CONTINUOUS)} sx={chipSx(mode === 'continuous')}>{t('wizard.op.continuous')}</ButtonBase>
      {DURATION_PRESETS.map((s) => <ButtonBase key={s} disabled={disabled} aria-pressed={mode === 'preset' && duration === s} onClick={() => choose(s)} sx={chipSx(mode === 'preset' && duration === s)}>{s} s</ButtonBase>)}
      <ButtonBase disabled={disabled} aria-pressed={mode === 'other'} onClick={() => { setOtherOpen(true); if (duration === CONTINUOUS || (typeof duration === 'number' && (DURATION_PRESETS as readonly number[]).includes(duration))) onChange(null); }} sx={chipSx(mode === 'other')}>{t('wizard.op.other')}</ButtonBase>
    </Stack>
    {mode === 'other' && <BigNumber label={t('wizard.op.seconds')} unit="s" size="normal" min={0} value={typeof duration === 'number' ? duration : null} disabled={disabled} error={error} ariaLabel={`${ariaPrefix}: ${t('wizard.op.seconds')}`} onChange={(n) => onChange(n)} />}
  </Box>;
}
