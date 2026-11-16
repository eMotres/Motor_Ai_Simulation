/** The owner's Overview (owner 2026-11-16; admins only): every motor and board that a project uses or a production makes, under
 *  its die / platform, with the Project and the Production columns kept apart; a flat Projects tab beside it.  Reads
 *  `/api/overview` (projects, productions, registry ids, display states) and the catalog tree; writes only the display state. */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Accordion, AccordionDetails, AccordionSummary, Box, Button, Chip, CircularProgress, Link, Paper, Table, TableBody, TableCell,
  TableHead, TableRow, ToggleButton, ToggleButtonGroup, Tooltip, Typography } from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import RefreshIcon from '@mui/icons-material/Refresh';
import { useTranslation } from 'react-i18next';
import HelpTip from '../../components/common/HelpTip';
import { DisplayStateControl, DisplayStateFilter } from '../../components/common/DisplayStateControl';
import { DISPLAY_FILTERS, filterCounts, type DisplayFilter, type DisplayState } from '../../lib/displayState';
import { displayStateApi } from '../../lib/displayStateApi';
import { useRememberedView } from '../../lib/useRememberedView';
import { oneOf } from '../../lib/viewMemory';
import { appLinkPath } from '../../lib/appLink';
import { fetchFamilyTree } from '../../lib/familyTree';
import { NARROW } from '../../lib/narrowLayout';
import { shownStatus } from '../development/projectStage';
import { buildView, flatProjects, type Family, type OverviewAnswer, type OverviewProduction, type OverviewProject, type TreeDie } from './overviewModel';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const VIEWS = ['tree', 'projects'] as const;
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

  const load = useCallback(async () => {
    setLoading(true); setErr('');
    try {
      const r = await fetch(`${API}/api/overview`, { cache: 'no-store' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setAnswer(await r.json() as OverviewAnswer);
      fetchFamilyTree().then((tr) => setTree((tr.dies ?? []) as TreeDie[])).catch(() => setTree([]));
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
  const projectLabel = (p: OverviewProject) => t(`development:status.${shownStatus(p.status, p.spec_submitted ?? undefined)}`);

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
        label={`${t(`production:status.${p.status}`)} · ${t('overview.rev', { rev: p.current_revision })} · ${t('overview.qty', { n: p.order_quantity })}`}
        sx={{ m: 0.25, height: 20, fontSize: 11, ...STATE_SX[p.display.state] }} />
    </Tooltip>
  );

  const familyTable = (families: Family[], kind: 'motors' | 'controllers') => (
    <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, overflow: 'auto', mb: 2 }}>
      <Table size="small" sx={tableSx} data-testid={`overview-${kind}`}>
        <TableHead><TableRow>
          <TableCell>{t(kind === 'motors' ? 'overview.motor' : 'overview.board')}</TableCell>
          <TableCell>{t('overview.project')}</TableCell><TableCell>{t('overview.production')}</TableCell>
          <TableCell>{t('overview.worksWith')}</TableCell>
        </TableRow></TableHead>
        <TableBody>
          {families.map((f) => (
            <React.Fragment key={f.die}>
              <TableRow sx={{ bgcolor: 'var(--panel)' }}>
                <TableCell colSpan={4} sx={{ fontWeight: 800, color: 'var(--text-0)' }}>
                  {f.die}
                  {f.od !== null && <Box component="span" sx={{ ml: 1, color: 'var(--text-3)', fontWeight: 600 }}>Ø{f.od}</Box>}
                  {f.slots !== null && f.poles !== null && <Box component="span" sx={{ ml: 1, color: 'var(--text-3)', fontWeight: 600 }}>{f.slots}s{f.poles}p</Box>}
                  {!f.inCatalog && kind === 'motors' && <Tooltip title={t('overview.notInCatalogTip')}><Chip size="small" label={t('overview.notInCatalog')} sx={{ ml: 1, height: 18, fontSize: 10 }} /></Tooltip>}
                </TableCell>
              </TableRow>
              {f.rows.map((r) => (
                <TableRow key={r.key} hover>
                  <TableCell sx={{ pl: 3, whiteSpace: 'nowrap' }}>
                    {r.item.config}
                    {r.item.state === 'removed' && <Chip size="small" label={t('overview.removed')} sx={{ ml: 1, height: 18, fontSize: 10 }} />}
                  </TableCell>
                  <TableCell>{r.projects.length ? r.projects.map(projectChip) : DASH}</TableCell>
                  <TableCell>{r.productions.length ? r.productions.map(productionChip) : DASH}</TableCell>
                  <TableCell>
                    {r.item.works_with.length ? r.item.works_with.map((w) => <Chip key={w} size="small" label={w} sx={{ m: 0.25, height: 20, fontSize: 11 }} />) : null}
                    {r.item.analysis_controllers.map((c) => (
                      <Tooltip key={c} title={t('overview.analysisControllerTip')}><Chip size="small" variant="outlined" label={c} sx={{ m: 0.25, height: 20, fontSize: 11, borderStyle: 'dashed' }} /></Tooltip>
                    ))}
                    {!r.item.works_with.length && !r.item.analysis_controllers.length && DASH}
                  </TableCell>
                </TableRow>
              ))}
            </React.Fragment>
          ))}
        </TableBody>
      </Table>
    </Paper>
  );

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
        </ToggleButtonGroup>
        {model && <DisplayStateFilter value={filter} onChange={setFilter}
          counts={view === 'tree' ? { counts: model.counts, woke: { active: 0, parked: 0, archived: 0, all: 0 } } : projectCounts} />}
        {answer && !answer.registry && <Tooltip title={t('overview.noRegistryTip')}><Chip size="small" label={t('overview.noRegistry')} sx={{ height: 20, fontSize: 11 }} /></Tooltip>}
        {answer && answer.review.length > 0 && (
          <Tooltip title={answer.review.map((r) => `${String(r.code)}: ${String(r.where ?? '')}`).join('\n')}>
            <Chip size="small" color="warning" label={t('overview.review', { n: answer.review.length })} sx={{ height: 20, fontSize: 11 }} />
          </Tooltip>
        )}
        <Box sx={{ flex: 1 }} />
        <Button size="small" startIcon={<RefreshIcon sx={{ fontSize: 16 }} />} disabled={loading} onClick={() => void load()}
          sx={{ color: 'var(--text-2)', textTransform: 'none', fontSize: 12 }}>{t('overview.refresh')}</Button>
      </Box>
      {loading && !answer && <Box sx={{ display: 'flex', gap: 1.5, color: 'var(--text-3)', py: 3 }}><CircularProgress size={18} /></Box>}
      {err && <Typography sx={{ fontSize: 12, color: '#f87171' }}>{err}</Typography>}
      {empty && <Typography sx={{ fontSize: 13, color: 'var(--text-3)' }}>{t('overview.empty')}</Typography>}
      {model && !empty && view === 'tree' && (
        <>
          {model.motors.length > 0 && familyTable(model.motors, 'motors')}
          {model.controllers.length > 0 && familyTable(model.controllers, 'controllers')}
          {model.noMotorYet.length > 0 && (
            <Box sx={{ mb: 2 }}>
              <Typography sx={{ fontSize: 12, fontWeight: 700, color: 'var(--text-2)', mb: 0.5 }}>{t('overview.noMotorYet')}</Typography>
              {model.noMotorYet.map((p) => projectChip(p))}
            </Box>
          )}
          {model.catalogOnly.length > 0 && filter !== 'parked' && filter !== 'archived' && (
            <Accordion disableGutters sx={{ bgcolor: 'var(--panel-2)' }}>
              <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                <Typography sx={{ fontSize: 12, fontWeight: 700, color: 'var(--text-2)' }}>{t('overview.catalogOnly', { n: model.catalogOnly.length })}</Typography>
              </AccordionSummary>
              <AccordionDetails>
                {model.catalogOnly.map((d) => <Chip key={d.name} size="small" variant="outlined" label={`${d.name} · ${d.configs?.length ?? 0}`} sx={{ m: 0.25, height: 20, fontSize: 11 }} />)}
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
                  <TableCell sx={{ fontWeight: 600 }}><Link href={appLinkPath({ kind: 'project', id: p.id }) ?? undefined} underline="hover" color="inherit">{p.title}</Link></TableCell>
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
    </Box>
  );
};

export default OverviewPage;
