/** The price of the NEXT unit (owner 2026-11-22, admin only like the whole estimate): a repeat order of the same motor, no tooling and
 *  no development: one motor's manufacturing (the last iteration's per-motor amount) + testing when ticked + shipping of one motor.
 *  Derived from the lines on screen; the admin can overwrite the total, the duration and the shipping amount. */
import { Box, FormControlLabel, Stack, Switch, TextField, Tooltip, Typography } from '@mui/material';
import HelpTip from '../../../../components/common/HelpTip';
import { blankNextUnit, formatMoney, setNextUnit, setNextUnitShipping, type CostComputed, type CostEstimate, type T } from '../../costEstimate';

export default function CostNextUnit({ estimate, computed, t, today, who, onChange }: {
  estimate: CostEstimate; computed: CostComputed | null; t: T; today: string; who: string; onChange: (next: CostEstimate) => void;
}) {
  const nu = estimate.next_unit ?? blankNextUnit();
  const res = computed?.next_unit;
  const eur = (v: number | null | undefined) => (v === null || v === undefined ? '—' : formatMoney({ EUR: v }));
  const num = (raw: string): number | null => { const n = Number(raw); return raw !== '' && Number.isFinite(n) && n >= 0 ? n : null; };
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
        <TextField size="small" type="number" value={nu.shipping.amount ?? ''} placeholder="EUR"
          onChange={(e) => onChange(setNextUnitShipping(estimate, num(e.target.value), today, who))}
          slotProps={{ htmlInput: { 'aria-label': t('costEstimate.nextUnit.shipping'), min: 0 } }} />
        {nu.shipping.hint && <Typography sx={{ fontSize: 12, color: 'warning.main' }}>{nu.shipping.hint}</Typography>}
      </Stack>
      <Stack spacing={0.6}>
        <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{t('costEstimate.nextUnit.parcels')}</Typography>
        <TextField size="small" type="number" value={nu.parcels ?? ''} placeholder="1"
          onChange={(e) => onChange(setNextUnit(estimate, { parcels: e.target.value === '' ? null : Math.max(1, Math.min(50, Math.round(Number(e.target.value)))) }))}
          slotProps={{ htmlInput: { 'aria-label': t('costEstimate.nextUnit.parcels'), min: 1, max: 50 } }} />
        <Stack direction="row" spacing={0.5} data-testid="next-unit-box">
          {[0, 1, 2].map((i) => <TextField key={i} size="small" type="number" value={nu.box_cm?.[i] ?? ''}
            onChange={(e) => {
              const cur = [...(nu.box_cm ?? [0, 0, 0])]; cur[i] = Number(e.target.value);
              onChange(setNextUnit(estimate, { box_cm: cur.every((v) => v > 0) ? cur : cur.every((v) => !v) ? null : cur }));
            }}
            slotProps={{ htmlInput: { 'aria-label': `${t('costEstimate.nextUnit.box')} ${i + 1}`, min: 1, max: 400 } }} />)}
        </Stack>
      </Stack>
      <Stack spacing={0.6}>
        <Tooltip title={t('costEstimate.nextUnit.priceTip', { derived: eur(res?.derived) })}><Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{t('costEstimate.nextUnit.price')}</Typography></Tooltip>
        <TextField size="small" type="number" value={nu.price ?? ''} placeholder={res?.derived != null ? String(res.derived) : 'EUR'}
          onChange={(e) => onChange(setNextUnit(estimate, { price: num(e.target.value) }))} slotProps={{ htmlInput: { 'aria-label': t('costEstimate.nextUnit.price'), min: 0 } }} />
      </Stack>
      <Stack spacing={0.6}>
        <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{t('costEstimate.nextUnit.weeks')}</Typography>
        <TextField size="small" type="number" value={nu.weeks ?? ''} placeholder={res?.weeks != null ? String(res.weeks) : ''}
          onChange={(e) => onChange(setNextUnit(estimate, { weeks: num(e.target.value) }))} slotProps={{ htmlInput: { 'aria-label': t('costEstimate.nextUnit.weeks'), min: 0, max: 104 } }} />
      </Stack>
    </Box>
    <Typography data-testid="next-unit-total" sx={{ fontSize: 13.5, fontWeight: 600 }}>
      {res?.available ? `${t('costEstimate.nextUnit.total')}: ${eur(res.total)}` : (res?.reason || t('costEstimate.nextUnit.unavailable'))}
      {res?.available && res.weeks == null && (res.weeks_missing?.length ?? 0) > 0 ? ` · ${t('costEstimate.nextUnit.weeksMissing', { parts: res.weeks_missing!.join(', ') })}` : ''}
    </Typography>
  </Stack>;
}
