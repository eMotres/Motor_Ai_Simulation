/**
 * TicketDraftCard — the assistant's proposed ticket, shown for the user to check,
 * edit and send (owner 2026-10-05: the user confirms; nothing is filed otherwise).
 *
 * Pure view: the state machine is lib/supportFlow.ts; the widget wires it.
 */
import React from 'react';
import {
  Box, Button, CircularProgress, TextField, ToggleButton, ToggleButtonGroup, Typography,
} from '@mui/material';
import { useTranslation } from 'react-i18next';
import {
  TICKET_TYPES, MAX_TITLE, MAX_DESCRIPTION, canSend, type FlowState, type TicketDraft,
} from '../../lib/supportFlow';

const BODY = { fontSize: 13, lineHeight: 1.45 } as const;

const TicketDraftCard: React.FC<{
  flow: FlowState;
  signedIn: boolean;
  onEdit: (patch: Partial<TicketDraft>) => void;
  onSend: () => void;
  onDismiss: () => void;
}> = ({ flow, signedIn, onEdit, onSend, onDismiss }) => {
  const { t } = useTranslation('support');
  const ds = flow.draft;
  if (!ds) return null;
  const locked = ds.status === 'sending' || ds.status === 'sent';

  if (ds.status === 'sent') {
    return (
      <Box sx={{ border: '1px solid #16a34a', borderRadius: 1.5, p: 1.25, bgcolor: 'var(--panel)' }} role="status">
        <Typography sx={{ ...BODY, color: '#4ade80', fontWeight: 700 }}>
          ✓ {ds.ticketId ? t('draft.sent', { id: ds.ticketId }) : t('draft.sentNoId')}
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ border: '1px solid var(--line-accent)', borderRadius: 1.5, p: 1.25, bgcolor: 'var(--panel)', display: 'flex', flexDirection: 'column', gap: 1 }}>
      <Typography sx={{ ...BODY, fontWeight: 800, color: 'var(--text-0)' }}>{t('draft.heading')}</Typography>
      {ds.intro && <Typography sx={{ ...BODY, color: 'var(--text-1)' }}>{t('draft.ready')}</Typography>}
      <ToggleButtonGroup exclusive size="small" fullWidth value={ds.draft.type} disabled={locked}
        aria-label={t('draft.typeLabel')}
        onChange={(_, v) => { if (v) onEdit({ type: v }); }}>
        {TICKET_TYPES.map((k) => (
          <ToggleButton key={k} value={k} sx={{ textTransform: 'none', fontSize: 13, py: 0.25, px: 0.5 }}>
            {t(`type.${k}`)}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>
      <TextField label={t('draft.titleLabel')} value={ds.draft.title} disabled={locked} size="small" fullWidth
        onChange={(e) => onEdit({ title: e.target.value })}
        slotProps={{ htmlInput: { maxLength: MAX_TITLE } }}
        sx={{ '& .MuiInputBase-input': { fontSize: 13 } }} />
      <TextField label={t('draft.descriptionLabel')} value={ds.draft.description} disabled={locked} size="small"
        fullWidth multiline minRows={4} maxRows={10}
        onChange={(e) => onEdit({ description: e.target.value })}
        slotProps={{ htmlInput: { maxLength: MAX_DESCRIPTION } }}
        sx={{ '& .MuiInputBase-input': { fontSize: 13 } }} />
      <Typography sx={{ ...BODY, color: 'var(--text-2)' }}>{t('draft.attached')}</Typography>
      {ds.status === 'error' && (
        <Typography sx={{ ...BODY, color: '#f87171' }} role="alert">{t('draft.error', { error: ds.error ?? '' })}</Typography>
      )}
      {!signedIn && <Typography sx={{ ...BODY, color: '#fbbf24' }}>{t('draft.signIn')}</Typography>}
      <Box sx={{ display: 'flex', gap: 1 }}>
        <Button variant="contained" onClick={onSend} disabled={!signedIn || !canSend(flow)}
          sx={{ textTransform: 'none', fontSize: 13, flex: 1 }}>
          {ds.status === 'sending' ? <><CircularProgress size={16} sx={{ mr: 1 }} />{t('draft.sending')}</> : t('draft.send')}
        </Button>
        <Button onClick={onDismiss} disabled={ds.status === 'sending'} sx={{ textTransform: 'none', fontSize: 13 }}>
          {t('draft.dismiss')}
        </Button>
      </Box>
    </Box>
  );
};

export default TicketDraftCard;
