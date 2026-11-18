import { Fragment } from 'react';
import { Box, Button, Chip, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, Tooltip, Typography } from '@mui/material';
import ChatBubbleOutlineIcon from '@mui/icons-material/ChatBubbleOutline';
import type { Completeness, ProjectSummary, ProjectThreadMessage } from '../../types';
import type { StepEnv } from './env';
import { specTable, type RowState, type TableRow as SpecRow } from './specTableModel';
import { overallProgress } from './steps';

const cell = { py: 0.6, px: 1.25, fontSize: 13, borderColor: 'var(--line-soft)' } as const;
const STATE_COLOR: Record<RowState, 'default' | 'warning' | 'error'> = { answered: 'default', unknown: 'default', missing: 'warning', invalid: 'error' };

/** The Specification as ONE compact read table (owner 2026-11-18): a header row per wizard step (with Edit), then one line per
 *  value - label, value, unit, and its state when it is open; the key operating points as their own small table.  A row click
 *  (or Edit) opens the Wizard at that field / step; a viewer who cannot edit the specification gets no edit control and the rows
 *  do not react to a click. */
export default function SpecTable({ env, project, completeness, messages, headerExtra, onOpenField, onOpenStep }: {
  env: Pick<StepEnv, 'tr' | 'lang' | 'draft' | 'steps' | 'profile' | 'canEditSpec'>;
  project: ProjectSummary; completeness?: Completeness; messages: ProjectThreadMessage[];
  headerExtra?: React.ReactNode;
  onOpenField: (fieldId: string) => void; onOpenStep: (index: number) => void;
}) {
  const { tr, lang, draft, steps, profile, canEditSpec } = env;
  const sections = specTable(steps, draft, { lang, t: tr, profile, messages });
  const progress = overallProgress(completeness);
  const rowProps = (fieldId: string) => canEditSpec ? {
    hover: true, role: 'button', tabIndex: 0, 'data-field': fieldId, title: tr('wizard.table.openField'),
    onClick: () => onOpenField(fieldId),
    onKeyDown: (e: React.KeyboardEvent) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpenField(fieldId); } },
    sx: { cursor: 'pointer', '&:focus-visible': { outline: '2px solid', outlineColor: 'primary.main', outlineOffset: -2 } },
  } : { 'data-field': fieldId };
  const stateCell = (r: SpecRow) => <TableCell sx={{ ...cell, width: 150, whiteSpace: 'nowrap' }}>
    <Stack direction="row" spacing={0.75} alignItems="center" justifyContent="flex-end">
      {r.state !== 'answered' && <Chip size="small" variant="outlined" color={STATE_COLOR[r.state]} data-state={r.state} label={tr(`wizard.review.state.${r.state}`)} sx={{ height: 20, fontSize: 11 }} />}
      {r.comments > 0 && <Tooltip title={tr('wizard.table.comments', { count: r.comments })}><Box component="span" data-testid="row-comments" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.3, color: 'primary.main', fontSize: 12 }}><ChatBubbleOutlineIcon sx={{ fontSize: 14 }} />{r.comments}</Box></Tooltip>}
    </Stack>
  </TableCell>;

  return <Stack spacing={2} data-testid="spec-table" sx={{ minWidth: 0, maxWidth: 1100 }}>
    <Stack direction={{ xs: 'column', md: 'row' }} spacing={{ xs: 1, md: 3 }} alignItems={{ md: 'center' }} sx={{ bgcolor: 'var(--panel)', border: '1px solid var(--line)', borderRadius: '14px', px: { xs: 2, md: 3 }, py: 1.5 }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Tooltip title={project.customer_org || ''}><Typography sx={{ fontSize: 12, color: 'var(--text-3)', overflowWrap: 'anywhere' }}>{tr('wizard.projectLabel', { title: project.title })}</Typography></Tooltip>
        <Typography component="h1" sx={{ fontSize: 18, fontWeight: 600, color: 'var(--text-0)' }}>{tr('wizard.title')}{!canEditSpec && <Tooltip title={tr('specReadOnlyTip')}><Typography component="span" sx={{ ml: 1.5, fontSize: 12, fontWeight: 400, color: 'var(--text-3)' }}>{tr('specReadOnly')}</Typography></Tooltip>}</Typography>
      </Box>
      <Typography role="status" sx={{ fontSize: 12.5, color: 'var(--text-2)', whiteSpace: 'nowrap' }}>{tr('wizard.progress', { answered: progress.answered, total: progress.total })}</Typography>
      {headerExtra}
    </Stack>
    <Paper elevation={0} sx={{ bgcolor: 'var(--panel)', border: '1px solid var(--line)', borderRadius: '14px', overflowX: 'auto' }}>
      <Table size="small" aria-label={tr('wizard.title')} sx={{ minWidth: 520 }}>
        <TableHead><TableRow>
          <TableCell sx={{ ...cell, fontWeight: 600, color: 'var(--text-2)' }}>{tr('wizard.table.parameter')}</TableCell>
          <TableCell sx={{ ...cell, fontWeight: 600, color: 'var(--text-2)' }}>{tr('wizard.table.value')}</TableCell>
          <TableCell sx={{ ...cell, fontWeight: 600, color: 'var(--text-2)', width: 80 }}>{tr('wizard.table.unit')}</TableCell>
          <TableCell sx={{ ...cell, width: 150 }} />
        </TableRow></TableHead>
        <TableBody>
          {sections.map((s) => <Fragment key={s.stepId}>
            <TableRow data-section={s.stepId}>
              <TableCell colSpan={4} sx={{ ...cell, bgcolor: 'var(--panel-2)', pt: 1.2 }}>
                <Stack direction="row" alignItems="center">
                  <Typography component="h2" sx={{ flex: 1, fontSize: 14, fontWeight: 600, color: 'var(--text-0)' }}>{tr(s.title)}</Typography>
                  {canEditSpec && <Button size="small" data-testid="section-edit" onClick={() => onOpenStep(s.index)} aria-label={`${tr('wizard.review.edit')}: ${tr(s.title)}`} sx={{ textTransform: 'none', minHeight: 0, py: 0.25 }}>{tr('wizard.review.edit')}</Button>}
                </Stack>
              </TableCell>
            </TableRow>
            {s.points.length > 0 && <TableRow {...rowProps('key_points')}>
              <TableCell colSpan={4} sx={{ ...cell, py: 1 }}>
                <Table size="small" aria-label={tr('wizard.op.pointsTitle')} data-testid="spec-table-points">
                  <TableHead><TableRow>
                    {[tr('keyPoints.name'), `${tr('keyPoints.torque')}, N·m`, `${tr('keyPoints.speed')}, rpm`, tr('keyPoints.duration'), tr('wizard.op.power'), tr('wizard.table.role')].map((h) =>
                      <TableCell key={h} sx={{ ...cell, fontWeight: 600, color: 'var(--text-3)', fontSize: 12 }}>{h}</TableCell>)}
                  </TableRow></TableHead>
                  <TableBody>{s.points.map((p) => <TableRow key={p.id}>
                    {[p.name, p.torque, p.speed, p.duration, p.power, p.role].map((v, i) => <TableCell key={i} sx={{ ...cell, fontVariantNumeric: 'tabular-nums', fontWeight: i === 0 ? 500 : 400 }}>{v}</TableCell>)}
                  </TableRow>)}</TableBody>
                </Table>
              </TableCell>
            </TableRow>}
            {s.rows.map((r) => <TableRow key={r.id} {...rowProps(r.fieldId)}>
              <TableCell sx={{ ...cell, color: 'var(--text-2)', width: '38%' }}>{r.label}</TableCell>
              <TableCell sx={{ ...cell, color: r.state === 'answered' ? 'var(--text-0)' : 'var(--text-3)', fontWeight: 500, fontVariantNumeric: 'tabular-nums', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 0 }}>
                {r.title || r.value.length > 40
                  ? <Tooltip title={r.title ? <span style={{ whiteSpace: 'pre-line' }}>{r.title}</span> : r.value}><span>{r.value}</span></Tooltip>
                  : r.value || '—'}
              </TableCell>
              <TableCell sx={{ ...cell, color: 'var(--text-3)' }}>{r.unit}</TableCell>
              {stateCell(r)}
            </TableRow>)}
          </Fragment>)}
        </TableBody>
      </Table>
    </Paper>
  </Stack>;
}
