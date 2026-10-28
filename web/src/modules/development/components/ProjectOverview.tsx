/** The project overview of the Development tab in its two viewers (owner 2026-10-28): cards (default) or a list in the style of the Production table. */
import React from 'react';
import { Box, Chip, Paper, Table, TableBody, TableCell, TableHead, TableRow, Tooltip, Typography } from '@mui/material';
import UnreadBadge from '../../../components/common/UnreadBadge';
import CardMotorThumb, { useDieIndex } from '../../../components/common/CardMotorThumb';
import { projectCardMotor } from '../../../lib/cardMotor';
import { NARROW } from '../../../lib/narrowLayout';
import type { ProjectSummary } from '../types';
import type { ViewMode } from '../../../lib/viewMode';

export interface ProjectOverviewProps {
  projects: ProjectSummary[];
  mode: ViewMode;
  unread: (p: ProjectSummary) => number;
  /** the signed-in account, to show its role in each project */
  email: string;
  language: string;
  onOpen: (p: ProjectSummary) => void;
  /** the page's translator (development namespace) */
  t: (key: string, opts?: Record<string, unknown>) => any;
}

const numbers = (p: ProjectSummary) => {
  const c = p.completeness;
  return { filled: c?.filled_required ?? c?.filled ?? 0, total: c?.total_required ?? c?.required ?? 0 };
};

/** Enter / Space open the item like a click (rows and cards are not links). */
const keyOpen = (open: () => void) => (e: React.KeyboardEvent) => {
  if (e.target !== e.currentTarget) return;
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
};

const memberRoleOf = (p: ProjectSummary, email: string) =>
  p.members?.find((m) => m.email.toLowerCase() === email.toLowerCase())?.role ?? '';

const dateOf = (iso: string | undefined, language: string) => {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime()) ? d.toLocaleDateString(language) : '';
};

const Bar: React.FC<{ filled: number; total: number }> = ({ filled, total }) => (
  <Box sx={{ flex: '1 1 auto', minWidth: 40, height: 5, bgcolor: 'var(--panel)', borderRadius: 2 }}>
    <Box sx={{ width: `${total ? Math.min(100, filled / total * 100) : 0}%`, height: '100%', bgcolor: 'primary.main', borderRadius: 2 }} />
  </Box>
);

const ProjectOverview: React.FC<ProjectOverviewProps> = ({ projects, mode, unread, email, language, onOpen, t }) => {
  const dieIndex = useDieIndex(mode === 'cards');   // the catalog's dies, for the motor picture of each card
  if (mode === 'list') {
    return (
      <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, overflow: 'auto' }}>
        <Table size="small" sx={{
          /* Phone: keep the columns readable and scroll the table sideways (the Paper scrolls). */
          [NARROW]: { minWidth: 720 },
          '& td, & th': { borderColor: 'var(--panel)', fontSize: 12.5 },
          '& th': { color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', fontSize: 10, letterSpacing: '0.04em' },
        }}>
          <TableHead>
            <TableRow>
              <TableCell>{t('col.project')}</TableCell><TableCell>{t('col.customer')}</TableCell>
              <TableCell align="center">{t('col.stage')}</TableCell><TableCell sx={{ minWidth: 150 }}>{t('col.required')}</TableCell>
              <TableCell align="center">{t('col.role')}</TableCell><TableCell align="right">{t('col.updated')}</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {projects.map((p) => {
              const c = numbers(p), n = unread(p), role = memberRoleOf(p, email);
              return (
                <TableRow key={p.id} hover tabIndex={0} sx={{ cursor: 'pointer' }} onClick={() => onOpen(p)} onKeyDown={keyOpen(() => onOpen(p))}>
                  <TableCell sx={{ color: 'var(--text-0)', fontWeight: 600 }}>{p.title}<UnreadBadge count={n} /></TableCell>
                  <TableCell sx={{ color: 'var(--text-2)' }}>{p.customer_org}</TableCell>
                  <TableCell align="center"><Chip size="small" label={t(`status.${p.status}`)} sx={{ height: 18, fontSize: 10.5, fontWeight: 700, bgcolor: 'var(--panel)', color: 'var(--text-2)' }} /></TableCell>
                  <TableCell>
                    <Tooltip title={t('completeness', { filled: c.filled, total: c.total })}>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{c.filled}/{c.total}</Typography><Bar filled={c.filled} total={c.total} />
                      </Box>
                    </Tooltip>
                  </TableCell>
                  <TableCell align="center" sx={{ color: 'var(--text-2)' }}>{role ? t(`memberRole.${role}`) : ''}</TableCell>
                  <TableCell align="right" sx={{ color: 'var(--text-3)', whiteSpace: 'nowrap' }}>{dateOf(p.updated_at, language)}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Paper>
    );
  }
  return (
    <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(260px,1fr))', gap: 1.5 }}>
      {projects.map((p) => {
        const c = numbers(p), n = unread(p), motor = projectCardMotor(p.linked_motors, dieIndex);
        return (
          <Paper key={p.id} tabIndex={0} onClick={() => onOpen(p)} onKeyDown={keyOpen(() => onOpen(p))}
            sx={{ position: 'relative', p: 1.8, minHeight: motor ? 108 : undefined, cursor: 'pointer', bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', '&:hover': { borderColor: 'primary.main' } }}>
            <Box sx={{ display: 'flex', gap: 1, alignItems: 'start' }}>
              <Typography sx={{ flex: 1, fontWeight: 700, color: 'var(--text-0)' }}>{p.title}<UnreadBadge count={n} /></Typography>
              <Chip size="small" label={t(`status.${p.status}`)} />
            </Box>
            <Box sx={{ pr: motor ? 9 : 0 }}>   {/* keeps the text clear of the picture in the corner */}
              <Typography sx={{ color: 'var(--text-3)', fontSize: 12, mt: 0.5 }}>{p.customer_org}</Typography>
              {/* with a picture in the corner the label and the bar stack (owner 2026-10-28: side by side the bar shrank to a stub) */}
              <Box sx={{ mt: 1.2, display: 'flex', flexDirection: motor ? 'column' : 'row', alignItems: motor ? 'stretch' : 'center', gap: motor ? 0.5 : 1 }}>
                <Typography sx={{ fontSize: 11, color: 'var(--text-2)', whiteSpace: 'nowrap' }}>{t('completeness', { filled: c.filled, total: c.total })}</Typography>
                <Bar filled={c.filled} total={c.total} />
              </Box>
            </Box>
            {motor && <CardMotorThumb motor={motor} />}
          </Paper>
        );
      })}
    </Box>
  );
};

export default ProjectOverview;
