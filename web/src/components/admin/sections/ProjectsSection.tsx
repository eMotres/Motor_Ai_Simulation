/** Admin view for Development projects and their access links. */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Autocomplete, Box, Button, Chip, CircularProgress, Divider, Drawer, IconButton, MenuItem,
  Paper, Table, TableBody, TableCell, TableHead, TableRow, TextField, Tooltip, Typography,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import CloseIcon from '@mui/icons-material/Close';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import { useTranslation } from 'react-i18next';
import MotorsDialog, { type RegistryUser } from '../dialogs/MotorsDialog';
import type { ProjectMember, ProjectStatus } from '../../../modules/development/types';
import { useUIStore } from '../../../stores/uiStore';
import { adminProjectPaths, projectAdminMembers, projectCompletenessLabel, projectMotorKey, projectOpenEvent, projectOpenIntent, projectRoleBody, validProjectId } from '../adminProjectsModel';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const ROLES: ProjectMember['role'][] = ['customer', 'engineer', 'owner'];
const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;
const CHIP = { height: 18, fontSize: 10.5, fontWeight: 700 } as const;

interface ProjectRow {
  id: string; title: string; customer_org?: string; status: ProjectStatus;
  completeness?: { filled_required?: number; total_required?: number; filled?: number; required?: number };
  members: ProjectMember[]; linked_motors: Array<{ die: string; config: string }>;
  pending_invites?: ProjectMember[];
  last_activity?: string | null;
  grant_removal_candidates?: Array<{ email: string; die: string; config: string; reason?: string }>;
}
interface MotorLite { die: string; config: string }
const msg = (j: any, status: number) => typeof j?.detail === 'string' ? j.detail : j?.detail?.message ?? `HTTP ${status}`;
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API}${path}`, init);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(msg(j, r.status));
  return j as T;
}
const enc = encodeURIComponent;
const dateLabel = (value?: string | null) => value ? new Date(value).toLocaleString() : '—';

const ProjectsSection: React.FC = () => {
  const { t } = useTranslation('admin');
  const { t: td } = useTranslation('development');
  const setActiveTab = useUIStore((s) => s.setActiveTab);
  const [rows, setRows] = useState<ProjectRow[]>([]);
  const [users, setUsers] = useState<RegistryUser[]>([]);
  const [motors, setMotors] = useState<MotorLite[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [createId, setCreateId] = useState('');
  const [createTitle, setCreateTitle] = useState('');
  const [createOrg, setCreateOrg] = useState('');
  const [candidateUser, setCandidateUser] = useState<RegistryUser | null>(null);
  const [creating, setCreating] = useState(false);
  const loadEpoch = useRef(0);

  const load = useCallback(async () => {
    const epoch = ++loadEpoch.current;
    setLoading(true); setError(null);
    try {
      const [p, u, m] = await Promise.all([
        request<{ projects: ProjectRow[] }>('/api/admin/projects'),
        request<{ users: RegistryUser[] }>('/api/auth/users').catch(() => ({ users: [] })),
        request<{ dies?: Array<{ name: string; config_names?: string[] }> }>('/api/admin/dies').catch(() => ({ dies: [] })),
      ]);
      if (epoch !== loadEpoch.current) return;
      setRows(p.projects ?? []); setUsers(u.users ?? []);
      setMotors((m.dies ?? []).flatMap((d) => (d.config_names ?? []).map((config) => ({ die: d.name, config }))));
    } catch (e) { if (epoch === loadEpoch.current) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (epoch === loadEpoch.current) setLoading(false); }
  }, []);
  useEffect(() => { void load(); return () => { loadEpoch.current += 1; }; }, [load]);
  const current = rows.find((r) => r.id === selectedId) ?? null;
  const [search, setSearch] = useState('');
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return rows.filter((r) => !q || `${r.id} ${r.title} ${r.customer_org ?? ''}`.toLowerCase().includes(q));
  }, [rows, search]);

  const changed = (text: string) => { setNotice(`✓ ${text}`); void load(); };
  const create = async () => {
    if (creating) return;
    setCreating(true); setError(null);
    try {
      await request('/api/admin/projects', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: createId.trim().toLowerCase(), title: createTitle.trim(), customer_org: createOrg.trim() }) });
      setCreateOpen(false); setCreateId(''); setCreateTitle(''); setCreateOrg(''); changed(t('projects.created'));
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setCreating(false); }
  };

  return <Box>
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5, flexWrap: 'wrap' }}>
      <Typography sx={{ fontSize: 16, fontWeight: 800, color: 'var(--text-0)' }}>{t('projects.title')}</Typography>
      {notice && <Typography sx={{ fontSize: 11, color: notice.startsWith('✓') ? '#34d399' : '#f87171' }}>{notice}</Typography>}
      <Box sx={{ flex: 1 }} />
      <TextField size="small" placeholder={t('projects.search')} value={search} onChange={(e) => setSearch(e.target.value)} />
      <Button size="small" startIcon={<RefreshIcon />} onClick={() => void load()} disabled={loading} sx={{ textTransform: 'none' }}>{t('projects.refresh')}</Button>
      <Button size="small" variant="contained" onClick={() => setCreateOpen(true)} sx={{ textTransform: 'none' }}>{t('projects.create')}</Button>
    </Box>
    {loading && <Box sx={{ display: 'flex', gap: 1, py: 3, color: 'var(--text-3)' }}><CircularProgress size={18} />{t('projects.loading')}</Box>}
    {error && <Typography sx={{ color: '#f87171', fontSize: 12, mb: 1 }}>{error}</Typography>}
    {!loading && !error && <Paper sx={{ ...PANEL, overflowX: 'auto' }}><Table size="small" sx={{ '& td, & th': { borderColor: 'var(--panel)', fontSize: 12 }, '& th': { color: 'var(--text-3)', fontSize: 10, textTransform: 'uppercase' } }}>
      <TableHead><TableRow><TableCell>{t('projects.project')}</TableCell><TableCell>{t('projects.status')}</TableCell><TableCell>{t('projects.completeness')}</TableCell><TableCell>{t('projects.members')}</TableCell><TableCell>{t('projects.motors')}</TableCell><TableCell>{t('projects.activity')}</TableCell></TableRow></TableHead>
      <TableBody>{filtered.map((p) => {
        return <TableRow key={p.id} hover onClick={() => setSelectedId(p.id)} sx={{ cursor: 'pointer' }}>
          <TableCell><Typography sx={{ fontSize: 12.5, fontWeight: 700, color: 'var(--text-0)' }}>{p.title}</Typography><Typography sx={{ fontSize: 10.5, color: 'var(--text-4)' }}>{p.id}{p.customer_org ? ` · ${p.customer_org}` : ''}</Typography></TableCell>
          <TableCell><Chip size="small" label={td(`status.${p.status}`, { defaultValue: p.status })} sx={{ ...CHIP, color: '#60a5fa', bgcolor: '#2563eb1f' }} /></TableCell>
          <TableCell>{projectCompletenessLabel(p.completeness)}</TableCell>
          <TableCell>{projectAdminMembers(p.members ?? [], p.pending_invites ?? []).length}</TableCell><TableCell>{p.linked_motors?.length ?? 0}</TableCell><TableCell>{dateLabel(p.last_activity)}</TableCell>
        </TableRow>;
      })}{filtered.length === 0 && <TableRow><TableCell colSpan={6} sx={{ textAlign: 'center', color: 'var(--text-4)', py: 3 }}>{t('projects.empty')}</TableCell></TableRow>}</TableBody>
    </Table></Paper>}
    <Drawer anchor="right" open={!!current} onClose={() => setSelectedId(null)} PaperProps={{ sx: { width: { xs: '100%', sm: 430 }, bgcolor: 'var(--panel)', p: 2.5, overflowY: 'auto' } }}>
      {current && <ProjectDrawer project={current} users={users} motors={motors} onClose={() => setSelectedId(null)} onChanged={changed} onManageGrants={setCandidateUser} onOpenProject={() => { const intent = projectOpenIntent(current.id); try { sessionStorage.setItem(intent.storageKey, intent.projectId); } catch { /* Event path still opens while the page is mounted. */ } setSelectedId(null); setActiveTab('development' as never); window.dispatchEvent(new CustomEvent(projectOpenEvent, { detail: { projectId: intent.projectId } })); }} />}
    </Drawer>
    <CreateProject open={createOpen} id={createId} title={createTitle} org={createOrg} error={error} busy={creating} onId={setCreateId} onTitle={setCreateTitle} onOrg={setCreateOrg} onClose={() => setCreateOpen(false)} onCreate={() => void create()} />
    <MotorsDialog user={candidateUser} onClose={() => setCandidateUser(null)} onSaved={() => { setCandidateUser(null); changed(t('projects.grantsUpdated')); }} />
  </Box>;
};

const CreateProject: React.FC<{ open: boolean; id: string; title: string; org: string; error: string | null; busy: boolean; onId: (v: string) => void; onTitle: (v: string) => void; onOrg: (v: string) => void; onClose: () => void; onCreate: () => void }> = ({ open, id, title, org, error, busy, onId, onTitle, onOrg, onClose, onCreate }) => {
  const { t } = useTranslation('admin');
  return <Drawer anchor="right" open={open} onClose={onClose} PaperProps={{ sx: { width: 360, bgcolor: 'var(--panel)', p: 2.5 } }}><Typography sx={{ fontSize: 15, fontWeight: 800, mb: 2 }}>{t('projects.create')}</Typography>
    <TextField size="small" fullWidth label={t('projects.id')} value={id} onChange={(e) => onId(e.target.value)} sx={{ mb: 1 }} />
    <TextField size="small" fullWidth label={t('projects.name')} value={title} onChange={(e) => onTitle(e.target.value)} sx={{ mb: 1 }} />
    <TextField size="small" fullWidth label={t('projects.org')} value={org} onChange={(e) => onOrg(e.target.value)} sx={{ mb: 1.5 }} />
    {error && <Typography sx={{ color: '#f87171', fontSize: 11, mb: 1 }}>{error}</Typography>}
    <Box sx={{ display: 'flex', gap: 1 }}><Button onClick={onClose} sx={{ textTransform: 'none' }}>{t('projects.cancel')}</Button><Button variant="contained" disabled={busy || !validProjectId(id) || !title.trim()} onClick={onCreate} sx={{ textTransform: 'none' }}>{t('projects.create')}</Button></Box>
  </Drawer>;
};

const ProjectDrawer: React.FC<{ project: ProjectRow; users: RegistryUser[]; motors: MotorLite[]; onClose: () => void; onChanged: (s: string) => void; onManageGrants: (u: RegistryUser | null) => void; onOpenProject: () => void }> = ({ project, users, motors, onClose, onChanged, onManageGrants, onOpenProject }) => {
  const { t } = useTranslation('admin');
  const { t: td } = useTranslation('development');
  const [email, setEmail] = useState<string | null>(null); const [role, setRole] = useState<ProjectMember['role']>('customer');
  const [motorKey, setMotorKey] = useState<MotorLite | null>(null); const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null);
  const act = async (path: string, method: string, body?: unknown, ok = t('projects.saved')) => {
    setBusy(true); setError(null);
    try { await request(path, { method, ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) }); onChanged(ok); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const visibleMembers = projectAdminMembers(project.members ?? [], project.pending_invites ?? []);
  const memberEmails = new Set(visibleMembers.map((m) => m.email.toLowerCase()));
  const linked = new Set((project.linked_motors ?? []).map((m) => projectMotorKey(m.die, m.config)));
  const candidateUserFor = (emailValue: string) => users.find((u) => u.email.toLowerCase() === emailValue.toLowerCase()) ?? null;
  return <>
    <Box sx={{ display: 'flex', gap: 1, alignItems: 'start' }}><Box sx={{ flex: 1 }}><Typography sx={{ fontSize: 15, fontWeight: 800, color: 'var(--text-0)' }}>{project.title}</Typography><Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>{project.id}</Typography></Box><IconButton size="small" onClick={onClose}><CloseIcon fontSize="small" /></IconButton></Box>
    <Button size="small" startIcon={<OpenInNewIcon />} onClick={onOpenProject} sx={{ textTransform: 'none', mt: 1 }}>{t('projects.open')}</Button>
    <Divider sx={{ my: 1.5, borderColor: 'var(--line-soft)' }} />
    <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-3)', textTransform: 'uppercase', mb: 0.5 }}>{t('projects.members')}</Typography>
    {visibleMembers.map((m) => <Box key={m.email} sx={{ display: 'flex', alignItems: 'center', gap: 0.75, py: 0.25 }}><Typography sx={{ flex: 1, fontSize: 12, color: 'var(--text-1)' }}>{m.email}{m.pending ? ` · ${t('projects.pending')}` : ''}</Typography><TextField select size="small" value={m.role} onChange={(e) => void act(adminProjectPaths.members(project.id), 'POST', projectRoleBody(m.email, e.target.value as ProjectMember['role']), t('projects.roleUpdated'))} aria-label={t('projects.roleSelect', { email: m.email })} sx={{ width: 115 }} disabled={busy}>{ROLES.map((r) => <MenuItem key={r} value={r}>{t(`projects.role.${r}`)}</MenuItem>)}</TextField><IconButton size="small" disabled={busy} aria-label={t('projects.removeMember')} onClick={() => void act(adminProjectPaths.member(project.id, m.email), 'DELETE', undefined, t('projects.memberRemoved'))}><CloseIcon sx={{ fontSize: 14 }} /></IconButton></Box>)}
    <Box sx={{ display: 'flex', gap: 0.75, mt: 1 }}><Autocomplete size="small" options={users.map((u) => u.email).filter((e) => !memberEmails.has(e.toLowerCase()))} value={email} onChange={(_, v) => setEmail(v)} sx={{ flex: 1 }} renderInput={(params) => <TextField {...params} label={t('projects.account')} />} /><TextField select size="small" value={role} onChange={(e) => setRole(e.target.value as ProjectMember['role'])} sx={{ width: 112 }} aria-label={t('projects.roleLabel')}>
      {ROLES.map((r) => <MenuItem key={r} value={r}>{t(`projects.role.${r}`)}</MenuItem>)}</TextField><Button size="small" disabled={busy || !email} onClick={() => { if (email) void act(`/api/admin/projects/${enc(project.id)}/members`, 'POST', { email, role }, t('projects.memberAdded')); }} sx={{ textTransform: 'none' }}>{t('projects.add')}</Button></Box>
    <Divider sx={{ my: 1.5, borderColor: 'var(--line-soft)' }} />
    <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-3)', textTransform: 'uppercase', mb: 0.5 }}>{t('projects.motors')}</Typography>
    {(project.linked_motors ?? []).map((m) => <Box key={projectMotorKey(m.die, m.config)} sx={{ display: 'flex', alignItems: 'center', py: 0.2 }}><Typography sx={{ flex: 1, fontSize: 12 }}>{m.die} / {m.config}</Typography><IconButton size="small" disabled={busy} aria-label={t('projects.unlinkMotor')} onClick={() => void act(adminProjectPaths.motor(project.id, m.die, m.config), 'DELETE', undefined, t('projects.motorUnlinked'))}><CloseIcon sx={{ fontSize: 14 }} /></IconButton></Box>)}
    <Box sx={{ display: 'flex', gap: 0.75, mt: 1 }}><Autocomplete size="small" options={motors.filter((m) => !linked.has(projectMotorKey(m.die, m.config)))} getOptionLabel={(m) => `${m.die} / ${m.config}`} value={motorKey} onChange={(_, v) => setMotorKey(v)} sx={{ flex: 1 }} renderInput={(params) => <TextField {...params} label={t('projects.motor')} />} /><Button size="small" disabled={busy || !motorKey} onClick={() => { if (motorKey) void act(adminProjectPaths.motors(project.id), 'POST', motorKey, t('projects.motorLinked')); }} sx={{ textTransform: 'none' }}>{t('projects.link')}</Button></Box>
    <Divider sx={{ my: 1.5, borderColor: 'var(--line-soft)' }} />
    <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-3)', textTransform: 'uppercase', mb: 0.5 }}>{t('projects.grantCandidates')}</Typography>
    <Tooltip title={td('grantReviewTip')}><Typography sx={{ fontSize: 10.5, color: 'var(--text-4)', mb: 0.5 }}>{td('grantReviewCount', { count: project.grant_removal_candidates?.length ?? 0 })}</Typography></Tooltip>
    {(project.grant_removal_candidates ?? []).length === 0 && <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>{t('projects.noCandidates')}</Typography>}
    {(project.grant_removal_candidates ?? []).map((c, i) => <Box key={`${c.email}/${c.die}/${c.config}/${i}`} sx={{ display: 'flex', alignItems: 'center', gap: 0.5, py: 0.25 }}><Tooltip title={c.reason ? td(`grantReason.${c.reason}`, { defaultValue: c.reason }) : ''}><Typography sx={{ flex: 1, fontSize: 11, color: 'var(--text-2)' }}>{c.email} · {c.die}/{c.config}</Typography></Tooltip><Button size="small" disabled={!candidateUserFor(c.email)} onClick={() => onManageGrants(candidateUserFor(c.email))} sx={{ textTransform: 'none', fontSize: 10 }}>{t('projects.manageMotors')}</Button></Box>)}
    {error && <Typography sx={{ color: '#f87171', fontSize: 11, mt: 1 }}>{error}</Typography>}
  </>;
};

export default ProjectsSection;
