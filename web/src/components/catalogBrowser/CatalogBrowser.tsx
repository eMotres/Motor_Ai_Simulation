/**
 * CatalogBrowser — ONE browser for every reference catalogue (stage 1:
 * bearings, lubricants, power devices; owner-approved 2026-09-28).
 *
 * List + search + facet filters, a card view with a provenance badge on every
 * field (D datasheet · M measured · E estimate · ∂ derived, ⚠ = to verify),
 * clickable sources, validation deltas and the machines that use the card,
 * and a 2–3 card compare.  Selection stays with the caller: `rowAction`
 * renders whatever "use this" control the host needs (a radio in the
 * Controller, "A"/"B" in Shaft & bearings).  Editing is admin-only and lives
 * in the host (the backend refuses anybody else).
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent,
  DialogTitle, Link, MenuItem, Paper, TextField, Tooltip, Typography,
} from '@mui/material';
import HelpTip from '../common/HelpTip';
import SectionLabel from '../common/SectionLabel';
import { getCard, getUsedBy, listCards, type UsedBy } from './catalogApi';
import {
  compareRows, facet, filterCards, flattenBody, fmtValue, provBadge, provFor,
  sourceHref, toggleCompare,
  type CardEnvelope, type CardSummary, type CatalogKind,
} from './catalogLogic';
import { useTranslation } from 'react-i18next';
import { nsT } from '../../i18n/nsT';

// UI strings: locales/<lng>/motors.json (docs/I18N.md).
const tx = nsT('motors');

const TH = { fontSize: 11, color: 'var(--text-3)', whiteSpace: 'nowrap' } as const;
const TD = { fontSize: 12, color: 'var(--text-1)', fontFamily: 'monospace', whiteSpace: 'nowrap' } as const;
const SMALL = { minWidth: 120, '& .MuiInputBase-input': { fontSize: 12, py: 0.5 } } as const;

const KIND_LABEL: Record<CatalogKind, string> = {
  bearing: 'Bearings', lubricant: 'Lubricants', device: 'Power devices',
};

/** The kind's key columns: [cols key, header, unit, help]. */
const COLUMNS: Record<CatalogKind, Array<[string, string, string, string]>> = {
  bearing: [
    ['type', 'Type', '', 'deep groove or angular contact'],
    ['d', 'd', 'mm', 'bore'], ['D', 'D', 'mm', 'outside diameter'], ['B', 'B', 'mm', 'width'],
    ['C_kn', 'C', 'kN', 'basic dynamic load rating'],
    ['n_limit_grease_rpm', 'n grease', 'rpm', 'speed limit on grease'],
    ['seals', 'Seals', '', 'none / shields / contact / low_friction'],
  ],
  lubricant: [
    ['type', 'Kind', '', 'grease or oil-air'],
    ['nu40', 'ν40', 'mm²/s', 'base-oil viscosity at 40 °C'],
    ['nu100', 'ν100', 'mm²/s', 'base-oil viscosity at 100 °C'],
  ],
  device: [
    ['package', 'Package', '', 'package common name'],
    ['v_dss_V', 'V_DSS', 'V', 'blocking voltage'],
    ['i_d_100c_A', 'I_D 100 °C', 'A', 'continuous drain current at a 100 °C case'],
    ['r_ds_on_25c_mohm', 'R_DS(on) 25', 'mΩ', 'on-resistance at 25 °C, V_GS 18 V'],
    ['r_ds_on_175c_mohm', 'R_DS(on) 175', 'mΩ', 'on-resistance at 175 °C, V_GS 18 V'],
    ['t_j_max_c', 'T_j max', '°C', 'junction limit'],
  ],
};

const STATUS_COLOR: Record<string, string> = {
  validated: '#4ade80', active: 'var(--text-2)', draft: '#fbbf24', deprecated: '#f87171',
};

