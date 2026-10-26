/** Add / edit one test (Tests section).  Every input is validated by the server (a 422 names the field); the pasted rows are
 *  parsed here first so a bad line is shown before anything is sent.  One short label per field, the rest in tooltips. */
import React, { useMemo, useState } from 'react';
import {
  Alert, Box, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle, ListItemText, MenuItem, Select, Stack,
  TextField, Tooltip, Typography,
} from '@mui/material';
import { useTranslation } from 'react-i18next';
import HelpTip from '../../../components/common/HelpTip';
import { testsApi } from '../api';
import { columnsFor, parseNumberList, parseRows, rowsToText } from '../tableModel';
import type { TestKind, TestRecord } from '../types';

type Docs = Array<{ id: string; name: string }>;

/** A number, undefined for an empty field, or the text itself when it is not a number - so the server refuses it loudly
 *  ("'abc' is not a number") instead of it being dropped as empty. */
const num = (s: string): number | string | undefined => {
  const t = s.trim().replace(',', '.');
  if (!t) return undefined;
  const v = Number(t);
  return Number.isFinite(v) ? v : s.trim();
};
const str = (v: unknown) => (v === undefined || v === null ? '' : String(v));
const R_PAIRS = ['1-2', '2-3', '1-3'];
const L_PAIRS = ['1-2', '3-2', '1-3'];

/** "1-2 parallel R 12.5 Ohm 1000 0.5" per line -> LCR rows (pair mode quantity value unit freq level). */
function parseLcr(text: string) {
  return text.split('\n').map((l) => l.trim()).filter(Boolean).map((l) => {
    const [pair, mode, quantity, value, unit, freq, level] = l.split(/\s+/);
    return { pair, mode, quantity, value: num(value ?? ''), unit, freq_hz: num(freq ?? ''), level_v: num(level ?? '') };
  });
}
const lcrText = (rows: TestRecord['lcr']) => (rows ?? []).map((r) =>
  [r.pair, r.mode, r.quantity, r.value, r.unit, r.freq_hz ?? '', r.level_v ?? ''].join(' ').trim()).join('\n');

