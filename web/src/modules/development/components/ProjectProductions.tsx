/** The Production view of a project (owner 2026-11-02): one block per linked motor that has a production.  Read-only; "Open in
 *  Production" opens that die/config in the Production module, where the server shows what the caller may see. */
import { useState } from 'react';
import { Box, Button, Chip, Dialog, DialogContent, DialogTitle, IconButton, Paper, Stack, Tooltip, Typography } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import HelpTip from '../../../components/common/HelpTip';
import { openProduction } from '../../../lib/openProduction';
import TestsPanel from '../../tests/components/TestsPanel';
import { productionBlocks, type ProductionBlock, type ProjectProductionEntry } from '../projectProductions';

interface Props {
  projectId: string;
  entries: ProjectProductionEntry[];
  language: string;
  t: (key: string, opts?: Record<string, unknown>) => string;
}

export default function ProjectProductions({ projectId, entries, language, t }: Props) {
  const blocks = productionBlocks(entries, language);
  const [tests, setTests] = useState<ProductionBlock | null>(null);
  const linked = (b: ProductionBlock) => entries.find((e) => e.die === b.die && e.config === b.config);
  if (blocks.length === 0) return <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('productionView.none')}</Typography>;
  return <Box>
    <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}><HelpTip title={t('productionView.tip')} /></Box>
    <Stack spacing={1}>
      {blocks.map((b) => <Paper key={b.key} data-testid="production-block" sx={{ p: 1.2, bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)' }}>
        <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap" useFlexGap>
          <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-0)', overflowWrap: 'anywhere' }}>{b.die} / {b.config}{b.productRevision ? ` · ${b.productRevision}` : ''}</Typography>
          <Typography sx={{ fontSize: 12.5, color: 'var(--text-1)', flex: '1 1 200px', minWidth: 0, overflowWrap: 'anywhere' }}>{b.title}</Typography>
          <Chip size="small" label={t('productionView.revision', { rev: b.revision })} sx={{ height: 18, fontSize: 10.5 }} />
          <Chip size="small" label={t(`productionView.status.${b.status}`, { defaultValue: b.status })} sx={{ height: 18, fontSize: 10.5 }} />
          {b.canOpen && <Tooltip title={t('productionView.openTip')}><Button size="small" onClick={() => openProduction(b.die, b.config)}>{t('productionView.open')}</Button></Tooltip>}
        </Stack>
        {(b.counts.length > 0 || b.readiness) && <Stack direction="row" alignItems="center" spacing={0.6} flexWrap="wrap" useFlexGap sx={{ mt: 0.6 }}>
          {b.counts.map((c) => <Tooltip key={c.key} title={t(`productionView.count.${c.key}Tip`)}>
            <Chip size="small" variant="outlined" label={`${t(`productionView.count.${c.key}`)} ${c.value}`} sx={{ height: 18, fontSize: 10.5 }}
              {...(c.key === 'tests' && c.value > 0 ? { clickable: true, onClick: () => setTests(b) } : {})} />
          </Tooltip>)}
          {b.testKinds.length > 0 && <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{b.testKinds.map((k) => `${t(`productionView.testKind.${k.kind}`, { defaultValue: k.kind })} ${k.count}`).join(' · ')}</Typography>}
          {b.readiness && <Tooltip title={t('productionView.readinessTip')}><Typography sx={{ fontSize: 11, color: b.readiness.ready ? '#10b981' : 'var(--text-3)' }}>
            {t('productionView.readiness', { received: b.readiness.received, total: b.readiness.total, pct: b.readiness.pct, late: b.readiness.late })}
          </Typography></Tooltip>}
        </Stack>}
      </Paper>)}
    </Stack>
    <Dialog open={!!tests} onClose={() => setTests(null)} fullWidth maxWidth="lg">
      <DialogTitle sx={{ fontSize: 15, display: 'flex', alignItems: 'center' }}>
        <span style={{ flex: 1 }}>{t('productionView.count.tests')} · {tests?.die} / {tests?.config}</span>
        <IconButton size="small" onClick={() => setTests(null)}><CloseIcon sx={{ fontSize: 18 }} /></IconButton>
      </DialogTitle>
      <DialogContent>
        {tests && <TestsPanel die={linked(tests)?.linked_die || tests.die} config={linked(tests)?.linked_config || tests.config}
          source={{ kind: 'project', projectId, showAll: true }} />}
      </DialogContent>
    </Dialog>
  </Box>;
}
