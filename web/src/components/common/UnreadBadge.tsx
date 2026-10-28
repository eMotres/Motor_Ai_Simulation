/** The red unread-messages count beside the name of an overview item (Development cards / list, Production cards / list); nothing for 0. */
import React from 'react';
import { Chip, Tooltip } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { unreadLabel } from '../../lib/unreadMessages';

const UnreadBadge: React.FC<{ count: number }> = ({ count }) => {
  const { t } = useTranslation('common');
  if (!(count > 0)) return null;
  const tip = t('unreadMessagesTip', { count });
  return (
    <Tooltip title={tip}>
      <Chip size="small" color="error" label={unreadLabel(count)} aria-label={tip} data-testid="unread-badge"
        sx={{ ml: 1, height: 18, fontSize: 10.5, fontWeight: 700, verticalAlign: 'middle' }} />
    </Tooltip>
  );
};

export default UnreadBadge;