const BADGE_TIP: Record<string, string> = {
  D: 'datasheet', M: 'measured', E: 'estimate (ours, not the maker’s)', '∂': 'derived from other fields',
};

export interface CatalogBrowserProps {
  kinds: CatalogKind[];
  title: string;
  help: string;
  /** The id the host has selected (highlighted). */
  selectedId?: string;
  /** Per-row "use this" control, rendered first in the row. */
  rowAction?: (c: CardSummary) => React.ReactNode;
  /** One extra host column (e.g. the Controller's parallel count). */
  extraColumn?: { header: React.ReactNode; cell: (c: CardSummary) => React.ReactNode };
  /** A host-side filter (e.g. "fits board"); null = no restriction. */
  visibleIds?: Set<string> | null;
  /** Controls placed on the header line (host filters, admin buttons). */
  headerExtras?: React.ReactNode;
  /** Bump to re-fetch (after an admin edit). */
  reloadKey?: number;
}

const Badge: React.FC<{ env: CardEnvelope; field: string }> = ({ env, field }) => {
  const p = provFor(env, field);
  const b = provBadge(p);
  const src = env.sources.find((s) => s.id === p.src);
  const tip = [BADGE_TIP[b] ?? p.type, src?.doc, p.note, p.verify ? 'TO VERIFY against the printed catalogue' : '',
               p.default ? '(default: first source)' : ''].filter(Boolean).join(' · ');
  return (
    <Tooltip title={tip} arrow>
      <Box component="span" sx={{ fontSize: 10, px: 0.5, borderRadius: 0.5, cursor: 'help',
        border: '1px solid var(--line-soft)',
        color: p.verify ? '#fbbf24' : b === 'E' ? '#f0abfc' : b === 'M' ? '#4ade80' : 'var(--text-3)' }}>
        {b}{p.verify ? ' ⚠' : ''}
      </Box>
    </Tooltip>
  );
};

