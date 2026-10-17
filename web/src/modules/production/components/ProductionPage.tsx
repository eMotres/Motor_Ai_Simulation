import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Box, Button, Chip, CircularProgress, Paper, Tab, Tabs, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import HelpTip from '../../../components/common/HelpTip';
import { useAuth } from '../../../contexts/AuthContext';
import AssemblyProcess from './AssemblyProcess';
import BilingualText, { useContentLang } from './BilingualText';
import BomTable from './BomTable';
import ChangesPanel from './ChangesPanel';
import DiscussionPanel from './DiscussionPanel';
import DocumentsPanel from './DocumentsPanel';
import ProcessView from './ProcessView';
import ProductionListPage from './ProductionListPage';
import ProposalsPanel from './ProposalsPanel';
import { TimelineView } from './TimelineView';
import { ProductionApiError, productionApi } from '../api';
import { callerRole, canEditRole, unackedRevisions } from '../helpers';
import type {
  BomRow, DocumentRec, Part, ProcessSection, Production, Proposal, ReadinessSummary, Revision, Thread,
} from '../types';

const TABS = ['bom', 'assembly', 'timeline', 'process', 'documents', 'discussion', 'proposals', 'changes'] as const;
type TabId = typeof TABS[number];

interface Data {
  parts: Part[]; bom: BomRow[]; process: ProcessSection[]; documents: DocumentRec[];
  threads: Thread[]; proposals: Proposal[]; revisions: Revision[];
}
const EMPTY: Data = { parts: [], bom: [], process: [], documents: [], threads: [], proposals: [], revisions: [] };

