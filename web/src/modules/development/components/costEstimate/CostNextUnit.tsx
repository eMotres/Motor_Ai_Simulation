/** The price of the NEXT unit (owner 2026-11-22, admin only like the whole estimate): a repeat order of the same motor, no tooling and
 *  no development: one motor's manufacturing (the last iteration's per-motor amount) + testing when ticked + shipping of one motor.
 *  Derived from the lines on screen; the admin can overwrite the total, the duration and the shipping amount. */
import { useEffect, useState } from 'react';
import { Box, FormControlLabel, Stack, Switch, TextField, Tooltip, Typography } from '@mui/material';
import HelpTip from '../../../../components/common/HelpTip';
import { blankNextUnit, formatMoney, parseNumberInput as parse, setNextUnit, setNextUnitShipping, type CostComputed, type CostEstimate, type T } from '../../costEstimate';

/** A number field with an EDIT BUFFER: what is typed stays on screen, and only a valid value is committed (an empty field commits
 *  `null`).  An invalid one (negative, not a number, out of range, not whole) shows red next to the field and is NOT saved, so a
 *  half-typed entry never clears or corrupts the stored value. */
function BufNum({ value, onCommit, ariaLabel, placeholder, min = 0, max, int = false, invalidText, width }: {
  value: number | null | undefined; onCommit: (n: number | null) => void; ariaLabel: string; placeholder?: string; min?: number; max?: number; int?: boolean; invalidText: string; width?: number;
}) {
  const [text, setText] = useState(value === null || value === undefined ? '' : String(value));
  useEffect(() => { setText((cur) => (parse(cur, min, max, int) === (value ?? null) ? cur : value === null || value === undefined ? '' : String(value))); }, [value, min, max, int]);
  const bad = parse(text, min, max, int) === undefined;
  return <Stack spacing={0.25} sx={{ width }}>
    <TextField size="small" value={text} placeholder={placeholder} error={bad}
      onChange={(e) => { setText(e.target.value); const n = parse(e.target.value, min, max, int); if (n !== undefined) onCommit(n); }}
      slotProps={{ htmlInput: { 'aria-label': ariaLabel, 'aria-invalid': bad, inputMode: 'decimal' } }} />
    {bad && <Typography role="alert" sx={{ fontSize: 12, color: 'error.main' }}>{invalidText}</Typography>}
  </Stack>;
}
export default function CostNextUnit({ estimate, computed, t, today, who, onChange, weeksMax = 416 }: {
  estimate: CostEstimate; computed: CostComputed | null; t: T; today: string; who: string; onChange: (next: CostEstimate) => void;
  /** the schema's `durations.override_max`: the next unit's weeks are an override of the whole duration */ weeksMax?: number;
}) {
  const nu = estimate.next_unit ?? blankNextUnit();
  const res = computed?.next_unit;
  const eur = (v: number | null | undefined) => (v === null || v === undefined ? '—' : formatMoney({ EUR: v }));
  const invalid = t('costEstimate.nextUnit.invalid');
  const auto = nu.shipping.status === 'filled' && nu.shipping.source === 'dhl';
  return <Stack spacing={1} data-testid="next-unit" sx={{ borderTop: '1px solid var(--line-soft)', pt: 1.5 }}>
    <Stack direction="row" spacing={0.5} alignItems="center">
      <Typography sx={{ fontSize: 14, fontWeight: 600, color: 'var(--text-0)' }}>{t('costEstimate.nextUnit.title')}</Typography>
      <HelpTip title={t('costEstimate.nextUnit.tip')} />
    </Stack>
    <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', md: '1fr 1fr 1fr 1fr 1fr' } }}>
      <Stack spacing={0.4}>
        <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{t('costEstimate.nextUnit.manufacturing')}</Typography>
        <Typography data-testid="next-unit-manufacturing" sx={{ fontSize: 13.5 }}>{eur(res?.manufacturing)}</Typography>
        <FormControlLabel control={<Switch size="small" checked={nu.include_testing} onChange={(e) => onChange(setNextUnit(estimate, { include_testing: e.target.checked }))} />}
          label={<Typography sx={{ fontSize: 12.5 }}>{t('costEstimate.nextUnit.testing')}{res?.testing != null ? ` · ${eur(res.testing)}` : ''}</Typography>} />
      </Stack>
      <Stack spacing={0.6}>
        <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{t('costEstimate.nextUnit.shipping')}{auto ? ` · ${t('costEstimate.nextUnit.auto')}` : ''}</Typography>
        <BufNum value={nu.shipping.amount} placeholder="EUR" max={1e9} invalidText={invalid} ariaLabel={t('costEstimate.nextUnit.shipping')}
          onCommit={(n) => onChange(setNextUnitShipping(estimate, n, today, who))} />
        {nu.shipping.hint && <Typography sx={{ fontSize: 12, color: 'warning.main' }}>{nu.shipping.hint}</Typography>}
      </Stack>
      <Stack spacing={0.6}>
        <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{t('costEstimate.nextUnit.parcels')}</Typography>
        <BufNum value={nu.parcels} placeholder="1" min={1} max={50} int invalidText={invalid} ariaLabel={t('costEstimate.nextUnit.parcels')}
          onCommit={(n) => onChange(setNextUnit(estimate, { parcels: n }))} />
        <NextUnitBox box={nu.box_cm} label={t('costEstimate.nextUnit.box')} invalidText={invalid} onCommit={(box) => onChange(setNextUnit(estimate, { box_cm: box }))} />
      </Stack>
      <Stack spacing={0.6}>
        <Tooltip title={t('costEstimate.nextUnit.priceTip', { derived: eur(res?.derived) })}><Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{t('costEstimate.nextUnit.price')}</Typography></Tooltip>
        <BufNum value={nu.price} placeholder={res?.derived != null ? String(res.derived) : 'EUR'} max={1e9} invalidText={invalid} ariaLabel={t('costEstimate.nextUnit.price')}
          onCommit={(n) => onChange(setNextUnit(estimate, { price: n }))} />
      </Stack>
      <Stack spacing={0.6}>
        <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{t('costEstimate.nextUnit.weeks')}</Typography>
        <BufNum value={nu.weeks} placeholder={res?.weeks != null ? String(res.weeks) : ''} max={weeksMax} invalidText={invalid} ariaLabel={t('costEstimate.nextUnit.weeks')}
          onCommit={(n) => onChange(setNextUnit(estimate, { weeks: n }))} />
      </Stack>
    </Box>
    <Typography data-testid="next-unit-total" sx={{ fontSize: 13.5, fontWeight: 600 }}>
      {res?.available ? `${t('costEstimate.nextUnit.total')}: ${eur(res.total)}` : (res?.reason || t('costEstimate.nextUnit.unavailable'))}
      {res?.available && res.weeks == null && (res.weeks_missing?.length ?? 0) > 0 ? ` · ${t('costEstimate.nextUnit.weeksMissing', { parts: res.weeks_missing!.join(', ') })}` : ''}
    </Typography>
  </Stack>;
}