const CatalogBrowser: React.FC<CatalogBrowserProps> = ({
  kinds, title, help, selectedId, rowAction, extraColumn, visibleIds,
  headerExtras, reloadKey = 0,
}) => {
  useTranslation('motors'); // re-render on language change; lazy-loads the namespace
  const [kind, setKind] = useState<CatalogKind>(kinds[0]);
  const [cards, setCards] = useState<CardSummary[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState('');
  const [maker, setMaker] = useState('');
  const [status, setStatus] = useState('');
  const [type, setType] = useState('');
  const [cmp, setCmp] = useState<string[]>([]);
  const [open, setOpen] = useState<CardEnvelope | null>(null);
  const [usedBy, setUsedBy] = useState<UsedBy[] | null>(null);
  const [compare, setCompare] = useState<CardEnvelope[] | null>(null);

  useEffect(() => { if (!kinds.includes(kind)) setKind(kinds[0]); }, [kinds, kind]);

  useEffect(() => {
    let live = true;
    setErr(null);
    listCards(kind).then((c) => { if (live) setCards(c); })
      .catch((e) => { if (live) { setCards([]); setErr(String(e)); } });
    setCmp([]); setMaker(''); setStatus(''); setType('');
    return () => { live = false; };
  }, [kind, reloadKey]);

  const ok = useMemo(() => cards.filter((c) => !c.error), [cards]);
  const broken = useMemo(() => cards.filter((c) => c.error), [cards]);
  const shown = useMemo(() => filterCards(ok, { q, manufacturer: maker, status, type })
    .filter((c) => !visibleIds || visibleIds.has(c.id)), [ok, q, maker, status, type, visibleIds]);

  const openCard = useCallback(async (id: string) => {
    setErr(null); setUsedBy(null);
    try {
      setOpen(await getCard(kind, id));
      getUsedBy(kind, id).then(setUsedBy).catch(() => setUsedBy([]));
    } catch (e) { setErr(String(e)); }
  }, [kind]);

  const openCompare = useCallback(async () => {
    setErr(null);
    try { setCompare(await Promise.all(cmp.map((id) => getCard(kind, id)))); }
    catch (e) { setErr(String(e)); }
  }, [cmp, kind]);

  const cols = COLUMNS[kind];
  const nCols = cols.length + 4 + (extraColumn ? 1 : 0) + (rowAction ? 1 : 0);

  return (
    <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, p: 2 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1, flexWrap: 'wrap' }}>
        <SectionLabel sx={{ m: 0 }}>{title}</SectionLabel>
        <HelpTip title={help} />
        {kinds.length > 1 && kinds.map((k) => (
          <Chip key={k} size="small" label={KIND_LABEL[k]} onClick={() => setKind(k)}
            variant={k === kind ? 'filled' : 'outlined'} sx={{ fontSize: 11, height: 22 }} />
        ))}
        <Box sx={{ flex: 1 }} />
        {headerExtras}
      </Box>

      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1, flexWrap: 'wrap' }}>
        <TextField size="small" placeholder={tx('search')} value={q} onChange={(e) => setQ(e.target.value)}
          sx={{ ...SMALL, minWidth: 180 }} />
        <HelpTip title={tx('everyWordMustMatchIdPart')} />
        <TextField select size="small" label={tx('maker')} value={maker} onChange={(e) => setMaker(e.target.value)} sx={SMALL}>
          <MenuItem value="" sx={{ fontSize: 12 }}>any</MenuItem>
          {facet(ok, 'manufacturer').map((m) => <MenuItem key={m} value={m} sx={{ fontSize: 12 }}>{m}</MenuItem>)}
        </TextField>
        <TextField select size="small" label={tx('status')} value={status} onChange={(e) => setStatus(e.target.value)} sx={SMALL}>
          <MenuItem value="" sx={{ fontSize: 12 }}>any</MenuItem>
          {facet(ok, 'status').map((m) => <MenuItem key={m} value={m} sx={{ fontSize: 12 }}>{m}</MenuItem>)}
        </TextField>
        <HelpTip title="validated = checked against a measurement; active = in use, datasheet-based; draft = not reviewed; deprecated = kept for old machines." />
        <TextField select size="small" label={tx('type')} value={type} onChange={(e) => setType(e.target.value)} sx={SMALL}>
          <MenuItem value="" sx={{ fontSize: 12 }}>any</MenuItem>
          {facet(ok, 'type').map((m) => <MenuItem key={m} value={m} sx={{ fontSize: 12 }}>{m}</MenuItem>)}
        </TextField>
        <Button size="small" variant="outlined" disabled={cmp.length < 2} onClick={() => void openCompare()}
          sx={{ textTransform: 'none', fontSize: 11 }}>Compare {cmp.length ? `(${cmp.length})` : ''}</Button>
        <HelpTip title={tx('tick23RowsThenCompare')} />
      </Box>

      <Box sx={{ overflowX: 'auto' }}>
        <Box sx={{ display: 'grid', gridTemplateColumns: `repeat(${nCols}, auto)`, rowGap: 0.5,
          columnGap: 1.5, alignItems: 'center', minWidth: 720 }}>
          {rowAction && <Typography sx={TH} />}
          <Typography sx={TH} />
          <Typography sx={TH}>{tx('part')}</Typography>
          <Typography sx={TH}>{tx('maker')}</Typography>
          {cols.map(([k, h, u, tip]) => (
            <Tooltip key={k} title={tip}><Typography sx={{ ...TH, cursor: 'help' }}>{h}{u ? ` ${u}` : ''}</Typography></Tooltip>
          ))}
          <Typography sx={TH}>{tx('status')}</Typography>
          {extraColumn && <Box sx={TH}>{extraColumn.header}</Box>}
          {shown.map((c) => (
            <React.Fragment key={c.id}>
              {rowAction && <Box>{rowAction(c)}</Box>}
              <Checkbox size="small" checked={cmp.includes(c.id)} sx={{ p: 0.25 }}
                onChange={() => setCmp((s) => toggleCompare(s, c.id))}
                inputProps={{ 'aria-label': `compare ${c.id}` }} />
              <Link component="button" onClick={() => void openCard(c.id)}
                sx={{ ...TD, textAlign: 'left', color: 'var(--text-0)',
                  fontWeight: selectedId === c.id ? 700 : 400 }}>{c.id}</Link>
              <Typography sx={{ ...TD, fontFamily: 'inherit' }}>{c.manufacturer ?? '—'}</Typography>
              {cols.map(([k]) => (
                <Typography key={k} sx={TD}>{fmtValue(c.cols?.[k])}</Typography>
              ))}
              <Tooltip title={`${c.flags?.n_verify ?? 0} value(s) to verify, ${c.flags?.n_estimate ?? 0} estimate(s)`
                + (c.has_validation ? ', has validation data' : '')}>
                <Typography sx={{ ...TD, cursor: 'help', color: STATUS_COLOR[c.status ?? ''] ?? 'var(--text-2)' }}>
                  {c.status}{c.flags?.n_verify ? ' ⚠' : ''}
                </Typography>
              </Tooltip>
              {extraColumn && <Box>{extraColumn.cell(c)}</Box>}
            </React.Fragment>
          ))}
        </Box>
      </Box>
      {shown.length === 0 && !err && (
        <Typography sx={{ fontSize: 12, color: 'var(--text-3)', mt: 1 }}>{tx('noCardMatches')}</Typography>)}
      {broken.map((b) => (
        <Typography key={b.id} sx={{ fontSize: 11.5, color: '#fca5a5', mt: 1 }}>{b.id}: {b.error}</Typography>))}
      {err && <Typography sx={{ fontSize: 12, color: '#fca5a5', mt: 1 }}>{err}</Typography>}

      {/* ── one card ── */}
      <Dialog open={!!open} onClose={() => setOpen(null)} maxWidth="md" fullWidth>
        <DialogTitle sx={{ fontSize: 15 }}>
          {open?.part_number ?? open?.id}
          <Typography component="span" sx={{ fontSize: 12, color: 'var(--text-3)', ml: 1 }}>
            {open?.manufacturer} · {open?.status}
            {open?.revision?.n ? ` · rev ${open.revision.n} (${open.revision.date ?? ''})` : ''}
          </Typography>
        </DialogTitle>
        <DialogContent dividers>
          {open?.description && (
            <Typography sx={{ fontSize: 12, color: 'var(--text-2)', mb: 1 }}>{open.description}</Typography>)}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
            <SectionLabel sx={{ m: 0 }}>{tx('sources')}</SectionLabel>
            <HelpTip title={tx('whereTheNumbersComeFromA')} />
          </Box>
          {(open?.sources ?? []).map((s) => {
            const href = sourceHref(s);
            return (
              <Typography key={s.id} sx={{ fontSize: 12, color: 'var(--text-1)' }}>
                <b>{s.id}</b>{' · '}
                {href ? <Link href={href} target="_blank" rel="noopener noreferrer">{s.doc || href}</Link> : (s.doc ?? '')}
                {s.file ? ` · ${s.file}` : ''}{s.page ? ` · ${s.page}` : ''}{s.rev ? ` · rev ${s.rev}` : ''}
                {s.read ? ` · read ${s.read}` : ''}
              </Typography>
            );
          })}
          {!!open?.validation?.length && (
            <>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mt: 1.5 }}>
                <SectionLabel sx={{ m: 0 }}>{tx('validation')}</SectionLabel>
                <HelpTip title={tx('theCardCheckedAgainstAMeasurement')} />
              </Box>
              {open.validation.map((v, i) => (
                <Typography key={i} sx={{ fontSize: 12, color: 'var(--text-1)' }}>
                  {String(v.what ?? '')}
                  {v.ref != null ? ` · ref ${fmtValue(v.ref)}${v.ref_tol != null ? ` ± ${fmtValue(v.ref_tol)}` : ''}` : ''}
                  {v.model != null ? ` · model ${fmtValue(v.model)}` : ''}{v.unit ? ` ${String(v.unit)}` : ''}
                  {v.delta_pct != null ? ` · Δ ${fmtValue(v.delta_pct)} %` : ''}
                  {v.line ? ` · ${String(v.line)}` : ''}{v.doc ? ` · ${String(v.doc)}` : ''}
                </Typography>
              ))}
            </>
          )}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mt: 1.5 }}>
            <SectionLabel sx={{ m: 0 }}>{tx('usedBy')}</SectionLabel>
            <HelpTip title={tx('machineConfigurationsDieConfigThatName')} />
          </Box>
          <Typography sx={{ fontSize: 12, color: 'var(--text-1)' }}>
            {usedBy == null ? '…' : usedBy.length === 0 ? 'no machine'
              : usedBy.map((m) => `${m.die} / ${m.config}${m.count > 1 ? ` ×${m.count}` : ''}`).join(' · ')}
          </Typography>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mt: 1.5 }}>
            <SectionLabel sx={{ m: 0 }}>{tx('fields')}</SectionLabel>
            <HelpTip title={tx('dDatasheetMMeasuredEEstimate')} />
          </Box>
          <Box sx={{ display: 'grid', gridTemplateColumns: 'auto auto 1fr', columnGap: 1.5, rowGap: 0.25 }}>
            {open && flattenBody(open.body).filter(([k]) => !['description'].includes(k)).map(([k, v]) => (
              <React.Fragment key={k}>
                <Typography sx={{ ...TD, color: 'var(--text-3)' }}>{k}</Typography>
                <Badge env={open} field={k} />
                <Typography sx={{ ...TD, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                  {fmtValue(v)}{open.units?.[k] ? ` ${open.units[k]}` : ''}
                </Typography>
              </React.Fragment>
            ))}
          </Box>
          {open?.file && (
            <Typography sx={{ fontSize: 11, color: 'var(--text-3)', mt: 1.5 }}>file: {open.file}</Typography>)}
        </DialogContent>
        <DialogActions><Button onClick={() => setOpen(null)}>{tx('close')}</Button></DialogActions>
      </Dialog>

      {/* ── compare ── */}
      <Dialog open={!!compare} onClose={() => setCompare(null)} maxWidth="lg" fullWidth>
        <DialogTitle sx={{ fontSize: 15 }}>{tx('compare')}</DialogTitle>
        <DialogContent dividers>
          {compare && (
            <Box sx={{ display: 'grid', gridTemplateColumns: `auto repeat(${compare.length}, 1fr)`,
              columnGap: 1.5, rowGap: 0.25 }}>
              <Typography sx={TH} />
              {compare.map((e) => <Typography key={e.id} sx={{ ...TD, fontWeight: 700 }}>{e.id}</Typography>)}
              {compareRows(compare).map((r) => (
                <React.Fragment key={r.field}>
                  <Typography sx={{ ...TD, color: 'var(--text-3)' }}>{r.field}</Typography>
                  {r.values.map((v, i) => (
                    <Box key={i} sx={{ display: 'flex', gap: 0.5, alignItems: 'center',
                      bgcolor: r.differs ? 'rgba(251,191,36,0.10)' : 'transparent' }}>
                      <Typography sx={TD}>{v}</Typography>
                      {v !== '—' && <Badge env={compare[i]} field={r.field} />}
                    </Box>
                  ))}
                </React.Fragment>
              ))}
            </Box>
          )}
        </DialogContent>
        <DialogActions><Button onClick={() => setCompare(null)}>{tx('close')}</Button></DialogActions>
      </Dialog>
    </Paper>
  );
};

export default CatalogBrowser;
