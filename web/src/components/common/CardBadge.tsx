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
export const CardBadge: React.FC<{ date?: string | null; olderBuild?: boolean; staleDuties?: readonly string[] }> = ({ date, olderBuild = false, staleDuties = [] }) => {
  const { t } = useTranslation('common');
  if (!date) return null;
  const title = olderBuild
    ? t('card.olderBuildTip', { duties: staleDuties.join(', ') || t('card.someDuty') })
    : (date === '-' ? t('card.tipNoDate') : t('card.tip', { date }));
  return (
    <Tooltip title={title}>
      <Chip size="small"
        label={olderBuild ? `${t('card.badge')} \u00b7 ${t('card.olderBuild')}` : `\u2713 ${t('card.badge')}`}
        sx={{ ...CHIP, ...(olderBuild ? AMBER : GREEN) }} />
    </Tooltip>
  );
};
/** One die: completeness and result currency remain separately visible. */
export const CardSummary: React.FC<{
  cards: number;
  total: number;
  olderBuildConfigs?: readonly string[];
}> = ({ cards, total, olderBuildConfigs = [] }) => {
  const { t } = useTranslation('common');
  const s = cardSummary(cards, total);
  const olderBuild = olderBuildConfigs.length > 0;
  if (s.state === 'none') return null;
  const title = olderBuild
    ? t('card.summaryOlderBuildTip', { cards, total, configs: olderBuildConfigs.join(', ') })
    : t('card.summaryTip', { cards, total });
  const label = `${olderBuild ? '' : '\u2713 '}${t('card.summary', { cards, total })}`
    + (olderBuild ? ` \u00b7 ${t('card.olderBuild')}` : '');
  return (
    <Tooltip title={title}>
      <Chip size="small" label={label}
        sx={{ ...CHIP, ...(s.state === 'all' && !olderBuild ? GREEN : AMBER) }} />
    </Tooltip>
  );
};
