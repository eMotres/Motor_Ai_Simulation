import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Box, Button, Chip, CircularProgress, Stack, TextField, Typography } from '@mui/material';
import type { LoadDraft } from '../missionProfileState';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
export interface PropSummary { id: string; vendor: string; model: string; blades?: number | null; diameter_in?: number | null; pitch_in?: number | null; data_quality?: string; power_data?: string; selectable: boolean; rpm_range_tested?: [number, number] | null }
export interface PropSeries { propeller_id: string; rpm: number[]; torque_Nm: number[]; shaft_power_W: number[]; thrust_N: number[]; extrapolated: boolean[]; rpm_range_tested: [number, number] | null; power_estimated: boolean; t_ambient_c: number; rho_kg_m3?: number }
type Conditions = { temperature_C: number; altitude_m: number; pressure_Pa?: number };
const fitSummary = (value: unknown) => {
  if (!value || typeof value !== 'object') return null;
  const fit = value as Record<string, unknown>;
  return Object.fromEntries([['c_ref', fit.c_ref], ['rpm_ref', fit.rpm_ref], ['k', fit.exponent ?? fit.k], ['rpm_min', fit.rpm_min], ['rpm_max', fit.rpm_max], ['rms_rel', fit.rms_rel_residual ?? fit.rms_rel], ['n_used', fit.n_points_used ?? fit.n_used]].filter(([, v]) => typeof v === 'number'));
};
export async function fetchDevelopmentPropellers(): Promise<PropSummary[]> { const r = await fetch(`${API}/api/propellers?selectable_only=false`, { cache: 'no-store' }); if (!r.ok) throw new Error(`HTTP ${r.status}`); const j = await r.json(); return Array.isArray(j.propellers) ? j.propellers : []; }
export async function fetchDevelopmentDetail(id: string, c: Conditions): Promise<Record<string, any>> { const q = new URLSearchParams({ temp_c: String(c.temperature_C), altitude_m: String(c.altitude_m) }); if (c.pressure_Pa != null) q.set('pressure_pa', String(c.pressure_Pa)); const r = await fetch(`${API}/api/propellers/${encodeURIComponent(id)}?${q}`, { cache: 'no-store' }); if (!r.ok) throw new Error(`HTTP ${r.status}`); return await r.json(); }
export async function fetchDevelopmentSeries(id: string, c: Conditions): Promise<PropSeries> { const q = new URLSearchParams({ n: '81', temp_c: String(c.temperature_C), altitude_m: String(c.altitude_m) }); if (c.pressure_Pa != null) q.set('pressure_pa', String(c.pressure_Pa)); const r = await fetch(`${API}/api/propellers/${encodeURIComponent(id)}/series?${q}`, { cache: 'no-store' }); if (!r.ok) throw new Error(`HTTP ${r.status}`); return await r.json() as PropSeries; }

