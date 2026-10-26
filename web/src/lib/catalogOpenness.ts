/**
 * Open / closed motors in the Motors list (owner 2026-10-27): every die row looks the
 * same, and the ONLY per-row distinction is whether the motor is OPEN (its data lives
 * in the public repository) or CLOSED (everything else).  The server stamps
 * `source: 'open' | 'private'` on each die of GET /api/family/tree
 * (routes/family._die_source_of); anything but an explicit 'open' reads as closed,
 * so a missing or unknown value never shows a closed motor as open.
 */
export type Openness = 'open' | 'closed';

export function dieOpenness(die: { source?: string | null } | null | undefined): Openness {
  return die?.source === 'open' ? 'open' : 'closed';
}

/** One quiet colour pair: muted grey for open, amber for closed. */
export const OPENNESS_COLOR: Record<Openness, string> = {
  open: 'var(--text-4)',
  closed: '#fbbf24',
};

/** locales/<lng>/motors.json key of the marker's tooltip. */
export const OPENNESS_TIP_KEY: Record<Openness, string> = {
  open: 'openMotorTip',
  closed: 'closedMotorTip',
};

type Translate = (key: string, opts?: Record<string, unknown>) => string;

/**
 * The passport-card line of the editors' configuration tooltip: the card's date, or
 * which duties were computed on an older build.  It used to be a coloured chip in the
 * row (CardBadge / CardSummary); the rows are uniform now and the information waits
 * in the tooltip.  Empty when the configuration has no card.
 */
export function cardStatusTip(
  t: Translate,
  card: { date?: string | null; staleDuties?: readonly string[] },
): string {
  if (!card.date) return '';
  const stale = card.staleDuties ?? [];
  if (stale.length) return t('card.olderBuildTip', { duties: stale.join(', ') });
  return card.date === '-' ? t('card.tipNoDate') : t('card.tip', { date: card.date });
}
