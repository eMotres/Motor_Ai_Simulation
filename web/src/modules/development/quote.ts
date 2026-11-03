/** The customer's read-only quote of a project (owner 2026-11-03): what `GET /api/projects/{id}/quote` returns once an admin
 *  has shared a frozen copy of the cost estimate.  Whole EUR per line (the contingency is already folded in on the server),
 *  weeks, delivery place, payment terms; the display switch converts EUR to USD / CNY at the dated ECB rates of the share.
 *  Pure helpers, tested without a browser (quote.test.mjs). */

export type QuoteLineId = 'tooling' | 'manufacturing' | 'testing' | 'shipping';
export const QUOTE_LINES: QuoteLineId[] = ['tooling', 'manufacturing', 'testing', 'shipping'];
export type QuoteBench = 'dyno' | 'propeller' | 'both' | 'none';
export interface QuoteLine { price: number; weeks: number | null }
export interface QuoteIteration { index: number; motors: number; bench: QuoteBench; lines: Record<QuoteLineId, QuoteLine>; price: number; weeks: number | null }
export interface ProjectQuote {
  currency: 'EUR'; iterations: QuoteIteration[]; total_price: number; total_weeks: number | null;
  /** units of each currency per 1 EUR (ECB reference rates of `date`); null = EUR only */
  exchange_rates: { date: string | null; per_eur: Record<string, number> } | null;
  delivery: string | null; payment_terms: string; shared_on: string;
}
/** The admin's view of what is shared (cost estimate answer `shared`). */
export interface SharedQuoteMark { snapshot_n: number; revision: number | null; shared_at: string; shared_by: string }

/** The display currencies the switch offers: EUR always, then USD / CNY when the share carried their rate. */
export function quoteCurrencies(q: Pick<ProjectQuote, 'exchange_rates'> | null | undefined): string[] {
  const rates = q?.exchange_rates?.per_eur ?? {};
  return ['EUR', ...['USD', 'CNY'].filter((c) => Number(rates[c]) > 0)];
}

/** An EUR price in `currency` (whole units); EUR itself when the rate is unknown. */
export function convertPrice(eur: number, currency: string, q: Pick<ProjectQuote, 'exchange_rates'>): { currency: string; value: number } {
  const rate = currency === 'EUR' ? 1 : Number(q.exchange_rates?.per_eur?.[currency]);
  if (!(rate > 0)) return { currency: 'EUR', value: eur };
  return { currency, value: Math.round(eur * rate) };
}

/** "EUR 12 345" - whole units, spaces between thousands (the admin table's style). */
export function quoteMoney(eur: number, currency: string, q: Pick<ProjectQuote, 'exchange_rates'>): string {
  const { currency: cur, value } = convertPrice(eur, currency, q);
  return `${cur} ${Math.round(value).toLocaleString('en-US', { maximumFractionDigits: 0 }).replace(/,/g, ' ')}`;
}

export const quoteWeeks = (w: number | null | undefined): string => (w === null || w === undefined ? '—' : String(Number(Number(w).toFixed(1))));

/** The admin's one line about the share: revision and date ("Shared rev 40 on 2026-11-03"), or null when nothing is shared. */
export function sharedStatus(mark: SharedQuoteMark | null | undefined, t: (key: string, args?: Record<string, unknown>) => string): string | null {
  if (!mark) return null;
  return t('costEstimate.share.status', { rev: mark.revision ?? '?', date: String(mark.shared_at || '').slice(0, 10) });
}

/** True when the admin's current revision is not the one the customer sees (the button then reads "Share again"). */
export const shareIsStale = (mark: SharedQuoteMark | null | undefined, revision: number): boolean => !!mark && mark.revision !== revision;