const ProductionPage: React.FC<{ die: string; config: string; onBack: () => void }> = ({ die, config, onBack }) => {
  const { t } = useTranslation('production');
  const ui = useContentLang();
  const { user, isAdmin } = useAuth();
  const email = user?.email ?? '';
  const [prod, setProd] = useState<Production | null>(null);
  const [data, setData] = useState<Data>(EMPTY);
  const [readiness, setReadiness] = useState<ReadinessSummary | null>(null);
  const [tab, setTab] = useState<TabId>('bom');
  const [loading, setLoading] = useState(true);
  const [nda, setNda] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const p = await productionApi.get(die, config);
      const [parts, bom, process, documents, threads, proposals, revisions, proc] = await Promise.all([
        productionApi.parts(die, config), productionApi.bom(die, config), productionApi.process(die, config),
        productionApi.documents(die, config), productionApi.threads(die, config), productionApi.proposals(die, config),
        productionApi.revisions(die, config),
        productionApi.procurement(die, config).catch(() => null),
      ]);
      setProd(p);
      setData({ parts, bom, process, documents, threads, proposals, revisions });
      if (proc?.summary) setReadiness(proc.summary);
      setErr(null);
      setNda(false);
    } catch (e) {
      if (e instanceof ProductionApiError && e.ndaRequired) setNda(true);
      else setErr(t('errors.load', { reason: e instanceof Error ? e.message : String(e) }));
    } finally { setLoading(false); }
  }, [die, config, t]);
  useEffect(() => { setLoading(true); void reload(); }, [reload]);

  const role = prod ? callerRole(prod.members, email, isAdmin) : null;
  const ctx = useMemo(() => ({
    die, config, role, email, uiLang: ui, canEdit: canEditRole(role), reload: () => { void reload(); },
  }), [die, config, role, email, ui, reload]);
  const toAck = prod ? unackedRevisions(data.revisions, email).length : 0;

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1.5, flexWrap: 'wrap' }}>
        <Button size="small" onClick={onBack} sx={{ textTransform: 'none', fontSize: 12, color: 'var(--text-2)' }}>← {t('back')}</Button>
        <Typography sx={{ fontSize: 16, fontWeight: 800, color: 'var(--text-0)' }}>{die} / {config}</Typography>
        {prod && (
          <>
            <Chip size="small" label={t('parts.revision', { rev: prod.current_revision })} sx={{ height: 18, fontSize: 10.5, fontWeight: 700, bgcolor: 'var(--panel)', color: 'var(--text-2)' }} />
            <Chip size="small" label={t(`status.${prod.status}`)} sx={{ height: 18, fontSize: 10.5, fontWeight: 700, bgcolor: 'var(--panel)', color: 'var(--text-2)' }} />
            {role && <Chip size="small" label={t(`role.${role}`)} sx={{ height: 18, fontSize: 10.5, fontWeight: 700, bgcolor: 'var(--panel)', color: 'var(--text-3)' }} />}
            <Box sx={{ minWidth: 0 }}><BilingualText value={prod.title} size={13} canConfirm={ctx.canEdit} die={die} config={config}
              tref={{ entity: 'production', entity_id: `${die}/${config}`, field: 'title' }} onChanged={ctx.reload} /></Box>
          </>
        )}
      </Box>

      {prod && !loading && (
        // owner 2026-10-17: the tab menu at the very top, fixed while the page scrolls
        <Box sx={{ position: 'sticky', top: 0, zIndex: 20, bgcolor: 'var(--app-bg)', mb: 1.5 }}>
          <Tabs value={tab} onChange={(_, v) => setTab(v as TabId)} variant="scrollable" scrollButtons="auto"
            sx={{ minHeight: 32, borderBottom: '1px solid var(--line-soft)', '& .MuiTab-root': { minHeight: 32, textTransform: 'none', fontSize: 12.5, py: 0.5 } }}>
            {TABS.map((id) => (
              <Tab key={id} value={id} label={id === 'changes' && toAck > 0 ? `${t('tabs.changes')} (${toAck})` : t(`tabs.${id}`)} />
            ))}
          </Tabs>
        </Box>
      )}

      {/* Top Readiness Summary Bar */}
      {prod && readiness && !loading && (
        <Paper
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: 1.5,
            px: 2,
            py: 1,
            mb: 1.5,
            bgcolor: readiness.ready_for_assembly ? 'rgba(16, 185, 129, 0.08)' : 'rgba(239, 68, 68, 0.08)',
            border: `1px solid ${readiness.ready_for_assembly ? 'rgba(16, 185, 129, 0.3)' : 'rgba(239, 68, 68, 0.3)'}`,
            borderRadius: 1.5,
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
            <Typography sx={{ fontSize: 13, fontWeight: 700, color: 'var(--text-0)' }}>
              {t('procurement.summary', {
                received: readiness.parts_received_count,
                total: readiness.parts_total_count,
                pct: Math.round(readiness.received_pct),
                late: readiness.late_count,
                assemblyStatus: readiness.ready_for_assembly
                  ? `✓ ${t('procurement.readyForAssembly')}`
                  : `✗ ${t('procurement.notReadyForAssembly')}`,
              })}
            </Typography>
            {readiness.at_risk_count > 0 && (
              <Chip
                size="small"
                label={`${readiness.at_risk_count} ${t('procurement.status.at_risk')}`}
                sx={{
                  height: 20,
                  fontSize: 11,
                  bgcolor: 'rgba(245, 158, 11, 0.15)',
                  color: '#f59e0b',
                  border: '1px solid rgba(245, 158, 11, 0.4)',
                }}
              />
            )}
          </Box>
          {readiness.assembly_warnings && readiness.assembly_warnings.length > 0 && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Typography sx={{ fontSize: 11.5, color: '#ef4444', fontWeight: 600 }}>
                ⚠️ {readiness.assembly_warnings[0].message}
              </Typography>
            </Box>
          )}
        </Paper>
      )}

      {loading && <Box sx={{ display: 'flex', gap: 1.5, color: 'var(--text-3)', py: 3 }}><CircularProgress size={18} /><Typography sx={{ fontSize: 13 }}>{t('loading')}</Typography></Box>}
      {nda && (
        <Alert severity="warning" sx={{ fontSize: 12.5, alignItems: 'center' }}>
          {t('nda.required')} <HelpTip title={t('nda.requiredTip')} />
        </Alert>
      )}
      {err && <Typography sx={{ fontSize: 12, color: '#f87171' }}>{err}</Typography>}

      {prod && !loading && (
        <>

          {tab === 'bom' && (
            <>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 1 }}>
                <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('col.orderQty')}: {prod.order_quantity}</Typography>
                <HelpTip title={t('bom.tip')} />
              </Box>
              <BomTable
                rows={data.bom}
                parts={data.parts}
                orderQuantity={prod.order_quantity}
                ctx={ctx}
                shareCostsWithOem={prod.share_costs_with_oem}
                documents={data.documents}
                threads={data.threads}
                proposals={data.proposals}
                onPartChanged={reload}
              />
            </>
          )}
          {tab === 'assembly' && <AssemblyProcess ctx={ctx} parts={data.parts} />}
          {tab === 'timeline' && <TimelineView ctx={ctx} prod={prod} />}
          {tab === 'process' && <ProcessView sections={data.process} ctx={ctx} />}
          {tab === 'documents' && <DocumentsPanel documents={data.documents} parts={data.parts} ctx={ctx} />}
          {tab === 'discussion' && <DiscussionPanel threads={data.threads} parts={data.parts} ctx={ctx} />}
          {tab === 'proposals' && <ProposalsPanel proposals={data.proposals} parts={data.parts} ctx={ctx} />}
          {tab === 'changes' && <ChangesPanel revisions={data.revisions} currentRevision={prod.current_revision} ctx={ctx} />}
        </>
      )}
    </Box>
  );
};

/** The top-level Production tab: the list, and one production once opened. */
export const ProductionTab: React.FC = () => {
  const [sel, setSel] = useState<{ die: string; config: string } | null>(null);
  return (
    <Box sx={{ p: 3, overflowY: 'auto', height: '100%' }}>
      {sel
        ? <ProductionPage die={sel.die} config={sel.config} onBack={() => setSel(null)} />
        : <ProductionListPage onOpen={(p) => setSel({ die: p.die, config: p.config })} />}
    </Box>
  );
};

export default ProductionPage;
