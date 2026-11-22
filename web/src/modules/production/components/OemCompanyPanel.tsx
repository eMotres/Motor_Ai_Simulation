/** Company details of the factory for this production (owner 2026-11-22): filled by any OEM member, staff may edit; the customer side
 *  never gets this tab.  Bank fields are visible to this form's readers only and never leave through logs or digests (server rule).
 *  Short labels, one tooltip per field, no caption lines. */
import { useEffect, useState } from 'react';
import { Alert, Box, Button, CircularProgress, MenuItem, Select, Stack, TextField, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import HelpTip from '../../../components/common/HelpTip';
import { productionApi } from '../api';
import { OEM_COMPANY_GROUPS, type OemCompanyDetails } from '../types';

interface Props { die: string; config: string; onChanged: () => void }

export default function OemCompanyPanel({ die, config, onChanged }: Props) {
  const { t } = useTranslation('production');
  const [data, setData] = useState<OemCompanyDetails | null>(null);
  const [form, setForm] = useState<Record<string, string>>({});
  const [sources, setSources] = useState<Array<{ die: string; config: string; company: string }>>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const show = (next: OemCompanyDetails) => {
    setData(next);
    setForm(Object.fromEntries(next.fields.map((f) => {
      const v = next.values[f.id];
      return [f.id, f.kind === 'bool' ? (v === true ? 'yes' : v === false ? 'no' : '') : String(v ?? '')];
    })));
  };
  useEffect(() => {
    let live = true;
    setData(null); setError('');
    productionApi.companyDetails(die, config).then((d) => { if (live) show(d); }).catch((e) => { if (live) setError(e instanceof Error ? e.message : String(e)); });
    productionApi.companyDetailsSources(die, config).then((s) => { if (live) setSources(s); }).catch(() => undefined);
    return () => { live = false; };
  }, [die, config]);

  if (error && !data) return <Alert severity="error">{error}</Alert>;
  if (!data) return <CircularProgress size={18} />;

  const run = async (job: () => Promise<OemCompanyDetails>) => {
    setBusy(true); setError('');
    try { show(await job()); onChanged(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const stored = (id: string, kind: string) => { const v = data.values[id]; return kind === 'bool' ? (v === true ? 'yes' : v === false ? 'no' : '') : String(v ?? ''); };
  const dirty = data.fields.some((f) => (form[f.id] ?? '') !== stored(f.id, f.kind));
  const save = () => run(() => productionApi.saveCompanyDetails(die, config, Object.fromEntries(data.fields.map((f) => {
    const raw = (form[f.id] ?? '').trim();
    return [f.id, f.kind === 'bool' ? (raw === 'yes' ? true : raw === 'no' ? false : null) : raw || null];
  }))));

  return <Stack spacing={2} sx={{ maxWidth: 760 }} data-testid="oem-company-details">
    {error && <Alert severity="error">{error}</Alert>}
    {OEM_COMPANY_GROUPS.map((group) => <Stack key={group} spacing={1}>
      <Typography sx={{ fontSize: 13, fontWeight: 600, color: 'var(--text-2)' }}>{t(`company.group.${group}`)}</Typography>
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' }, gap: 1.5 }}>
        {data.fields.filter((f) => f.group === group).map((f) => f.kind === 'bool'
          ? <TextField key={f.id} select size="small" label={t(`company.field.${f.id}`)} value={form[f.id] ?? ''} onChange={(e) => setForm((v) => ({ ...v, [f.id]: e.target.value }))}
              InputProps={{ endAdornment: <HelpTip title={t(`company.tip.${f.id}`)} /> }} inputProps={{ 'data-testid': `oem-company-${f.id}` }}>
              <MenuItem value="">{t('company.unset')}</MenuItem><MenuItem value="yes">{t('company.yes')}</MenuItem><MenuItem value="no">{t('company.no')}</MenuItem>
            </TextField>
          : <TextField key={f.id} size="small" label={t(`company.field.${f.id}`)} value={form[f.id] ?? ''} multiline={f.kind === 'multiline'} minRows={f.kind === 'multiline' ? 2 : undefined}
              onChange={(e) => setForm((v) => ({ ...v, [f.id]: e.target.value }))}
              InputProps={{ endAdornment: <HelpTip title={t(`company.tip.${f.id}`)} /> }} inputProps={{ 'data-testid': `oem-company-${f.id}` }} />)}
      </Box>
    </Stack>)}
    <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
      <Button variant="contained" disabled={busy || !dirty} onClick={() => void save()}>{t('company.save')}</Button>
      <Button disabled={busy || dirty || !data.ready_to_confirm} onClick={() => void run(() => productionApi.confirmCompanyDetails(die, config))}>{t('company.confirm')}</Button>
      {sources.length > 0 && <Select size="small" displayEmpty value="" disabled={busy} renderValue={() => t('company.copyFrom')}
        onChange={(e) => { const [d, c] = String(e.target.value).split('\u0000'); if (d && c) void run(() => productionApi.copyCompanyDetails(die, config, { die: d, config: c })); }}>
        {sources.map((s) => <MenuItem key={`${s.die}|${s.config}`} value={`${s.die}\u0000${s.config}`}>{s.company || `${s.die} / ${s.config}`}</MenuItem>)}
      </Select>}
      {sources.length > 0 && <HelpTip title={t('company.copyTip')} />}
      <Typography sx={{ fontSize: 13, color: 'var(--text-2)' }} data-testid="oem-company-confirmed">
        {data.confirmed ? t('company.confirmedBy', { by: data.confirmed.by, date: data.confirmed.at.slice(0, 10) }) : t('company.notConfirmed')}
      </Typography>
    </Stack>
  </Stack>;
}
