/** One row per uploading file: name, bytes sent / total, a progress bar, the state, and Cancel / Retry / Remove.
 *  The text of a failed file is the server's own error (red, next to the row).  All rules live in lib/uploadQueue.ts. */
import React from 'react';
import { Box, Button, LinearProgress, Typography } from '@mui/material';
import { percent, progressLabel, type UploadItem } from '../../lib/uploadQueue';

export interface UploadRowLabels {
  queued: string; failed: string; cancelled: string; cancel: string; retry: string; dismiss: string;
}

interface Props {
  items: UploadItem[];
  labels: UploadRowLabels;
  language?: string;
  onCancel: (id: number) => void;
  onRetry: (id: number) => void;
  onDismiss: (id: number) => void;
}

const UploadRows: React.FC<Props> = ({ items, labels, language = 'en', onCancel, onRetry, onDismiss }) => {
  if (!items.length) return null;
  return <Box data-testid="upload-rows" sx={{ display: 'flex', flexDirection: 'column', gap: 0.75, mb: 1.25 }}>
    {items.map((i) => {
      const active = i.status === 'queued' || i.status === 'uploading';
      return <Box key={i.id} data-testid="upload-row" data-status={i.status} sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 12.5, minWidth: 0, flex: '1 1 180px', overflowWrap: 'anywhere' }}>{i.name}</Typography>
        <Typography sx={{ fontSize: 12, color: 'var(--text-2)', whiteSpace: 'nowrap' }}>{i.status === 'queued' ? labels.queued : progressLabel(i.status === 'done' ? { loaded: i.size, size: i.size } : i, language)}</Typography>
        <LinearProgress variant="determinate" value={percent(i)} color={i.status === 'failed' ? 'error' : i.status === 'done' ? 'success' : 'primary'}
          aria-label={i.name} sx={{ width: 140, flex: '0 0 140px', opacity: i.status === 'cancelled' || i.status === 'queued' ? 0.4 : 1 }} />
        <Typography sx={{ fontSize: 12, color: 'var(--text-2)', minWidth: 44, textAlign: 'right' }}>{i.status === 'uploading' || i.status === 'done' ? `${percent(i)} %` : (i.status === 'queued' ? labels.queued : i.status === 'failed' ? labels.failed : labels.cancelled)}</Typography>
        {i.status === 'failed' && i.error && <Typography role="alert" sx={{ fontSize: 11, color: '#f87171', overflowWrap: 'anywhere' }}>{i.error}</Typography>}
        {active && <Button size="small" sx={{ textTransform: 'none' }} onClick={() => onCancel(i.id)}>{labels.cancel}</Button>}
        {(i.status === 'failed' || i.status === 'cancelled') && <Button size="small" sx={{ textTransform: 'none' }} onClick={() => onRetry(i.id)}>{labels.retry}</Button>}
        {!active && <Button size="small" sx={{ textTransform: 'none' }} onClick={() => onDismiss(i.id)}>{labels.dismiss}</Button>}
      </Box>;
    })}
  </Box>;
};

export default UploadRows;
