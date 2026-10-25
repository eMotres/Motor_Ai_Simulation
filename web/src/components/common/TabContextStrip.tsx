/**
 * TabContextStrip — the line under the tabs on the tabs that do NOT work on the live machine (owner 2026-10-26):
 * Development shows the open project and its motors (the selected variant highlighted), Production shows the motor
 * of the production being viewed, and a click on a chip opens that motor in Configure like the Motors tab does.  It never changes the
 * live machine; lib/stripContext.ts decides which context applies.  One line, labels + tooltips only.
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Chip, Tooltip, Typography } from '@mui/material';
import { openProjectMotorByRef } from '../../lib/openProjectMotor';
import { motorLabel, visibleChips, type StripContext } from '../../lib/stripContext';

type ProjectContext = Extract<StripContext, { kind: 'project' }>;

const BAR_SX = {
  display: 'flex', alignItems: 'center', gap: 1, px: 1.5, py: 0.35, bgcolor: 'var(--panel-2)',
  borderBottom: '1px solid var(--line-soft)', flexShrink: 0, minHeight: 28, overflow: 'hidden', whiteSpace: 'nowrap',
} as const;

const ProjectStrip: React.FC<{ ctx: ProjectContext }> = ({ ctx }) => {
  const { t } = useTranslation('common');
  const { shown, hidden } = visibleChips(ctx.motors, ctx.selected);
  return (
    <Box sx={BAR_SX} data-testid="strip-project">
      <Tooltip title={t('strip.projectTip')}>
        <Typography component="span" sx={{ fontSize: 12, color: 'var(--text-2)', cursor: 'help', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {t('strip.project')}{ctx.title ? ': ' : ''}{ctx.title ? <b style={{ color: 'var(--text-1)' }}>{ctx.title}</b> : null}
        </Typography>
      </Tooltip>
      {shown.map((m) => {
        const selected = ctx.selected !== null && ctx.selected.die === m.die && ctx.selected.config === m.config;
        const blocked = m.pointsReady === false;
        const chip = (
          <Chip size="small" component="span" label={motorLabel(m)}
            color={selected ? 'primary' : 'default'} variant={selected ? 'filled' : 'outlined'}
            disabled={blocked}
            onClick={blocked ? undefined : () => { void openProjectMotorByRef({ id: ctx.projectId, title: ctx.title }, m).catch(() => undefined); }}
            sx={{ height: 20, fontSize: 11, fontWeight: selected ? 700 : 400 }} />
        );
        return (
          <Tooltip key={`${m.die}/${m.config}`} title={blocked ? t('strip.noPointsTip') : t('strip.chipTip')}>
            <span>{chip}</span>
          </Tooltip>
        );
      })}
      {hidden.length > 0 && (
        <Tooltip title={`${t('strip.moreTip')}: ${hidden.map(motorLabel).join(', ')}`}>
          <Chip size="small" variant="outlined" label={`+${hidden.length}`} sx={{ height: 20, fontSize: 11 }} />
        </Tooltip>
      )}
    </Box>
  );
};

const ProductionStrip: React.FC<{ die: string; config: string }> = ({ die, config }) => {
  const { t } = useTranslation('common');
  return (
    <Box sx={BAR_SX} data-testid="strip-production">
      <Tooltip title={t('strip.productionTip')}>
        <Typography component="span" sx={{ fontSize: 12, color: 'var(--text-2)', cursor: 'help', fontVariantNumeric: 'tabular-nums' }}>
          {t('strip.production')}: <b style={{ color: 'var(--text-1)' }}>{die}</b>{' / '}<b style={{ color: 'var(--text-1)' }}>{config}</b>
        </Typography>
      </Tooltip>
    </Box>
  );
};

/** Renders nothing for kind 'none' (and for 'live', which the machine strip draws itself). */
const TabContextStrip: React.FC<{ ctx: StripContext }> = ({ ctx }) => {
  if (ctx.kind === 'project') return <ProjectStrip ctx={ctx} />;
  if (ctx.kind === 'production') return <ProductionStrip die={ctx.die} config={ctx.config} />;
  return null;
};

export default TabContextStrip;
