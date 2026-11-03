/** Shipping settings of the cost estimate (owner 2026-11-02, admin only like the whole estimate): ship from / to, the
 *  shipping margin, and the dated DHL quotes the shipping suggestion scales per kg.  A lane is saved only when every field
 *  is valid (`laneErrors`), so a half-typed lane never blocks the autosave.  Shown in Settings of both views. */
import { useState } from 'react';
import { Box, Button, IconButton, MenuItem, Select, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Tooltip, Typography } from '@mui/material';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import EditOutlinedIcon from '@mui/icons-material/EditOutlined';
import HelpTip from '../../../../components/common/HelpTip';
import {
  blankLane, laneErrors, removeLane, setSetting, setShipTo, txt, upsertLane,
  type CostEstimate, type CostLang, type CostSchema, type DhlLane, type T,
} from '../../costEstimate';

const LANE_COLS: Array<keyof DhlLane> = ['from', 'to_country', 'quoted_on', 'weight_kg', 'price', 'currency', 'transit_days', 'mode', 'source', 'note'];

export default function CostShipping({ estimate, schema, lang, t, today, onChange }: {
  estimate: CostEstimate; schema: CostSchema; lang: CostLang; t: T; today: string; onChange: (next: CostEstimate) => void;
}) {
  const ship = schema.shipping;
  const [draft, setDraft] = useState<DhlLane | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  // what is typed in the country box while it has focus (a half-typed code is not saved); otherwise the stored destination
  const [countryDraft, setCountryDraft] = useState<string | null>(null);
  const country = countryDraft ?? estimate.settings.ship_to?.country ?? '';
  if (!ship) return null;
  const field = (id: string) => ship.settings.find((f) => f.id === id);
  const label = (id: string) => txt(field(id)?.label, lang);
  const head = (id: string) => <Stack direction="row" spacing={0.5} alignItems="center"><Typography sx={{ fontSize: 13, color: 'var(--text-1)' }}>{label(id)}</Typography><HelpTip title={txt(field(id)?.help, lang)} /></Stack>;
  const lanes = estimate.dhl_lanes ?? [];
  const errors = draft ? laneErrors(draft, schema.currencies) : [];
  const shipTo = estimate.settings.ship_to;
  const countryOk = /^[A-Za-z]{2}$/.test(country.trim());
  const set = (patch: Partial<DhlLane>) => setDraft((d) => (d ? { ...d, ...patch } : d));
  const saveDraft = () => { if (!draft || errors.length) return; onChange(upsertLane(estimate, draft, editing, schema.currencies)); setDraft(null); setEditing(null); };
  const laneInput = (k: keyof DhlLane) => {
    const v = draft?.[k];
    if (k === 'currency') return <Select size="small" value={String(v ?? '')} onChange={(e) => set({ currency: String(e.target.value) })} inputProps={{ 'aria-label': t(`costEstimate.dhl.col.${k}`) }} sx={{ minWidth: 76, fontSize: 13 }}>
      {schema.currencies.map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}</Select>;
    const numeric = k === 'weight_kg' || k === 'price' || k === 'transit_days';
    return <TextField size="small" value={v === null || v === undefined || (numeric && v === 0) ? '' : String(v)} error={errors.includes(k)}
      placeholder={k === 'quoted_on' ? 'YYYY-MM-DD' : k === 'to_country' ? 'AR' : ''}
      onChange={(e) => { const s = e.target.value; set({ [k]: numeric ? (s.trim() === '' ? (k === 'transit_days' ? null : 0) : Number(s.replace(',', '.'))) : k === 'to_country' ? s.toUpperCase().slice(0, 2) : s } as Partial<DhlLane>); }}
      onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); saveDraft(); } }}
      slotProps={{ htmlInput: { 'aria-label': t(`costEstimate.dhl.col.${k}`), inputMode: numeric ? 'decimal' : undefined } }}
      sx={{ width: k === 'mode' || k === 'source' || k === 'note' ? 150 : k === 'from' ? 120 : k === 'quoted_on' ? 112 : 82, '& .MuiInputBase-input': { fontSize: 13, py: 0.75 } }} />;
  };

  return <Stack spacing={1.25} data-testid="shipping-settings" sx={{ borderTop: '1px solid var(--line-soft)', pt: 1.5 }}>
    <Stack direction="row" spacing={0.5} alignItems="center"><Typography sx={{ fontSize: 14, fontWeight: 600, color: 'var(--text-0)' }}>{t('costEstimate.dhl.title')}</Typography><HelpTip title={txt(ship.excluded, lang)} /></Stack>
    <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', md: '1fr 1fr 1fr 1fr' } }}>
      <Stack spacing={0.6}>{head('ship_from')}<TextField size="small" value={estimate.settings.ship_from ?? ''} onChange={(e) => onChange(setSetting(estimate, schema, 'ship_from', e.target.value.slice(0, 80)))}
        slotProps={{ htmlInput: { 'aria-label': label('ship_from') } }} /></Stack>
      <Stack spacing={0.6}>{head('ship_to')}
        <Stack direction="row" spacing={1}>
          <TextField size="small" value={country} placeholder="AR" error={country.trim() !== '' && !countryOk} sx={{ width: 76 }} onBlur={() => setCountryDraft(null)}
            onChange={(e) => { const c = e.target.value.toUpperCase().slice(0, 2); setCountryDraft(c); if (c === '' || /^[A-Z]{2}$/.test(c)) onChange(setShipTo(estimate, c, shipTo?.city ?? '')); }}
            slotProps={{ htmlInput: { 'aria-label': t('costEstimate.dhl.country') } }} />
          <TextField size="small" value={shipTo?.city ?? ''} disabled={!shipTo} placeholder={t('costEstimate.dhl.city')} sx={{ flex: 1 }}
            onChange={(e) => onChange(setShipTo(estimate, shipTo?.country ?? '', e.target.value))} slotProps={{ htmlInput: { 'aria-label': t('costEstimate.dhl.city') } }} />
        </Stack>
        {!shipTo && <Typography sx={{ fontSize: 11.5, color: 'warning.main' }}>{t('costEstimate.status.missing')}</Typography>}
      </Stack>
      <Stack spacing={0.6}>{head('ship_margin_pct')}<TextField size="small" type="number" value={estimate.settings.ship_margin_pct ?? ''}
        onChange={(e) => { const n = Number(e.target.value); if (e.target.value !== '' && Number.isFinite(n)) onChange(setSetting(estimate, schema, 'ship_margin_pct', Math.min(100, Math.max(0, n)))); }}
        slotProps={{ htmlInput: { 'aria-label': label('ship_margin_pct'), min: 0, max: 100 }, input: { endAdornment: <Typography component="span" sx={{ fontSize: 12, color: 'var(--text-3)' }}>%</Typography> } }} /></Stack>
      <Stack spacing={0.6}>{head('ship_round_eur')}<TextField size="small" type="number" value={estimate.settings.ship_round_eur ?? ''}
        onChange={(e) => { const n = Number(e.target.value); if (e.target.value !== '' && Number.isFinite(n)) onChange(setSetting(estimate, schema, 'ship_round_eur', Math.min(10000, Math.max(0, n)))); }}
        slotProps={{ htmlInput: { 'aria-label': label('ship_round_eur'), min: 0, max: 10000 }, input: { endAdornment: <Typography component="span" sx={{ fontSize: 12, color: 'var(--text-3)' }}>€</Typography> } }} /></Stack>
    </Box>
    <Stack direction="row" spacing={0.5} alignItems="center"><Typography sx={{ fontSize: 13, fontWeight: 600, color: 'var(--text-1)' }}>{t('costEstimate.dhl.lanes')}</Typography><HelpTip title={t('costEstimate.dhl.lanesTip')} /></Stack>
    <Box sx={{ overflowX: 'auto', minWidth: 0 }}>
      <Table size="small" sx={{ minWidth: 900, '& td, & th': { px: 0.75, fontSize: 12.5 } }}>
        <TableHead><TableRow>{LANE_COLS.map((k) => <TableCell key={k} sx={{ fontWeight: 600, color: 'var(--text-2)', whiteSpace: 'nowrap' }}>{t(`costEstimate.dhl.col.${k}`)}</TableCell>)}<TableCell /></TableRow></TableHead>
        <TableBody>
          {lanes.map((l, i) => editing === i && draft
            ? <TableRow key={i}>{LANE_COLS.map((k) => <TableCell key={k}>{laneInput(k)}</TableCell>)}<TableCell sx={{ whiteSpace: 'nowrap' }}>
              <Button size="small" variant="contained" disabled={errors.length > 0} onClick={saveDraft} sx={{ textTransform: 'none' }}>{t('costEstimate.dhl.save')}</Button>
              <Button size="small" onClick={() => { setDraft(null); setEditing(null); }} sx={{ textTransform: 'none' }}>{t('cancel')}</Button></TableCell></TableRow>
            : <TableRow key={i}>{LANE_COLS.map((k) => <TableCell key={k} sx={{ whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums', maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis' }}>{String(l[k] ?? '—')}</TableCell>)}
              <TableCell sx={{ whiteSpace: 'nowrap' }}>
                <Tooltip title={t('costEstimate.dhl.edit')}><IconButton size="small" aria-label={`${t('costEstimate.dhl.edit')} ${i + 1}`} onClick={() => { setDraft({ ...l }); setEditing(i); }}><EditOutlinedIcon sx={{ fontSize: 16 }} /></IconButton></Tooltip>
                <Tooltip title={t('costEstimate.dhl.remove')}><IconButton size="small" aria-label={`${t('costEstimate.dhl.remove')} ${i + 1}`} onClick={() => onChange(removeLane(estimate, i))}><DeleteOutlineIcon sx={{ fontSize: 16 }} /></IconButton></Tooltip>
              </TableCell></TableRow>)}
          {draft && editing === null && <TableRow>{LANE_COLS.map((k) => <TableCell key={k}>{laneInput(k)}</TableCell>)}<TableCell sx={{ whiteSpace: 'nowrap' }}>
            <Button size="small" variant="contained" disabled={errors.length > 0} onClick={saveDraft} sx={{ textTransform: 'none' }}>{t('costEstimate.dhl.save')}</Button>
            <Button size="small" onClick={() => setDraft(null)} sx={{ textTransform: 'none' }}>{t('cancel')}</Button></TableCell></TableRow>}
          {lanes.length === 0 && !draft && <TableRow><TableCell colSpan={LANE_COLS.length + 1} sx={{ color: 'var(--text-3)' }}>{t('costEstimate.dhl.none')}</TableCell></TableRow>}
        </TableBody>
      </Table>
    </Box>
    {draft && errors.length > 0 && <Typography sx={{ fontSize: 11.5, color: 'warning.main' }}>{t('costEstimate.dhl.invalid')}</Typography>}
    {!draft && <Box><Button size="small" disabled={lanes.length >= (ship.lane_limit || 50)} onClick={() => { setDraft(blankLane(estimate, today)); setEditing(null); }} sx={{ textTransform: 'none' }}>{t('costEstimate.dhl.add')}</Button></Box>}
  </Stack>;
}