export default function PropellerPicker({ value, canEdit = true, engineerView = false, onChange, t }: { value: LoadDraft; canEdit?: boolean; engineerView?: boolean; onChange: (v: LoadDraft) => void; t: (key: string) => string }) {
  const [items, setItems] = useState<PropSummary[]>([]), [error, setError] = useState(''), [busy, setBusy] = useState(false), [query, setQuery] = useState(''), [pending, setPending] = useState<LoadDraft | null>(null);
  const requestId = useRef(0);
  const valueRef = useRef(value);
  valueRef.current = value;
  const canEditRef = useRef(canEdit);
  canEditRef.current = canEdit;
  useEffect(() => { let live = true; fetchDevelopmentPropellers().then((v) => live && setItems(v)).catch(() => live && setError('unavailable')); return () => { live = false; requestId.current += 1; }; }, []);
  const filtered = useMemo(() => items.filter((p) => `${p.vendor} ${p.model} ${p.id} ${p.diameter_in ?? ''} ${p.pitch_in ?? ''} ${p.data_quality ?? ''} ${p.power_data ?? ''} ${(p.rpm_range_tested ?? []).join(' ')}`.toLowerCase().includes(query.toLowerCase())), [items, query]);
  const conditions: Conditions = { temperature_C: value.temperature_C ?? 25, altitude_m: value.altitude_m ?? 0, pressure_Pa: value.pressure_Pa };
  const inactive = value.load_type !== 'air_propeller' || value.answer_status?.load_type === 'not_known_yet' || value.answer_status?.load_source === 'not_known_yet' || value.answer_status?.load_characteristic === 'not_known_yet';
  useEffect(() => { setPending(null); requestId.current += 1; setBusy(false); }, [conditions.temperature_C, conditions.altitude_m, conditions.pressure_Pa, canEdit, inactive, value.catalog_id, value.load_type, value.source]);
  const choose = useCallback(async (id: string, requestConditions: Conditions) => {
    if (!canEditRef.current || inactive) return;
    const request = ++requestId.current; setBusy(true); setError('');
    const stillCurrent = () => {
      const current = valueRef.current;
      return request === requestId.current && canEditRef.current && current.load_type === 'air_propeller' && current.answer_status?.load_type !== 'not_known_yet' && current.answer_status?.load_source !== 'not_known_yet' && current.answer_status?.load_characteristic !== 'not_known_yet' && (current.temperature_C ?? 25) === requestConditions.temperature_C && (current.altitude_m ?? 0) === requestConditions.altitude_m && (current.pressure_Pa ?? null) === (requestConditions.pressure_Pa ?? null);
    };
    try {
      const prop = items.find((x) => x.id === id); if (!prop?.selectable) throw new Error(t('catalog.unavailable'));
      const [detail, series] = await Promise.all([fetchDevelopmentDetail(id, requestConditions), fetchDevelopmentSeries(id, requestConditions)]);
      if (!stillCurrent()) return;
      const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify({ id, detail })));
      if (!stillCurrent()) return;
      const digest = Array.from(new Uint8Array(bytes)).map((x) => x.toString(16).padStart(2, '0')).join('');
      const fit = detail.fit && typeof detail.fit === 'object' ? detail.fit as Record<string, unknown> : {};
      setPending({ load_type: value.load_type, source: 'catalog', catalog_id: id, vendor: prop.vendor, model: prop.model, data_quality: prop.data_quality, power_data: prop.power_data, answer_status: { load_source: 'filled', load_characteristic: 'filled' }, series: { rpm: series.rpm, torque_Nm: series.torque_Nm, shaft_power_W: series.shaft_power_W, extrapolated: series.extrapolated, quality: series.power_estimated ? 'estimated_power' : 'torque_measured', ambient_C: requestConditions.temperature_C, altitude_m: requestConditions.altitude_m, pressure_Pa: requestConditions.pressure_Pa ?? null, tested_domain_rpm: series.rpm_range_tested, pitch_in: detail.pitch_in, fit: { ct: fitSummary(fit.ct), cp: fitSummary(fit.cp) }, source_urls: Array.isArray(detail.source_urls) ? detail.source_urls.filter((u: unknown): u is string => typeof u === 'string') : [], content_digest: digest } });
    } catch { if (stillCurrent()) setError(t('catalog.unavailable')); }
    finally { if (request === requestId.current) setBusy(false); }
  }, [inactive, items, t]);
  useEffect(() => () => { requestId.current += 1; }, []);
  return <Stack spacing={1}>
    <TextField size="small" label={t('catalog.search')} value={query} onChange={(e) => setQuery(e.target.value)} />
    {error && <Alert severity="warning">{t('catalog.unavailable')}</Alert>}
    <Box sx={{ maxHeight: 220, overflow: 'auto', border: '1px solid var(--line)', borderRadius: 1 }} aria-label={t('catalog.results')}>
    {filtered.map((p) => <Button key={p.id} title={`${p.rpm_range_tested?.join('-') ?? t('catalog.unknownDomain')} rpm`} aria-pressed={value.catalog_id === p.id} disabled={!canEdit || inactive || !p.selectable || busy} onClick={() => void choose(p.id, conditions)} fullWidth sx={{ justifyContent: 'space-between', borderBottom: '1px solid var(--line-soft)', textTransform: 'none' }}><span>{p.vendor} · {p.model} {p.diameter_in ? `· ${p.diameter_in} in` : ''} {p.pitch_in ? `· ${p.pitch_in} ${t('catalog.pitch')}` : ''}</span><Chip size="small" label={!p.selectable ? t('catalog.reference') : t(`catalog.quality.${p.power_data ?? p.data_quality ?? 'unknown'}`)} /></Button>)}
      {!filtered.length && !error && <Typography sx={{ p: 1.5, color: 'var(--text-3)' }}>{t('catalog.empty')}</Typography>}
    </Box>
    {busy && <CircularProgress size={20} aria-label={t('catalog.loading')} />}
    {pending?.series && !inactive && pending.series.ambient_C === conditions.temperature_C && pending.series.altitude_m === conditions.altitude_m && (pending.series.pressure_Pa ?? null) === (conditions.pressure_Pa ?? null) && <Alert severity="info" action={<Stack direction="row"><Button size="small" disabled={!canEdit || inactive} onClick={() => { if (!canEditRef.current || inactive) return; const current = valueRef.current; onChange({ ...current, source: 'catalog', catalog_id: pending.catalog_id, vendor: pending.vendor, model: pending.model, data_quality: pending.data_quality, power_data: pending.power_data, answer_status: { ...current.answer_status, load_source: 'filled', load_characteristic: 'filled' }, series: pending.series }); setPending(null); }}>{t('catalog.applyPreview')}</Button><Button size="small" onClick={() => setPending(null)}>{t('mission.cancel')}</Button></Stack>}>{t('catalog.previewReady')}: {pending.vendor} · {pending.model} · {pending.series.tested_domain_rpm?.join('–') ?? t('catalog.unknownDomain')} rpm · {t(`catalog.quality.${pending.series.quality}`)}</Alert>}
    {value.series && <Alert severity={value.series.ambient_C !== conditions.temperature_C || value.series.altitude_m !== conditions.altitude_m || (value.series.pressure_Pa ?? null) !== (conditions.pressure_Pa ?? null) ? 'warning' : 'info'} action={value.catalog_id && (value.series.ambient_C !== conditions.temperature_C || value.series.altitude_m !== conditions.altitude_m || (value.series.pressure_Pa ?? null) !== (conditions.pressure_Pa ?? null)) ? <Button size="small" disabled={!canEdit || busy || inactive} onClick={() => void choose(value.catalog_id!, conditions)}>{t('catalog.refreshConditions')}</Button> : undefined}>{t('catalog.domain')}: {value.series.tested_domain_rpm?.join('–') ?? t('catalog.unknownDomain')} rpm · {t('catalog.conditions')}: {value.series.ambient_C} °C, {value.series.altitude_m} m, {value.series.pressure_Pa ?? t('catalog.standardPressure')} Pa · {t(`catalog.quality.${value.series.quality}`)}{value.series.extrapolated.some(Boolean) && ` · ${t('catalog.hasExtrapolation')}`}</Alert>}
    {engineerView && value.series && <Box sx={{ p: 1, border: '1px solid var(--line)', borderRadius: 1 }}><details><summary>{t('catalog.engineerDetails')}</summary><Stack spacing={0.4} sx={{ pt: 0.7 }}>{(['ct', 'cp'] as const).map((name) => { const fit = value.series?.fit && typeof value.series.fit === 'object' ? (value.series.fit as Record<string, unknown>)[name] : null; return fit && typeof fit === 'object' ? <Typography key={name} component="div" sx={{ fontSize: 12 }}>{name.toUpperCase()}: {Object.entries(fit as Record<string, unknown>).map(([k, v]) => `${k}=${v}`).join(' · ')}</Typography> : null; })}{value.series.source_urls?.map((url) => <Typography key={url} component="div" sx={{ fontSize: 12 }}><a href={url} target="_blank" rel="noreferrer">{url}</a></Typography>)}</Stack></details></Box>}
  </Stack>;
}