/** The three box dimensions: committed only when all three are valid (1..400 cm), or all empty (= from the weight); a partial box
 *  stays in the buffer, red, and is never saved. */
function NextUnitBox({ box, label, invalidText, onCommit }: { box: number[] | null | undefined; label: string; invalidText: string; onCommit: (box: number[] | null) => void }) {
  const fromProp = (b: number[] | null | undefined) => [0, 1, 2].map((i) => (b?.[i] === undefined ? '' : String(b[i])));
  const [text, setText] = useState<string[]>(fromProp(box));
  const parsed = text.map((x) => parse(x, 1, 400, false));
  const allEmpty = parsed.every((v) => v === null);
  const allValid = parsed.every((v) => typeof v === 'number');
  const bad = !allEmpty && !allValid;
  useEffect(() => {       // follow the stored box when it changes elsewhere; a half-typed box stays as typed
    setText((cur) => {
      const p = cur.map((x) => parse(x, 1, 400, false));
      if (p.every((v) => v === null)) return box ? fromProp(box) : cur;
      if (p.every((v) => typeof v === 'number')) return box && p.every((v, i) => v === box[i]) ? cur : fromProp(box);
      return cur;
    });
  }, [box]);
  return <Stack spacing={0.25} data-testid="next-unit-box">
    <Stack direction="row" spacing={0.5}>
      {[0, 1, 2].map((i) => <TextField key={i} size="small" value={text[i]} error={bad}
        onChange={(e) => {
          const nextText = text.map((x, j) => (j === i ? e.target.value : x));
          setText(nextText);
          const p = nextText.map((x) => parse(x, 1, 400, false));
          if (p.every((v) => v === null)) onCommit(null);
          else if (p.every((v) => typeof v === 'number')) onCommit(p as number[]);
        }}
        slotProps={{ htmlInput: { 'aria-label': `${label} ${i + 1}`, 'aria-invalid': bad, inputMode: 'decimal' } }} />)}
    </Stack>
    {bad && <Typography role="alert" sx={{ fontSize: 12, color: 'error.main' }}>{invalidText}</Typography>}
  </Stack>;
}
