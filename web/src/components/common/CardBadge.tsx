/**
 * CardBadge / CardSummary - "this machine has a FULL passport card" (owner
 * 2026-10-05).  The flag comes from the server (passport_store: a v1 record per
 * die/configuration), never from a name list here.  Same chip in the Motors
 * tab, the admin motor picker and Configure.
 */
import React from 'react';
import { Chip, Tooltip } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { cardSummary } from '../../lib/accessUi';

const GREEN = { bgcolor: 'rgba(52,211,153,0.16)', color: '#34d399' } as const;
const AMBER = { bgcolor: 'rgba(251,191,36,0.16)', color: '#fbbf24' } as const;
const CHIP = { height: 18, fontSize: 11, fontWeight: 700, '& .MuiChip-label': { px: 0.75 } } as const;

/** One configuration: a check mark and "card"; the tooltip carries the date. */
export const CardBadge: React.FC<{ date?: string | null }> = ({ date }) => {
  const { t } = useTranslation('common');
  if (!date) return null;
  return (
    <Tooltip title={date === '-' ? t('card.tipNoDate') : t('card.tip', { date })}>
      <Chip size="small" label={`✓ ${t('card.badge')}`} sx={{ ...CHIP, ...GREEN }} />
    </Tooltip>
  );
};

/** One die: "n/m cards" - green when every configuration has one, amber when partial. */
export const CardSummary: React.FC<{ cards: number; total: number }> = ({ cards, total }) => {
  const { t } = useTranslation('common');
  const s = cardSummary(cards, total);
  if (s.state === 'none') return null;
  return (
    <Tooltip title={t('card.summaryTip', { cards, total })}>
      <Chip size="small" label={`✓ ${t('card.summary', { cards, total })}`}
        sx={{ ...CHIP, ...(s.state === 'all' ? GREEN : AMBER) }} />
    </Tooltip>
  );
};
