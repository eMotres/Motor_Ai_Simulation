// Small shared pieces of the Gears page: a labelled-with-tooltip text, a number field, the state chip.
import { useState } from 'react';
import { Chip, TextField, Tooltip } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { toneOf } from '../view';
import { TONE_COLOUR } from './tone';

/** One short label; what it means goes into the tooltip (keys `l.<k>` and `tip.<k>`). */
export function Lbl({ k, suffix }: { k: string; suffix?: string }) {
  const { t } = useTranslation('gears');
  return <Tooltip title={t(`tip.${k}`)} placement="top" arrow enterTouchDelay={0}><span>{t(`l.${k}`)}{suffix ?? ''}</span></Tooltip>;
}

/** Estimated / unsupported / failed / valid: a coloured chip whose tooltip says what that state means. */
export function StateChip({ state, label }: { state: string | null | undefined; label?: string }) {
  const { t } = useTranslation('gears');
  const tone = toneOf(state);
  const key = tone === 'unknown' ? 'unknown' : tone;
  return (
    <Tooltip title={t(`stateTip.${key}`)} placement="top" arrow enterTouchDelay={0}>
      <Chip size="small" variant="outlined" label={label ?? t(`state.${key}`)} data-state={key}
        sx={{ height: 20, fontSize: 11, color: TONE_COLOUR[tone], borderColor: TONE_COLOUR[tone] }} />
    </Tooltip>
  );
}

/** A number field that keeps its own text while typing and commits only values that parse; red with the message when invalid. */
export function NumField({ label, tip, value, onChange, onClear, error, width = 84, step, testId }: {
  label: string; tip?: string; value: number | undefined; onChange: (v: number) => void; error?: string; width?: number; step?: number; testId?: string;
  /** optional field: emptying the text calls this (the value becomes absent) */
  onClear?: () => void;
}) {
  const [typed, setTyped] = useState<string | null>(null);   // the text while the field is being edited, else the value
  const text = typed ?? (value === undefined ? '' : String(value));
  const setText = setTyped;
  const field = (
    <TextField size="small" label={label} value={text} type="number" error={!!error} data-testid={testId}
      onChange={(e) => { setText(e.target.value); const n = Number(e.target.value); if (e.target.value.trim() === '') { onClear?.(); } else if (Number.isFinite(n)) onChange(n); }}
      onBlur={() => setTyped(null)}
      slotProps={{ htmlInput: { step: step ?? 'any', 'aria-invalid': !!error, 'aria-label': label }, inputLabel: { shrink: true } }}
      sx={{ width, '& input': { fontSize: 13, py: 0.75 } }} />
  );
  const title = error ?? tip ?? '';
  return title ? <Tooltip title={title} placement="top" arrow>{field}</Tooltip> : field;
}
