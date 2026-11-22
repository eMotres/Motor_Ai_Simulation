/** Company details of the project (owner 2026-11-22): the legal, signatory and delivery data of the customer company, filled by
 *  the project owner (staff may edit).  Short labels, one tooltip per field, no caption lines; the server refuses anybody else. */
import { useEffect, useState } from 'react';
import { Alert, Box, Button, CircularProgress, MenuItem, Select, Stack, TextField, Typography } from '@mui/material';
import HelpTip from '../../../components/common/HelpTip';
import { developmentApi } from '../api';
import { COMPANY_GROUPS, type CompanyDetails } from '../company';

interface Props { projectId: string; t: (key: string, args?: Record<string, unknown>) => string }

export default function CompanyDetailsPanel({ projectId, t }: Props) {
  const [data, setData] = useState<CompanyDetails | null>(null);
  const [form, setForm] = useState<Record<string, string>>({});
  const [sources, setSources] = useState<Array<{ project_id: string; title: string; company: string }>>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [prefilled, setPrefilled] = useState(false);

  const load = (next: CompanyDetails) => {
    setData(next);
    const fromPrefill = !Object.keys(next.values).length && !!next.prefill && Object.keys(next.prefill).length > 0;
    setForm(fromPrefill ? { ...next.prefill } : { ...next.values });
    setPrefilled(fromPrefill);
  };
  useEffect(() => {
    let live = true;
    setData(null); setError('');
    developmentApi.companyDetails(projectId).then((d) => { if (live) load(d); }).catch((e) => { if (live) setError(e instanceof Error ? e.message : String(e)); });
    developmentApi.companyDetailsSources(projectId).then((s) => { if (live) setSources(s); }).catch(() => undefined);
    return () => { live = false; };
  }, [projectId]);

  if (error && !data) return <Alert severity="error">{error}</Alert>;
  if (!data) return <CircularProgress size={18} />;

  const run = async (job: () => Promise<CompanyDetails>) => {
    setBusy(true); setError('');
    try { load(await job()); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const save = () => run(() => developmentApi.saveCompanyDetails(projectId, Object.fromEntries(data.fields.map((f) => [f.id, (form[f.id] ?? '').trim() || null]))));
  const dirty = data.fields.some((f) => (form[f.id] ?? '') !== (data.values[f.id] ?? ''));

  return <Stack spacing={2} sx={{ maxWidth: 760 }} data-testid="company-details">
    {error && <Alert severity="error">{error}</Alert>}
    {COMPANY_GROUPS.map((group) => <Stack key={group} spacing={1}>
      <Typography sx={{ fontSize: 13, fontWeight: 600, color: 'var(--text-2)' }}>{t(`company.group.${group}`)}</Typography>
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' }, gap: 1.5 }}>
        {data.fields.filter((f) => f.group === group).map((f) => <TextField key={f.id} size="small" label={t(`company.field.${f.id}`)}
          required={data.required.includes(f.id)} value={form[f.id] ?? ''} multiline={f.kind === 'multiline'} minRows={f.kind === 'multiline' ? 2 : undefined}
          onChange={(e) => setForm((v) => ({ ...v, [f.id]: e.target.value }))}
          InputProps={{ endAdornment: <HelpTip title={t(`company.tip.${f.id}`)} /> }} inputProps={{ 'data-testid': `company-${f.id}` }} />)}
      </Box>
    </Stack>)}
    <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
      <Button variant="contained" disabled={busy || !dirty} onClick={() => void save()}>{t('company.save')}</Button>
      {data.can_confirm && <Button disabled={busy || dirty || !data.ready_to_confirm} onClick={() => void run(() => developmentApi.confirmCompanyDetails(projectId))}>{t('company.confirm')}</Button>}
      {sources.length > 0 && <Select size="small" displayEmpty value="" disabled={busy} renderValue={() => t('company.copyFrom')}
        onChange={(e) => { if (e.target.value) void run(() => developmentApi.copyCompanyDetails(projectId, String(e.target.value))); }}>
        {sources.map((s) => <MenuItem key={s.project_id} value={s.project_id}>{s.company || s.title}</MenuItem>)}
      </Select>}
      {sources.length > 0 && <HelpTip title={t('company.copyTip')} />}
      {prefilled && <HelpTip title={t('company.prefillTip')} />}
      <Typography sx={{ fontSize: 13, color: 'var(--text-2)' }} data-testid="company-confirmed">
        {data.confirmed ? t('company.confirmedBy', { by: data.confirmed.by, date: data.confirmed.at.slice(0, 10) }) : t('company.notConfirmed')}
      </Typography>
    </Stack>
  </Stack>;
}
