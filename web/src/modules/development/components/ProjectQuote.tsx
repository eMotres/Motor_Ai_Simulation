/** The project's read-only Quote (owner 2026-11-03): the frozen copy of the cost estimate an admin shared with the project's
 *  members.  One table, iterations as rows (motors, what each line costs, weeks, iteration price), then the total, the
 *  delivery place, the payment terms, the shared date and the EUR / USD / CNY switch.  No inputs, no sources, no captions:
 *  one tooltip at most per element.  Numbers come from the server as whole EUR (quote.ts). */
import { useEffect, useState } from 'react';
import { Alert, Box, CircularProgress, MenuItem, Select, Stack, Table, TableBody, TableCell, TableHead, TableRow, Tooltip, Typography } from '@mui/material';
import HelpTip from '../../../components/common/HelpTip';
import { developmentApi, DevelopmentApiError } from '../api';
import { QUOTE_LINES, quoteCurrencies, quoteMoney, quoteWeeks, type ProjectQuote as Quote } from '../quote';

interface Props { projectId: string; t: (key: string, args?: Record<string, unknown>) => string }

export default function ProjectQuote({ projectId, t }: Props) {
  const [quote, setQuote] = useState<Quote | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'none' | 'error'>('loading');
  const [error, setError] = useState('');
  const [currency, setCurrency] = useState('EUR');
  useEffect(() => {
    let live = true;
    setState('loading'); setQuote(null);
    developmentApi.projectQuote(projectId)
      .then((q) => { if (live) { setQuote(q); setState('ready'); } })
      .catch((e) => { if (!live) return; if (e instanceof DevelopmentApiError && e.status === 404) setState('none'); else { setError(e instanceof Error ? e.message : String(e)); setState('error'); } });
    return () => { live = false; };
  }, [projectId]);

  if (state === 'loading') return <CircularProgress size={18} />;
  if (state === 'error') return <Alert severity="error">{error}</Alert>;
  if (state === 'none' || !quote) return <Typography sx={{ fontSize: 13, color: 'var(--text-3)' }}>{t('customerQuote.none')}</Typography>;

  const cur = quoteCurrencies(quote).includes(currency) ? currency : 'EUR';
  const money = (eur: number) => quoteMoney(eur, cur, quote);
  const num = { fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap', textAlign: 'right' } as const;
  const head = (key: string, right = false) => <TableCell key={key} sx={{ fontSize: 12, fontWeight: 600, color: 'var(--text-2)', whiteSpace: 'nowrap', textAlign: right ? 'right' : 'left' }}>{t(`customerQuote.col.${key}`)}</TableCell>;
  const rates = quote.exchange_rates;
  const fact = (label: string, value: string, testId: string) => <Stack spacing={0.25} sx={{ minWidth: 0 }}>
    <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{label}</Typography>
    <Typography data-testid={testId} sx={{ fontSize: 13.5, color: 'var(--text-0)', overflowWrap: 'anywhere' }}>{value || '—'}</Typography>
  </Stack>;

  return <Box data-testid="project-quote" sx={{ bgcolor: 'var(--panel)', border: '1px solid var(--line)', borderRadius: '14px', p: { xs: 1, md: 2 }, minWidth: 0 }}>
    <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
      <Typography component="h2" sx={{ fontSize: 16, fontWeight: 600, color: 'var(--text-0)', flex: 1 }}>{t('tabs.quote')}</Typography>
      <HelpTip title={t('customerQuote.tip')} />
      <Tooltip title={rates ? t('customerQuote.currencyTip', { date: rates.date ?? '' }) : t('customerQuote.noRates')}>
        <Select size="small" value={cur} onChange={(e) => setCurrency(String(e.target.value))} inputProps={{ 'aria-label': t('customerQuote.currency') }}
          sx={{ minWidth: 84, fontSize: 13, '& .MuiSelect-select': { py: 0.5 } }}>
          {quoteCurrencies(quote).map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}
        </Select>
      </Tooltip>
    </Stack>
    <Box sx={{ overflowX: 'auto', minWidth: 0 }}>
      <Table size="small" sx={{ minWidth: 760, '& td, & th': { px: 0.75, borderColor: 'var(--line-soft)' } }}>
        <TableHead><TableRow>
          {head('iteration')}{head('motors', true)}{QUOTE_LINES.map((id) => head(id, true))}{head('weeks', true)}{head('price', true)}
        </TableRow></TableHead>
        <TableBody>
          {quote.iterations.map((it) => <TableRow key={it.index} data-row={it.index}>
            <TableCell sx={{ fontSize: 13, fontWeight: 600, whiteSpace: 'nowrap' }}>{t('customerQuote.iteration', { n: it.index })}</TableCell>
            <TableCell sx={num}>{it.motors}</TableCell>
            {QUOTE_LINES.map((id) => {
              const line = it.lines[id];
              const noBench = id === 'testing' && it.bench === 'none';
              const tip = [id === 'testing' ? t(`customerQuote.bench.${it.bench}`) : '', line.weeks !== null ? t('customerQuote.lineWeeks', { n: quoteWeeks(line.weeks) }) : ''].filter(Boolean).join(' · ');
              const cell = noBench ? '—' : money(line.price);
              return <TableCell key={id} sx={num} data-testid={`quote-${it.index}-${id}`}>{tip ? <Tooltip title={tip}><span>{cell}</span></Tooltip> : cell}</TableCell>;
            })}
            <TableCell sx={num}>{t('customerQuote.weeksN', { n: quoteWeeks(it.weeks) })}</TableCell>
            <TableCell sx={{ ...num, fontWeight: 600 }} data-testid={`quote-${it.index}-price`}>{money(it.price)}</TableCell>
          </TableRow>)}
          <TableRow>
            <TableCell colSpan={2 + QUOTE_LINES.length} sx={{ fontSize: 14, fontWeight: 700 }}>{t('customerQuote.total')}</TableCell>
            <TableCell sx={{ ...num, fontWeight: 700 }}>{t('customerQuote.weeksN', { n: quoteWeeks(quote.total_weeks) })}</TableCell>
            <TableCell sx={{ ...num, fontWeight: 700, fontSize: 15 }} data-testid="quote-total">{money(quote.total_price)}</TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </Box>
    <Box sx={{ display: 'grid', gap: 2, mt: 1.5, gridTemplateColumns: { xs: '1fr', sm: '1fr 2fr 1fr' } }}>
      {fact(t('customerQuote.delivery'), quote.delivery ?? '', 'quote-delivery')}
      {fact(t('customerQuote.paymentTerms'), quote.payment_terms, 'quote-terms')}
      {fact(t('customerQuote.sharedOn'), quote.shared_on, 'quote-shared-on')}
    </Box>
  </Box>;
}
