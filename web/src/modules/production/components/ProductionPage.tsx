/** One production: Parts, BOM, Process, Documents, Discussion, Proposals, Changes. Also exports ProductionTab (list <-> page). */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Box, Button, Chip, CircularProgress, MenuItem, Tab, Tabs, TextField, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import HelpTip from '../../../components/common/HelpTip';
import { useAuth } from '../../../contexts/AuthContext';
import BilingualText, { BilingualInput, useContentLang } from './BilingualText';
import BomTable from './BomTable';
import ChangesPanel from './ChangesPanel';
import DiscussionPanel from './DiscussionPanel';
import DocumentsPanel from './DocumentsPanel';
import PartCard from './PartCard';
import ProcessView from './ProcessView';
import ProductionListPage from './ProductionListPage';
import ProposalsPanel from './ProposalsPanel';
import { ProductionApiError, productionApi } from '../api';
import { callerRole, canEditRole, emptyBilingual, makeBilingual, sortByBom, unackedRevisions } from '../helpers';
import type {
  BomRow, DocumentRec, Lang, Part, ProcessSection, Production, Proposal, Revision, Thread,
} from '../types';

const TABS = ['parts', 'bom', 'process', 'documents', 'discussion', 'proposals', 'changes'] as const;
type TabId = typeof TABS[number];

interface Data {
  parts: Part[]; bom: BomRow[]; process: ProcessSection[]; documents: DocumentRec[];
  threads: Thread[]; proposals: Proposal[]; revisions: Revision[];
}
const EMPTY: Data = { parts: [], bom: [], process: [], documents: [], threads: [], proposals: [], revisions: [] };

const AddPart: React.FC<{ die: string; config: string; onDone: () => void }> = ({ die, config, onDone }) => {
  const { t } = useTranslation('production');
  const ui = useContentLang();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [lang, setLang] = useState<Lang>(ui);
  const [kind, setKind] = useState<Part['kind']>('made');
  const [err, setErr] = useState<string | null>(null);
  if (!open) return <Button size="small" sx={{ textTransform: 'none', alignSelf: 'flex-start' }} onClick={() => setOpen(true)}>{t('parts.add')}</Button>;
  return (
    <Box sx={{ display: 'flex', gap: 1, alignItems: 'flex-start', flexWrap: 'wrap' }}>
      <Box sx={{ flex: 1, minWidth: 260 }}><BilingualInput label={t('parts.name')} value={name} lang={lang} onChange={(v, l) => { setName(v); setLang(l); }} /></Box>
      <TextField select size="small" value={kind} onChange={(e) => setKind(e.target.value as Part['kind'])} sx={{ width: 140 }}>
        {(['made', 'purchased', 'consumable'] as const).map((k) => <MenuItem key={k} value={k}>{t(`parts.kindValue.${k}`)}</MenuItem>)}
      </TextField>
      <Button size="small" variant="contained" disabled={!name.trim()} sx={{ textTransform: 'none' }}
        onClick={async () => {
          try {
            await productionApi.createPart(die, config, { name: makeBilingual(name, lang), description: emptyBilingual(lang), kind, qty_per_motor: 1, material: '', spec: '' });
            setOpen(false); setName(''); onDone();
          } catch (e) { setErr(t('failed', { reason: e instanceof Error ? e.message : String(e) })); }
        }}>{t('add')}</Button>
      <Button size="small" sx={{ textTransform: 'none' }} onClick={() => setOpen(false)}>{t('cancel')}</Button>
      {err && <Typography sx={{ fontSize: 11, color: '#f87171' }}>{err}</Typography>}
    </Box>
  );
};

const ProductionPage: React.FC<{ die: string; config: string; onBack: () => void }> = ({ die, config, onBack }) => {
  const { t } = useTranslation('production');
  const ui = useContentLang();
  const { user, isAdmin } = useAuth();
  const email = user?.email ?? '';
  const [prod, setProd] = useState<Production | null>(null);
  const [data, setData] = useState<Data>(EMPTY);
  const [tab, setTab] = useState<TabId>('parts');
  const [loading, setLoading] = useState(true);
  const [nda, setNda] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const p = await productionApi.get(die, config);
      const [parts, bom, process, documents, threads, proposals, revisions] = await Promise.all([
        productionApi.parts(die, config), productionApi.bom(die, config), productionApi.process(die, config),
        productionApi.documents(die, config), productionApi.threads(die, config), productionApi.proposals(die, config),
        productionApi.revisions(die, config),
      ]);
      setProd(p); setData({ parts, bom, process, documents, threads, proposals, revisions }); setErr(null); setNda(false);
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
  const parts = useMemo(() => sortByBom(data.parts, data.bom), [data.parts, data.bom]);

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

      {loading && <Box sx={{ display: 'flex', gap: 1.5, color: 'var(--text-3)', py: 3 }}><CircularProgress size={18} /><Typography sx={{ fontSize: 13 }}>{t('loading')}</Typography></Box>}
      {nda && (
        <Alert severity="warning" sx={{ fontSize: 12.5, alignItems: 'center' }}>
          {t('nda.required')} <HelpTip title={t('nda.requiredTip')} />
        </Alert>
      )}
      {err && <Typography sx={{ fontSize: 12, color: '#f87171' }}>{err}</Typography>}

      {prod && !loading && (
        <>
          <Tabs value={tab} onChange={(_, v) => setTab(v as TabId)} variant="scrollable" scrollButtons="auto"
            sx={{ minHeight: 32, mb: 1.5, borderBottom: '1px solid var(--line-soft)', '& .MuiTab-root': { minHeight: 32, textTransform: 'none', fontSize: 12.5, py: 0.5 } }}>
            {TABS.map((id) => (
              <Tab key={id} value={id} label={id === 'changes' && toAck > 0 ? `${t('tabs.changes')} (${toAck})` : t(`tabs.${id}`)} />
            ))}
          </Tabs>
          {tab === 'parts' && (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.25 }}>
              {ctx.canEdit && <AddPart die={die} config={config} onDone={ctx.reload} />}
              {parts.length === 0 && <Typography sx={{ fontSize: 13, color: 'var(--text-4)' }}>{t('parts.none')}</Typography>}
              {parts.map((p) => <PartCard key={p.id} part={p} ctx={ctx} onChanged={ctx.reload} />)}
            </Box>
          )}
          {tab === 'bom' && (
            <>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 1 }}>
                <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('col.orderQty')}: {prod.order_quantity}</Typography>
                <HelpTip title={t('bom.tip')} />
              </Box>
              <BomTable rows={data.bom} parts={data.parts} orderQuantity={prod.order_quantity} ctx={ctx} />
            </>
          )}
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