export const TestEditor: React.FC<{
  open: boolean; die: string; config: string; kind: TestKind; record?: TestRecord | null; documents: Docs;
  onClose: () => void; onSaved: () => void;
}> = ({ open, die, config, kind, record, documents, onClose, onSaved }) => {
  const { t } = useTranslation('production');
  const tt = (k: string, o?: Record<string, unknown>) => t(`tests.editor.${k}`, o);
  const r = record ?? null;
  const c = r?.conditions ?? {};
  const [f, setF] = useState<Record<string, string>>(() => ({
    date: r?.date ?? new Date().toISOString().slice(0, 10), title: str(r?.title), operator: str(r?.operator),
    instrument: str(r?.instrument), profile: str(r?.profile), note: str(r?.note),
    ambient: str(c.ambient_c), humidity: str(c.humidity_pct), state: str(c.state), rotor: c.rotor ?? 'in',
    magnetTemp: str(c.magnet_temp_c), leadsLength: str(r?.leads?.length_mm), leadsAwg: str(r?.leads?.awg),
    leadsArea: str(r?.leads?.area_mm2),
    rTemp: str(r?.resistance?.temp_c), rPairs: (r?.resistance?.pairs ?? []).map((p) => p.value_mohm).join(' / '),
    lFreq: str(r?.inductance?.freq_hz), lPairs: (r?.inductance?.pairs ?? []).map((p) => p.value_uh).join(' / '),
    lcr: lcrText(r?.lcr),
    insulation: (r?.insulation ?? []).map((x) => `${x.voltage_v} ${x.more_than ? '>' : ''}${x.resistance_mohm}`).join('\n'),
    hipot: (r?.hipot ?? []).map((x) => [x.voltage_v, x.leakage_ma ?? '', x.time_s ?? ''].join(' ').trim()).join('\n'),
    surge: (r?.surge ?? []).map((x) => `${x.voltage_v} ${x.result}`).join('\n'),
    weight: str(r?.weight?.[0]?.mass_g), weightWhat: str(r?.weight?.[0]?.what),
    rows: rowsToText(r?.rows, kind),
  }));
  const [docs, setDocs] = useState<string[]>(r?.documents ?? []);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) => setF((v) => ({ ...v, [k]: e.target.value }));
  const parsed = useMemo(() => (kind === 'parameters' ? null : parseRows(f.rows, kind)), [f.rows, kind]);

  const body = (): Partial<TestRecord> => {
    const out: Record<string, unknown> = {
      kind, date: f.date, title: f.title, operator: f.operator, instrument: f.instrument, profile: f.profile, note: f.note,
      documents: docs,
      conditions: {
        ambient_c: num(f.ambient), humidity_pct: num(f.humidity), state: f.state || undefined,
        magnet_temp_c: num(f.magnetTemp), ...(kind === 'parameters' ? { rotor: f.rotor } : {}),
      },
    };
    if (f.leadsLength.trim()) out.leads = { count: 3, length_mm: num(f.leadsLength), awg: num(f.leadsAwg), area_mm2: num(f.leadsArea) };
    if (kind === 'parameters') {
      const rv = parseNumberList(f.rPairs);
      if (rv.length) out.resistance = { temp_c: num(f.rTemp), pairs: rv.map((v, i) => ({ pair: R_PAIRS[i] ?? `${i + 1}`, value_mohm: v })) };
      const lv = parseNumberList(f.lPairs);
      if (lv.length) out.inductance = { freq_hz: num(f.lFreq), pairs: lv.map((v, i) => ({ pair: L_PAIRS[i] ?? `${i + 1}`, value_uh: v })) };
      if (f.lcr.trim()) out.lcr = parseLcr(f.lcr);
      if (f.insulation.trim()) out.insulation = f.insulation.split('\n').filter((l) => l.trim()).map((l) => {
        const [v, m] = l.trim().split(/\s+/);
        return { voltage_v: num(v ?? ''), resistance_mohm: num((m ?? '').replace('>', '')), more_than: (m ?? '').startsWith('>') };
      });
      if (f.hipot.trim()) out.hipot = f.hipot.split('\n').filter((l) => l.trim()).map((l) => {
        const [v, ma, s] = l.trim().split(/\s+/);
        return { voltage_v: num(v ?? ''), leakage_ma: num(ma ?? ''), time_s: num(s ?? '') };
      });
      if (f.surge.trim()) out.surge = f.surge.split('\n').filter((l) => l.trim()).map((l) => {
        const [v, res] = l.trim().split(/\s+/);
        return { voltage_v: num(v ?? ''), result: (res ?? 'ok').toLowerCase() };
      });
      if (f.weight.trim()) out.weight = [{ what: f.weightWhat || 'motor', mass_g: num(f.weight) }];
      // dimensions are kept as they were (entered through the API / a later editor)
      if (r?.dimensions?.length) out.dimensions = r.dimensions;
    } else {
      out.rows = parsed?.rows ?? [];
    }
    return out as Partial<TestRecord>;
  };

  const save = async () => {
    if (parsed && parsed.errors.length) { setErr(t('tests.errors.parse', { line: parsed.errors[0].line, text: parsed.errors[0].text })); return; }
    setBusy(true); setErr(null);
    try {
      if (r) await testsApi.edit(die, config, r.id, body());
      else await testsApi.add(die, config, body());
      onSaved();
    } catch (e) {
      setErr(t('tests.errors.save', { reason: e instanceof Error ? e.message : String(e) }));
    } finally { setBusy(false); }
  };

  const tf = (k: string, opts: { tip?: string; w?: number; multiline?: boolean; type?: string } = {}) => (
    <TextField key={k} size="small" label={tt(k)} value={f[k] ?? ''} onChange={set(k)} type={opts.type}
      multiline={opts.multiline} minRows={opts.multiline ? 2 : undefined}
      sx={{ width: opts.w ?? 150, '& .MuiInputBase-input': { fontSize: 12.5 } }} InputLabelProps={{ shrink: true }}
      InputProps={opts.tip ? { endAdornment: <HelpTip title={opts.tip} /> } : undefined} />
  );
  const kindLabel = t(`tests.sub.${kind}`);

  return (
    <Dialog open={open} onClose={() => !busy && onClose()} fullWidth maxWidth="md">
      <DialogTitle sx={{ fontSize: 15 }}>{tt(r ? 'titleEdit' : 'titleAdd', { kind: kindLabel })}</DialogTitle>
      <DialogContent>
        <Stack spacing={1.2} sx={{ pt: 0.5 }}>
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
            {tf('date', { type: 'date', w: 160 })}{tf('title', { w: 220 })}{tf('instrument', { w: 200 })}
            {tf('profile', { w: 140 })}{tf('operator', { w: 160 })}
          </Box>
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
            {tf('ambient', { w: 110 })}{tf('humidity', { w: 110 })}{tf('state', { w: 220, tip: tt('stateTip') })}
            {kind !== 'parameters' && tf('magnetTemp', { w: 120, tip: tt('magnetTempTip') })}
            {kind === 'parameters' && (
              <TextField select size="small" label={tt('rotor')} value={f.rotor} InputLabelProps={{ shrink: true }}
                onChange={(e) => setF((v) => ({ ...v, rotor: e.target.value }))} sx={{ width: 150 }}>
                <MenuItem value="in">{tt('rotorIn')}</MenuItem>
                <MenuItem value="out">{tt('rotorOut')}</MenuItem>
              </TextField>
            )}
          </Box>
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
            {tf('leadsLength', { w: 110 })}{tf('leadsAwg', { w: 100 })}{tf('leadsArea', { w: 100 })}
            <HelpTip title={tt('leadsTip')} />
          </Box>
          {kind === 'parameters' ? (
            <>
              <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                {tf('rPairs', { w: 260, tip: tt('pairsTip') })}{tf('rTemp', { w: 110 })}
                {tf('lPairs', { w: 260, tip: tt('pairsTip') })}{tf('lFreq', { w: 110 })}
              </Box>
              <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                {tf('lcr', { w: 420, multiline: true, tip: tt('lcrTip') })}
                {tf('insulation', { w: 200, multiline: true, tip: tt('insulationTip') })}
              </Box>
              <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                {tf('hipot', { w: 200, multiline: true })}{tf('surge', { w: 200, multiline: true })}
                {tf('weight', { w: 110 })}{tf('weightWhat', { w: 200 })}
              </Box>
            </>
          ) : (
            <Box>
              <TextField size="small" fullWidth multiline minRows={5} value={f.rows} onChange={set('rows')}
                label={tt('rows', { columns: columnsFor(kind).map((col) => t(`tests.cols.${col}`)).join(' / ') })}
                InputLabelProps={{ shrink: true }} sx={{ '& .MuiInputBase-input': { fontSize: 12, fontFamily: 'monospace' } }}
                InputProps={{ endAdornment: <HelpTip title={tt('rowsTip')} /> }} />
              {parsed && (
                <Typography sx={{ fontSize: 12, mt: 0.4, color: parsed.errors.length ? '#f87171' : 'var(--text-2)' }}>
                  {parsed.errors.length
                    ? t('tests.errors.parse', { line: parsed.errors[0].line, text: parsed.errors[0].text })
                    : tt('rowsParsed', { count: parsed.rows.length })}
                </Typography>
              )}
            </Box>
          )}
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'flex-start' }}>
            {documents.length > 0 && (
              <Tooltip title={tt('documents')} placement="top">
                <Select size="small" multiple displayEmpty value={docs} onChange={(e) => setDocs(e.target.value as string[])}
                  renderValue={(v) => (v.length ? `${tt('documents')}: ${v.length}` : tt('documents'))}
                  sx={{ minWidth: 200, fontSize: 12.5 }}>
                  {documents.map((d) => (
                    <MenuItem key={d.id} value={d.id} dense>
                      <Checkbox size="small" checked={docs.includes(d.id)} /><ListItemText primary={d.name} />
                    </MenuItem>
                  ))}
                </Select>
              </Tooltip>
            )}
            {tf('note', { w: 420, multiline: true })}
          </Box>
          {err && <Alert severity="error" sx={{ fontSize: 12.5 }}>{err}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button disabled={busy} onClick={onClose}>{tt('cancel')}</Button>
        <Button disabled={busy} variant="contained" onClick={() => void save()}>{tt('save')}</Button>
      </DialogActions>
    </Dialog>
  );
};

export default TestEditor;
