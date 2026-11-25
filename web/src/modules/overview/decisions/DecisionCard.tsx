/** One card of the owner queue: who asks, how old, the question, what waits, the detail in full, then the buttons.
 *  Presentational: all state lives in DecisionsView, so the render test can show every kind. */
import React from 'react';
import { Box, Button, Chip, Link, Paper, TextField, Tooltip, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { MiniMarkdown } from '../../designer/miniMarkdown';
import { ageParts, isApproval, type OqItem } from './model';

export interface DecisionCardProps {
  item: OqItem; now: number; projectTitle?: string; projectHref?: string | null;
  text: string; onText: (v: string) => void; busy?: boolean; error?: string;
  onOption: (label: string) => void; onSend: () => void; onRead: () => void;
}

const DecisionCard: React.FC<DecisionCardProps> = ({ item, now, projectTitle, projectHref, text, onText, busy, error, onOption, onSend, onRead }) => {
  const { t } = useTranslation('common');
  const age = ageParts(item.created_at, now);
  const approval = isApproval(item.kind);
  const isMessage = item.kind === 'message';
  return (
    <Paper data-testid="oq-card" data-kind={item.kind} sx={{ p: 2, bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', mb: 1 }}>
        <Chip size="small" label={t(`ownerQueue.kind.${item.kind}`)} sx={{ height: 20, fontSize: 11 }} />
        {item.project_id && (
          <Link data-testid="oq-project" href={projectHref ?? undefined} underline="hover" sx={{ fontWeight: 700, fontSize: 13 }}>{projectTitle || item.project_id}</Link>
        )}
        <Tooltip title={t('ownerQueue.token', { name: item.token_name })}>
          <Typography component="span" data-testid="oq-from" sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('ownerQueue.from', { session: item.asked_by })}</Typography>
        </Tooltip>
        <Typography component="span" sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t(`ownerQueue.${age.unit}`, { n: age.n })}</Typography>
      </Box>
      <Typography data-testid="oq-question" sx={{ fontSize: 16, fontWeight: 700, color: 'var(--text-0)' }}>{item.question}</Typography>
      {item.blocks && <Typography data-testid="oq-blocks" sx={{ fontSize: 12.5, color: 'var(--text-2)', mt: 0.5 }}>{t('ownerQueue.blocks', { text: item.blocks })}</Typography>}
      {item.kind === 'outside_message' && (
        <Typography data-testid="oq-recipient" sx={{ fontSize: 13, mt: 1, fontWeight: 600, wordBreak: 'break-word' }}>{t('ownerQueue.recipient', { to: item.recipient || '' })}</Typography>
      )}
      {item.detail && (
        // never collapsed or clipped: for an approval the owner reads the whole text he approves
        <Box data-testid="oq-detail" data-full={approval ? 'true' : 'false'} sx={{ mt: 1.5, p: 1.25, border: '1px solid var(--line-soft)', borderRadius: 1, overflowX: 'auto' }}>
          {approval && <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-3)', textTransform: 'uppercase', mb: 0.5 }}>
            {t(item.kind === 'catalog_write' ? 'ownerQueue.detailCatalog' : 'ownerQueue.detailFull')}</Typography>}
          <MiniMarkdown text={item.detail} />
        </Box>
      )}
      <Box data-testid="oq-buttons" sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center', mt: 1.5 }}>
        {isMessage ? (
          <Button size="small" variant="contained" disabled={busy} onClick={onRead} data-testid="oq-read" sx={{ textTransform: 'none' }}>{t('ownerQueue.read')}</Button>
        ) : item.options.map((o, i) => (
          <Button key={o.label} size="small" disabled={busy} variant={o.recommended ? 'contained' : 'outlined'} data-recommended={o.recommended ? 'true' : undefined}
            data-testid="oq-option" onClick={() => onOption(o.label)} sx={{ textTransform: 'none' }}>
            {`${i + 1}. ${o.label}`}
            {o.recommended && <Box component="span" sx={{ ml: 0.75, fontSize: 10.5, opacity: 0.85 }}>{`(${t('ownerQueue.recommended')})`}</Box>}
          </Button>
        ))}
      </Box>
      <Box sx={{ display: 'flex', gap: 1, alignItems: 'flex-start', mt: 1.25 }}>
        <TextField size="small" multiline minRows={1} maxRows={6} fullWidth value={text} onChange={(e) => onText(e.target.value)}
          label={t(isMessage ? 'ownerQueue.replyLabel' : 'ownerQueue.answerLabel')} inputProps={{ 'data-testid': 'oq-text', maxLength: 4000 }} />
        {!approval && <Button size="small" variant="outlined" disabled={busy || !text.trim()} onClick={onSend} data-testid="oq-send" sx={{ textTransform: 'none', mt: 0.25 }}>
          {t(isMessage ? 'ownerQueue.reply' : 'ownerQueue.send')}</Button>}
      </Box>
      {error && <Typography data-testid="oq-error" sx={{ fontSize: 12, color: '#f87171', mt: 0.75 }}>{error}</Typography>}
    </Paper>
  );
};

export default DecisionCard;
