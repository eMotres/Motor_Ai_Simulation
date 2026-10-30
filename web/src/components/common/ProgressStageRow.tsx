/**
 * ProgressStageRow — the stages of a running solve, left → right in execution
 * order, each a compact segment with its own bar and "n / N frames"
 * (owner 2026-10-30: "split into stages — warm-up, demagnetisation and the final
 * calculation — each with its own bar and frame count, in one row").
 *
 * Done stages are solid, the running one animates, pending ones are greyed and a
 * skipped one (a warm-up served from the cache) is dashed.  An adaptive stage's
 * count is printed "19 / 20+" with a tooltip hint, because its total keeps
 * growing until the start-up transient settles.  The long explanation of each
 * stage lives in its tooltip, never in the row (house rule: no text walls).
 *
 * Narrow screens: the segments wrap onto further lines rather than shrinking
 * below a readable width.  The rules (bar fill, count kind) are in
 * `progressStages.ts`, which `node --test` can run.
 */
import React from 'react';
import { Box, keyframes } from '@mui/material';
import { useTranslation } from 'react-i18next';

import {
  formatStageSeconds, isIndeterminate, isKnownStage, stageCount, stageFraction,
  stageUnit,
} from './progressStages';
import type { ProgressStage } from './progressStages';

const sweep = keyframes`
  0%   { background-position: 0 0; }
  100% { background-position: 28px 0; }
`;
const slide = keyframes`
  0%   { transform: translateX(-100%); }
  100% { transform: translateX(260%); }
`;

const ProgressStageRow: React.FC<{ stages: ProgressStage[] }> = ({ stages }) => {
  const { t } = useTranslation('common');

  return (
    <Box role="list" aria-label={t('progress.label')}
      sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75, flex: '1 1 200px', minWidth: 0 }}>
      {stages.map(s => {
        const known = isKnownStage(s.key);
        const name = known ? t(`progress.name.${s.key}`) : s.name;
        const count = stageCount(s);
        const unit = stageUnit(s);
        const running = s.state === 'running';
        const dim = s.state === 'pending' || s.state === 'skipped';
        const frac = stageFraction(s);
        const countText =
          count.kind === 'exact'   ? t('progress.count', { done: count.done, planned: count.planned, unit })
          : count.kind === 'growing' ? t('progress.countGrowing', { done: count.done, planned: count.planned, unit })
          : count.kind === 'open'    ? t('progress.countOpen', { done: count.done, unit })
          : '–';
        const tip = [
          `${name} · ${t(`progress.state.${s.state}`)}`,
          known ? t(`progress.tip.${s.key}`) : '',
          s.adaptive && (running || s.state === 'pending') ? t('progress.adaptiveHint') : '',
          s.note || '',
          s.elapsed_s > 0 ? `${formatStageSeconds(s.elapsed_s)}` : '',
        ].filter(Boolean).join('\n');

        return (
          <Box key={s.key} role="listitem" title={tip} data-stage={s.key} data-state={s.state}
            sx={{ flex: '1 1 auto', minWidth: 112, cursor: 'help',
              opacity: dim ? 0.55 : 1,
              px: 0.75, py: 0.4, borderRadius: 0.75,
              border: '1px solid',
              borderStyle: s.state === 'skipped' ? 'dashed' : 'solid',
              borderColor: running ? 'var(--line-accent)' : 'var(--line-soft)',
              bgcolor: running ? 'var(--panel-2)' : 'transparent' }}>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', gap: 0.75,
              alignItems: 'baseline', fontSize: 11, lineHeight: 1.3,
              color: running ? 'var(--text-0)' : 'var(--text-2)' }}>
              <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {s.state === 'done' ? '✓ ' : ''}{name}
              </span>
              <span style={{ whiteSpace: 'nowrap', color: running ? 'var(--text-1)' : 'var(--text-3)' }}>
                {countText}
              </span>
            </Box>
            <Box sx={{ position: 'relative', height: 4, mt: 0.4, borderRadius: 2,
              overflow: 'hidden', bgcolor: 'var(--line-soft)' }}>
              {isIndeterminate(s) ? (
                <Box sx={{ position: 'absolute', inset: 0, width: '38%',
                  bgcolor: 'var(--brand)', borderRadius: 2,
                  animation: `${slide} 1.3s ease-in-out infinite` }} />
              ) : (
                <Box sx={{ width: `${(100 * frac).toFixed(1)}%`, height: '100%',
                  transition: 'width 300ms linear',
                  bgcolor: s.state === 'stopped' ? 'var(--text-3)' : 'var(--brand)',
                  ...(running ? {
                    backgroundImage:
                      'linear-gradient(135deg, rgba(255,255,255,0.28) 25%, transparent 25%, '
                      + 'transparent 50%, rgba(255,255,255,0.28) 50%, rgba(255,255,255,0.28) 75%, '
                      + 'transparent 75%)',
                    backgroundSize: '28px 28px',
                    animation: `${sweep} 0.8s linear infinite`,
                  } : {}) }} />
              )}
            </Box>
          </Box>
        );
      })}
    </Box>
  );
};

export default ProgressStageRow;
