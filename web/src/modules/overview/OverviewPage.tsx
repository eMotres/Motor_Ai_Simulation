/** The owner's Overview (owner 2026-11-16; admins only): every motor and board that a project uses or a production makes, under
 *  its die / platform, with the Project and the Production columns kept apart; a flat Projects tab beside it.  Reads
 *  `/api/overview` (projects, productions, registry ids, display states) and the catalog tree; writes only the display state. */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Accordion, AccordionDetails, AccordionSummary, Badge, Box, Button, Chip, CircularProgress, IconButton, Link, Paper, Popover, Table, TableBody, TableCell,
  TableHead, TableRow, ToggleButton, ToggleButtonGroup, Tooltip, Typography } from '@mui/material';
import ArchiveOutlinedIcon from '@mui/icons-material/ArchiveOutlined';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import RefreshIcon from '@mui/icons-material/Refresh';
import { useTranslation } from 'react-i18next';
import HelpTip from '../../components/common/HelpTip';
import { DisplayStateControl, DisplayStateFilter } from '../../components/common/DisplayStateControl';
import { DISPLAY_FILTERS, filterCounts, type DisplayFilter, type DisplayState } from '../../lib/displayState';
import { displayStateApi } from '../../lib/displayStateApi';
import { dieArchiveApi, type ArchivedDie } from '../../lib/dieArchiveApi';
import { ConfirmDialog, type ConfirmState } from '../../components/common/PromptDialogs';
import { useRememberedView } from '../../lib/useRememberedView';
import { oneOf } from '../../lib/viewMemory';
import { appLinkPath } from '../../lib/appLink';
import { fetchFamilyTree, invalidateFamilyTree } from '../../lib/familyTree';
import { NARROW } from '../../lib/narrowLayout';
import { shownStatus } from '../development/projectStage';
import ArchivedDies from './ArchivedDies';
import DecisionsView from './decisions/DecisionsView';
import { badgeTotal, type OqCount } from './decisions/model';
import { ownerQueueApi } from './decisions/ownerQueueApi';
import { PriorityMark } from './decisions/PriorityMark';
import { buildView, flatProjects, headerChips, type Family, type OverviewAnswer, type OverviewProduction, type OverviewProject, type TreeDie } from './overviewModel';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const DERIVED_STAGES: readonly string[] = ['specification', 'simulation', 'confirmation', 'in_production', 'finished'];
const VIEWS = ['tree', 'projects', 'decisions'] as const;
type View = typeof VIEWS[number];

