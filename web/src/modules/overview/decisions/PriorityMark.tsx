/** The one small mark of a project's priority: a red "urgent" or a grey "low" chip, nothing for normal. */
import React from 'react';
import { Chip } from '@mui/material';
import { useTranslation } from 'react-i18next';
import type { OqPriority } from './model';

export const PriorityMark: React.FC<{ priority?: OqPriority | string | null }> = ({ priority }) => {
  const { t } = useTranslation('common');
  if (priority !== 'urgent' && priority !== 'low') return null;
  return <Chip size="small" data-testid="oq-priority-mark" data-priority={priority} label={t(`ownerQueue.priority.${priority}`)}
    sx={{ height: 18, fontSize: 10.5, ml: 0.5, ...(priority === 'urgent' ? { bgcolor: '#b91c1c', color: '#fff' } : { bgcolor: 'var(--panel)', color: 'var(--text-3)' }) }} />;
};
