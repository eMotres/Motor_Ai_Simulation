/** Earlier proposals, READ-ONLY, for staff only (owner 2026-11-15: the Proposals tab is gone, Discussion and Changes stay).  Nothing is deleted:
 *  a stored proposal and its decision stay reachable here; new questions go into the Discussion and design changes into a published revision. */
import React from 'react';
import { Box, Chip, Paper, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import BilingualText from './BilingualText';
import type { Part, Proposal } from '../types';

const COLOR: Record<Proposal['status'], string> = { open: '#2563eb', accepted: '#16a34a', rejected: '#dc2626', implemented: '#7c3aed' };

const LegacyProposals: React.FC<{ proposals: Proposal[]; parts: Part[] }> = ({ proposals, parts }) => {
  const { t } = useTranslation('production');
  if (proposals.length === 0) return null;
  const byId = new Map(parts.map((p) => [p.id, p]));
  return (
    <Box component="details" sx={{ mt: 2 }} data-testid="legacy-proposals">
      <Box component="summary" sx={{ cursor: 'pointer', fontSize: 12.5, color: 'var(--text-2)' }}>
        {t('proposals.earlier', { count: proposals.length })}
      </Box>
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1, mt: 1 }}>
        {proposals.map((p) => {
          const part = p.part_id ? byId.get(p.part_id) : undefined;
          return (
            <Paper key={p.id} data-item-id={p.id} sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, p: 1.25 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', mb: 0.5 }}>
                <Chip size="small" label={t(`proposals.status.${p.status}`)} sx={{ height: 18, fontSize: 10.5, fontWeight: 700, color: COLOR[p.status], bgcolor: `${COLOR[p.status]}1f` }} />
                {part ? <BilingualText value={part.name} size={12} bold /> : <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('proposals.general')}</Typography>}
                <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>{p.author_email} · {p.created_at.slice(0, 10)}</Typography>
                {p.linked_revision && <Chip size="small" label={t('proposals.linked', { rev: p.linked_revision })} sx={{ height: 18, fontSize: 10.5 }} />}
              </Box>
              <BilingualText value={p.text} size={12.5} />
              {p.decision_note && <Box sx={{ mt: 0.5, pl: 1, borderLeft: '2px solid var(--line-soft)' }}><BilingualText value={p.decision_note} size={12} /></Box>}
            </Paper>
          );
        })}
      </Box>
    </Box>
  );
};

export default LegacyProposals;