const STATE_SX: Record<DisplayState, object> = {
  active: {}, parked: { opacity: 0.75, borderStyle: 'dashed' }, archived: { opacity: 0.55, borderStyle: 'dotted' },
};
const tableSx = {
  [NARROW]: { minWidth: 760 },
  '& td, & th': { borderColor: 'var(--panel)', fontSize: 12.5, verticalAlign: 'top' },
  '& th': { color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', fontSize: 10, letterSpacing: '0.04em' },
};
const DASH = <Typography component="span" sx={{ color: 'var(--text-4)' }}>—</Typography>;

const OverviewPage: React.FC = () => {
  const { t } = useTranslation(['common', 'development', 'production']);
  const [answer, setAnswer] = useState<OverviewAnswer | null>(null);
  const [tree, setTree] = useState<TreeDie[]>([]);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);
  const [view, setView] = useRememberedView<View>('overview.view', 'tree', oneOf(VIEWS));
  const [filter, setFilter] = useRememberedView<DisplayFilter>('overview.displayFilter', 'active', oneOf(DISPLAY_FILTERS));
  const [reviewAnchor, setReviewAnchor] = useState<HTMLElement | null>(null);
  const [askConfirm, setAskConfirm] = useState<ConfirmState | null>(null);
  const [oq, setOq] = useState<OqCount | null>(null);
  const [prio, setPrio] = useState<Record<string, { priority: string }>>({});
  const loadCount = useCallback(() => {
    ownerQueueApi.count().then(setOq).catch(() => setOq(null));
    ownerQueueApi.priorities().then(setPrio).catch(() => setPrio({}));
  }, []);
  useEffect(() => { loadCount(); const id = window.setInterval(loadCount, 60000); return () => window.clearInterval(id); }, [loadCount]);

  const load = useCallback(async () => {
    setLoading(true); setErr('');
    try {
      const r = await fetch(`${API}/api/overview`, { cache: 'no-store' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setAnswer(await r.json() as OverviewAnswer);
      fetchFamilyTree({ fresh: true }).then((tr) => setTree((tr.dies ?? []) as TreeDie[])).catch(() => setTree([]));
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const model = useMemo(() => (answer ? buildView(answer, tree, filter) : null), [answer, tree, filter]);
  const projectsFlat = useMemo(() => (answer ? flatProjects(answer, filter) : []), [answer, filter]);
  const projectCounts = useMemo(() => {
    const all = answer ? Object.values(answer.projects) : [];
    return filterCounts(all, (p) => p.display?.state ?? 'active', () => false);
  }, [answer]);

  const setProjectState = async (p: OverviewProject, next: DisplayState, reason: string) => {
    try { await displayStateApi.set({ kind: 'project', project_id: p.id }, next, reason); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    void load();
  };
  // the stage is derived by the server (production -> in production, stored results -> computed), never written back
  // Archive a die that is not needed any more (owner 2026-11-17): the server says first whether it may go, then asks for the name.
  const changed = () => { invalidateFamilyTree(); try { window.dispatchEvent(new CustomEvent('family-changed')); } catch { /* SSR */ } void load(); };
  const startArchive = async (die: string) => {
    setErr('');
    try {
      const c = await dieArchiveApi.check(die);
      if (!c.archivable) { setErr(t('overview.archive.shared', { die })); return; }
      if (c.refusals.length) {
        setErr(t('overview.archive.refused', { die, why: c.refusals.map((r) => t(`overview.archive.refusal.${r.code}`, { names: r.names.join(', ') })).join('; ') }));
        return;
      }
      setAskConfirm({ title: t('overview.archive.title', { die }), body: t('overview.archive.body', { die }), confirmLabel: t('overview.archive.confirm'),
        onConfirm: () => { dieArchiveApi.archive(die).then(changed).catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e))); } });
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };
  const restore = (d: ArchivedDie) => { setErr(''); dieArchiveApi.restore(d).then(changed).catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e))); };
  const archiveButton = (die: string) => (
    <Tooltip title={t('overview.archive.tip')}>
      <IconButton size="small" aria-label={t('overview.archive.tip')} data-testid="overview-archive-die" onClick={() => void startArchive(die)}
        sx={{ ml: 0.5, p: 0.25, color: 'var(--text-4)' }}><ArchiveOutlinedIcon sx={{ fontSize: 15 }} /></IconButton>
    </Tooltip>
  );
  const archived = answer?.archived_dies ?? [];

  // the ONE derived stage (routes/overview.motor_stage: the project is as far as its most advanced motor), else its status
  const projectLabel = (p: OverviewProject) => (p.stage && (DERIVED_STAGES.includes(p.stage) || p.stage === 'computed')
    ? t(`overview.stageName.${p.stage}`) : t(`development:status.${p.stage || shownStatus(p.status, p.spec_submitted ?? undefined)}`));

  const projectChip = (p: OverviewProject & { link_status?: string }) => (
    <Tooltip key={p.id} title={[p.customer_org, projectLabel(p), p.link_status === 'trial' ? t('overview.trial') : '', t(`displayState.${p.display.state}`)].filter(Boolean).join(' · ')}>
      <Chip size="small" variant="outlined" component="a" clickable href={appLinkPath({ kind: 'project', id: p.id }) ?? undefined}
        label={<>{p.title}<Box component="span" sx={{ ml: 0.5, color: 'var(--text-3)' }}>{projectLabel(p)}</Box></>}
        sx={{ m: 0.25, height: 20, fontSize: 11, ...(p.link_status === 'trial' ? { borderStyle: 'dashed' } : {}), ...STATE_SX[p.display.state] }} />
    </Tooltip>
  );
  const productionChip = (p: OverviewProduction) => (
    <Tooltip key={`${p.die}/${p.config}`} title={[p.title, t(`displayState.${p.display.state}`)].filter(Boolean).join(' · ')}>
      <Chip size="small" variant="outlined" component="a" clickable href={appLinkPath({ kind: 'production', die: p.die, config: p.config }) ?? undefined}
        label={`${t(`production:status.${p.status}`)} · ${t('overview.rev', { rev: p.current_revision })} · ${(p.order_quantity > 0 ? t('overview.qty', { n: p.order_quantity }) : t('overview.qtyNotSet'))}`}
        sx={{ m: 0.25, height: 20, fontSize: 11, ...STATE_SX[p.display.state] }} />
    </Tooltip>
  );

  const familyTable = (families: Family[], kind: 'motors' | 'controllers') => {
    // "works with" only when some row has data: a column of dashes says nothing (finding 2026-11-17)
    const withWorks = families.some((f) => f.rows.some((r) => r.item.works_with.length || r.item.analysis_controllers.length));
    return (
    <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, overflow: 'auto', mb: 2 }}>
      <Table size="small" sx={tableSx} data-testid={`overview-${kind}`}>
        <TableHead><TableRow>
          <TableCell>{t(kind === 'motors' ? 'overview.motor' : 'overview.board')}</TableCell>
          <TableCell>{t('overview.project')}</TableCell><TableCell>{t('overview.production')}</TableCell>
          {withWorks && <TableCell>{t('overview.worksWith')}</TableCell>}
        </TableRow></TableHead>
        <TableBody>
          {families.map((f) => (
            <React.Fragment key={f.die}>
              <TableRow sx={{ bgcolor: 'var(--panel)' }}>
                <TableCell colSpan={withWorks ? 4 : 3} sx={{ fontWeight: 800, color: 'var(--text-0)' }}>
                  {f.die}
                  {headerChips(f).od && <Box component="span" sx={{ ml: 1, color: 'var(--text-3)', fontWeight: 600 }}>Ø{f.od}</Box>}
                  {headerChips(f).sp && <Box component="span" sx={{ ml: 1, color: 'var(--text-3)', fontWeight: 600 }}>{f.slots}s{f.poles}p</Box>}
                  {!f.inCatalog && kind === 'motors' && <Tooltip title={t('overview.notInCatalogTip')}><Chip size="small" label={t('overview.notInCatalog')} sx={{ ml: 1, height: 18, fontSize: 10 }} /></Tooltip>}
                  {f.inCatalog && kind === 'motors' && archiveButton(f.die)}
                </TableCell>
              </TableRow>
              {f.rows.map((r) => (
                <TableRow key={r.key} hover sx={r.muted ? { opacity: 0.55 } : undefined}>
                  <TableCell sx={{ pl: 3, whiteSpace: 'nowrap' }}>
                    {r.item.config}
                    {r.item.state === 'removed' && <Chip size="small" label={t('overview.removed')} sx={{ ml: 1, height: 18, fontSize: 10 }} />}
                  </TableCell>
                  <TableCell>{r.projects.length ? r.projects.map(projectChip) : DASH}</TableCell>
                  <TableCell>{r.productions.length ? r.productions.map(productionChip) : DASH}</TableCell>
                  {withWorks && <TableCell>
                    {r.item.works_with.length ? r.item.works_with.map((w) => <Chip key={w} size="small" label={w} sx={{ m: 0.25, height: 20, fontSize: 11 }} />) : null}
                    {r.item.analysis_controllers.map((c) => (
                      <Tooltip key={c} title={t('overview.analysisControllerTip')}><Chip size="small" variant="outlined" label={c} sx={{ m: 0.25, height: 20, fontSize: 11, borderStyle: 'dashed' }} /></Tooltip>
                    ))}
                    {!r.item.works_with.length && !r.item.analysis_controllers.length && DASH}
                  </TableCell>}
                </TableRow>
              ))}
            </React.Fragment>
          ))}
        </TableBody>
      </Table>
    </Paper>
    );
  };

  const empty = !!answer && !Object.keys(answer.items).length && !Object.keys(answer.projects).length;
  return (
    <Box sx={{ p: 3, overflowY: 'auto', height: '100%' }} data-testid="overview-page">
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 17, fontWeight: 800, color: 'var(--text-0)' }}>{t('overview.title')}</Typography>
        <HelpTip title={t('overview.titleTip')} />
        <ToggleButtonGroup size="small" exclusive value={view} onChange={(_e, v: View | null) => { if (v) setView(v); }}
          sx={{ '& .MuiToggleButton-root': { py: 0.2, px: 1, fontSize: 11.5, textTransform: 'none' } }}>
          <ToggleButton value="tree">{t('overview.byMotor')}</ToggleButton>
          <ToggleButton value="projects">{t('overview.projects')}</ToggleButton>
          <ToggleButton value="decisions" data-testid="overview-decisions-tab">
            <Tooltip title={t(oq?.open_urgent ? 'ownerQueue.countTipUrgent' : 'ownerQueue.countTip', { urgent: oq?.open_urgent ?? 0, blocking: oq?.open_blocking ?? 0, messages: oq?.open_messages ?? 0 })}>
              <Badge color="warning" badgeContent={badgeTotal(oq)} sx={{ '& .MuiBadge-badge': { right: -10, top: 2 } }}><span>{t('ownerQueue.tab')}</span></Badge>
            </Tooltip>
          </ToggleButton>
        </ToggleButtonGroup>
        {model && view !== 'decisions' && <DisplayStateFilter value={filter} onChange={setFilter}
          counts={view === 'tree' ? { counts: model.counts, woke: { active: 0, parked: 0, archived: 0, all: 0 } } : projectCounts}
          tip={t(view === 'tree' ? 'overview.countsRowsTip' : 'overview.countsProjectsTip')} />}
        {answer && !answer.registry && <Tooltip title={t('overview.noRegistryTip')}><Chip size="small" label={t('overview.noRegistry')} sx={{ height: 20, fontSize: 11 }} /></Tooltip>}
        {answer && answer.review.length > 0 && (
          <>
            <Chip size="small" color="warning" clickable onClick={(e) => setReviewAnchor(e.currentTarget)} label={t('overview.review', { n: answer.review.length })} sx={{ height: 20, fontSize: 11 }} />
            <Popover open={!!reviewAnchor} anchorEl={reviewAnchor} onClose={() => setReviewAnchor(null)} anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}>
              <Box sx={{ p: 1.5, maxWidth: 520 }} data-testid="overview-review">
                {answer.review.map((r, i) => (
                  <Box key={i} sx={{ mb: 1 }}>
                    <Typography sx={{ fontSize: 12.5, fontWeight: 700 }}>{t(`overview.reviewCode.${String(r.code)}.what`, { where: String(r.where ?? ''), candidates: Array.isArray(r.candidates) ? r.candidates.join(', ') : '' })}</Typography>
                    <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{t(`overview.reviewCode.${String(r.code)}.do`)}</Typography>
                  </Box>
                ))}
              </Box>
            </Popover>
          </>
        )}
        <Box sx={{ flex: 1 }} />
        <Button size="small" startIcon={<RefreshIcon sx={{ fontSize: 16 }} />} disabled={loading} onClick={() => void load()}
          sx={{ color: 'var(--text-2)', textTransform: 'none', fontSize: 12 }}>{t('overview.refresh')}</Button>
      </Box>
      {loading && !answer && <Box sx={{ display: 'flex', gap: 1.5, color: 'var(--text-3)', py: 3 }}><CircularProgress size={18} /></Box>}
      {err && <Typography sx={{ fontSize: 12, color: '#f87171' }}>{err}</Typography>}
      {empty && view !== 'decisions' && <Typography sx={{ fontSize: 13, color: 'var(--text-3)' }}>{t('overview.empty')}</Typography>}
      {view === 'decisions' && <DecisionsView projects={answer?.projects ?? {}} onChanged={loadCount} />}
      {model && !empty && view === 'tree' && (
        <>
          {model.motors.length > 0 && familyTable(model.motors, 'motors')}
          {model.controllers.length > 0 && familyTable(model.controllers, 'controllers')}
          {model.noItemYet.length > 0 && (
            <Box sx={{ mb: 2 }}>
              <Typography sx={{ fontSize: 12, fontWeight: 700, color: 'var(--text-2)', mb: 0.5 }}>{t('overview.noItemYet')}</Typography>
              {model.noItemYet.map((p) => projectChip(p))}
            </Box>
          )}
          {(filter === 'archived' || filter === 'all') && <ArchivedDies dies={archived} onRestore={restore} />}
          {model.catalogOnly.length > 0 && filter !== 'parked' && filter !== 'archived' && (
            <Accordion disableGutters sx={{ bgcolor: 'var(--panel-2)' }}>
              <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                <Typography sx={{ fontSize: 12, fontWeight: 700, color: 'var(--text-2)' }}>{t('overview.catalogOnly', { n: model.catalogOnly.length })}</Typography>
              </AccordionSummary>
              <AccordionDetails>
                {model.catalogOnly.map((d) => <Chip key={d.name} size="small" variant="outlined" label={`${d.name} · ${d.configs?.length ?? 0}`}
                  onDelete={() => void startArchive(d.name)} deleteIcon={<Tooltip title={t('overview.archive.tip')}><ArchiveOutlinedIcon aria-label={t('overview.archive.tip')} /></Tooltip>}
                  sx={{ m: 0.25, height: 20, fontSize: 11 }} />)}
              </AccordionDetails>
            </Accordion>
          )}
        </>
      )}
      {answer && !empty && view === 'projects' && (
        <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, overflow: 'auto' }}>
          <Table size="small" sx={tableSx} data-testid="overview-projects">
            <TableHead><TableRow>
              <TableCell>{t('overview.project')}</TableCell><TableCell>{t('overview.customer')}</TableCell>
              <TableCell>{t('overview.stage')}</TableCell><TableCell>{t('overview.motor')}</TableCell>
              <TableCell>{t('overview.production')}</TableCell><TableCell align="right">{t('overview.updated')}</TableCell>
              <TableCell>{t('overview.shown')}</TableCell>
            </TableRow></TableHead>
            <TableBody>
              {projectsFlat.map((p) => (
                <TableRow key={p.id} hover>
                  <TableCell sx={{ fontWeight: 600 }}><Link href={appLinkPath({ kind: 'project', id: p.id }) ?? undefined} underline="hover" color="inherit">{p.title}</Link><PriorityMark priority={prio[p.id]?.priority} /></TableCell>
                  <TableCell sx={{ color: 'var(--text-2)' }}>{p.customer_org}</TableCell>
                  <TableCell>{projectLabel(p)}</TableCell>
                  <TableCell>{p.motors.length ? p.motors.map((m) => <Chip key={m} size="small" label={m} sx={{ m: 0.25, height: 20, fontSize: 11 }} />) : DASH}</TableCell>
                  <TableCell>{(() => {
                    const prods = p.motors.flatMap((m) => answer.items[m]?.productions ?? []).map((k) => answer.productions[k]).filter(Boolean) as OverviewProduction[];
                    return prods.length ? prods.map(productionChip) : DASH;
                  })()}</TableCell>
                  <TableCell align="right" sx={{ color: 'var(--text-3)', whiteSpace: 'nowrap' }}>{(p.updated_at || '').slice(0, 10)}</TableCell>
                  <TableCell><DisplayStateControl rec={p.display} onChange={(next, reason) => setProjectState(p, next, reason)} /></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      )}
      <ConfirmDialog state={askConfirm} onClose={() => setAskConfirm(null)} />
    </Box>
  );
};

export default OverviewPage;
