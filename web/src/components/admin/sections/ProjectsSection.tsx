/** Admin view for Development projects and their access links. */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Autocomplete, Box, Button, Chip, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, Divider, Drawer, IconButton, MenuItem,
  Paper, Table, TableBody, TableCell, TableHead, TableRow, TextField, Tooltip, Typography,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import CloseIcon from '@mui/icons-material/Close';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import { useTranslation } from 'react-i18next';
import MotorsDialog, { type RegistryUser } from '../dialogs/MotorsDialog';
import type { ProjectMember, ProjectMotor, ProjectMotorPickerItem, ProjectStatus } from '../../../modules/development/types';
import { useUIStore } from '../../../stores/uiStore';
import { adminProjectPaths, projectAdminMembers, projectCompletenessLabel, projectOwnerLabel, projectMotorKey, projectOpenEvent, projectOpenIntent, projectRoleBody, validProjectId } from '../adminProjectsModel';
import { filterPickerCatalog, groupPickerCatalog, pickerDisplayLabel, pickerReasonKey } from '../../../modules/development/pickerModel';
import StarIcon from '@mui/icons-material/Star';
import StarBorderIcon from '@mui/icons-material/StarBorder';
import TaskAltIcon from '@mui/icons-material/TaskAlt';
import RadioButtonUncheckedIcon from '@mui/icons-material/RadioButtonUnchecked';
import { flagWho } from '../../../modules/development/projectFlags';
import { motorLinkAvailability } from '../../../modules/development/motorAvailability';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const ROLES: ProjectMember['role'][] = ['customer', 'engineer', 'owner'];
const PANEL = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5 } as const;
const CHIP = { height: 18, fontSize: 10.5, fontWeight: 700 } as const;

interface ProjectRow {
  id: string; title: string; customer_org?: string; status: ProjectStatus;
  completeness?: { filled_required?: number; total_required?: number; filled?: number; required?: number };
  members: ProjectMember[]; linked_motors: Array<{ die: string; config: string; status?: 'current' | 'trial' }>;
  pending_invites?: ProjectMember[];
  last_activity?: string | null;
  grant_removal_candidates?: Array<{ email: string; die: string; config: string; reason?: string }>;
}
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
      const [p, u] = await Promise.all([
        request<{ projects: ProjectRow[] }>('/api/admin/projects'),
        request<{ users: RegistryUser[] }>('/api/auth/users').catch(() => ({ users: [] })),
      ]);
      if (epoch !== loadEpoch.current) return;
      setRows(p.projects ?? []); setUsers(u.users ?? []);
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
      <TableHead><TableRow><TableCell>{t('projects.project')}</TableCell><TableCell>{t('projects.owner')}</TableCell><TableCell>{t('projects.status')}</TableCell><TableCell>{t('projects.completeness')}</TableCell><TableCell>{t('projects.members')}</TableCell><TableCell>{t('projects.motors')}</TableCell><TableCell>{t('projects.activity')}</TableCell></TableRow></TableHead>
      <TableBody>{filtered.map((p) => {
        return <TableRow key={p.id} hover onClick={() => setSelectedId(p.id)} sx={{ cursor: 'pointer' }}>
          <TableCell><Typography sx={{ fontSize: 12.5, fontWeight: 700, color: 'var(--text-0)' }}>{p.title}</Typography><Typography sx={{ fontSize: 10.5, color: 'var(--text-4)' }}>{p.id}{p.customer_org ? ` · ${p.customer_org}` : ''}</Typography></TableCell>
          <TableCell sx={{ overflowWrap: 'anywhere' }}>{projectOwnerLabel(p.members ?? [], p.pending_invites ?? [])}</TableCell>
          <TableCell><Chip size="small" label={td(`status.${p.status}`, { defaultValue: p.status })} sx={{ ...CHIP, color: '#60a5fa', bgcolor: '#2563eb1f' }} /></TableCell>
          <TableCell>{projectCompletenessLabel(p.completeness)}</TableCell>
          <TableCell>{projectAdminMembers(p.members ?? [], p.pending_invites ?? []).length}</TableCell><TableCell>{p.linked_motors?.length ?? 0}</TableCell><TableCell>{dateLabel(p.last_activity)}</TableCell>
        </TableRow>;
      })}{filtered.length === 0 && <TableRow><TableCell colSpan={7} sx={{ textAlign: 'center', color: 'var(--text-4)', py: 3 }}>{t('projects.empty')}</TableCell></TableRow>}</TableBody>
    </Table></Paper>}
    <Drawer anchor="right" open={!!current} onClose={() => setSelectedId(null)} PaperProps={{ sx: { width: { xs: '100%', sm: 430 }, bgcolor: 'var(--panel)', p: 2.5, overflowY: 'auto' } }}>
      {current && <ProjectDrawer project={current} users={users} onClose={() => setSelectedId(null)} onChanged={changed} onManageGrants={setCandidateUser} onOpenProject={() => { const intent = projectOpenIntent(current.id); try { sessionStorage.setItem(intent.storageKey, intent.projectId); } catch { /* Event path still opens while the page is mounted. */ } setSelectedId(null); setActiveTab('development' as never); window.dispatchEvent(new CustomEvent(projectOpenEvent, { detail: { projectId: intent.projectId } })); }} />}
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

const ProjectDrawer: React.FC<{ project: ProjectRow; users: RegistryUser[]; onClose: () => void; onChanged: (s: string) => void; onManageGrants: (u: RegistryUser | null) => void; onOpenProject: () => void }> = ({ project, users, onClose, onChanged, onManageGrants, onOpenProject }) => {
  const { t } = useTranslation('admin');
  const { t: td } = useTranslation('development');
  const [email, setEmail] = useState<string | null>(null); const [role, setRole] = useState<ProjectMember['role']>('customer');
  const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null);
  const [picker, setPicker] = useState<{ this_project: ProjectMotorPickerItem[]; catalog: ProjectMotorPickerItem[] }>({ this_project: [], catalog: [] }); const [pickerOpen, setPickerOpen] = useState(false); const [pickerSearch, setPickerSearch] = useState(''); const [unlinkTarget, setUnlinkTarget] = useState<{ die: string; config: string } | null>(null);
  const [linkedMotors, setLinkedMotors] = useState<ProjectMotor[] | null>(null);
  const pickerEpoch = useRef(0);
  const linkedMotorEpoch = useRef(0);
  const currentProjectId = useRef(project.id); currentProjectId.current = project.id;
  const loadPicker = useCallback(async (id?: string) => { const targetId = id ?? currentProjectId.current; const epoch = ++pickerEpoch.current; try { const value = await request<{ this_project: ProjectMotorPickerItem[]; catalog: ProjectMotorPickerItem[] }>(`/api/projects/${enc(targetId)}/motors/picker`); if (pickerEpoch.current === epoch && targetId === currentProjectId.current) setPicker(value); } catch { if (pickerEpoch.current === epoch && targetId === currentProjectId.current) setPicker({ this_project: [], catalog: [] }); } }, []);
  const loadLinkedMotors = useCallback(async (id?: string) => { const targetId = id ?? currentProjectId.current; const epoch = ++linkedMotorEpoch.current; try { const value = await request<{ motors: ProjectMotor[] }>(`/api/projects/${enc(targetId)}/motors?show_all=true`); if (epoch === linkedMotorEpoch.current && targetId === currentProjectId.current) setLinkedMotors(value.motors ?? []); } catch { if (epoch === linkedMotorEpoch.current && targetId === currentProjectId.current) setLinkedMotors(null); } }, []);
  useEffect(() => { setBusy(false); setError(null); setPickerOpen(false); setPickerSearch(''); setUnlinkTarget(null); setLinkedMotors(null); void loadPicker(project.id); void loadLinkedMotors(project.id); return () => { pickerEpoch.current += 1; linkedMotorEpoch.current += 1; }; }, [project.id, loadPicker, loadLinkedMotors]);
  const act = async (path: string, method: string, body?: unknown, ok = t('projects.saved')) => {
    const id = project.id;
    setBusy(true); setError(null);
    try { await request(path, { method, ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) }); if (id !== currentProjectId.current) return false; onChanged(ok); await Promise.all([loadPicker(id), loadLinkedMotors(id)]); return true; }
    catch (e) { if (id === currentProjectId.current) setError(e instanceof Error ? e.message : String(e)); return false; } finally { if (id === currentProjectId.current) setBusy(false); }
  };
  const visibleMembers = projectAdminMembers(project.members ?? [], project.pending_invites ?? []);
  const adminMotors: ProjectMotor[] = linkedMotors ?? (project.linked_motors ?? []).map((motor) => ({ ...motor, status: motor.status ?? 'current' }));
  const memberEmails = new Set(visibleMembers.map((m) => m.email.toLowerCase()));
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
    {/* ONE tidy row per motor in a fixed grid (owner 2026-10-26): name (one line, the full name + step note in the tooltip) | status |
        recommended star | report reviewed | unlink.  Nothing is opened from here and no note is edited here (both live in the
        Development project table). */}
    {adminMotors.map((m) => {
      const motorDie = m.canonical_die ?? m.die;
      const live = m.kind !== 'history';
      const avail = motorLinkAvailability(m, undefined, true);
      const computed = !!(avail.configureDuty ?? avail.pointsDuty);     // the same test the open buttons used to make
      const flag = (name: 'recommended' | 'report_reviewed', value: boolean) => act('/api/projects/' + enc(project.id) + '/motors/' + name, 'PATCH', { die: motorDie, config: m.config, value }, td(name === 'recommended' ? 'recommended.label' : 'report.reviewed'));
      return (
        <Box key={projectMotorKey(motorDie, m.config) + (m.snapshot_id ?? '')} data-testid="linked-motor-row"
          sx={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) 112px 32px 32px 32px', alignItems: 'center', columnGap: 0.5, py: 0.2 }}>
          <Tooltip title={`${motorDie} / ${m.config}${m.note ? ` — ${m.note}` : ''}`}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, minWidth: 0 }}>
              <Typography noWrap sx={{ fontSize: 12, minWidth: 0 }}>{motorDie} / {m.config}</Typography>
              {live && !computed && <Chip size="small" label={td('notComputed')} sx={{ height: 18, fontSize: 10, flex: '0 0 auto', color: 'var(--text-4)', bgcolor: 'var(--panel-2)' }} />}
            </Box>
          </Tooltip>
          {live ? <>
            <Tooltip title={td('motorStatus.tip')}><TextField select size="small" value={m.status === 'trial' ? 'trial' : 'current'} disabled={busy} aria-label={td('motorStatus.label')}
              sx={{ '& .MuiSelect-select': { py: 0.3, fontSize: 11 } }}
              onChange={async (e) => { const status = e.target.value as 'current' | 'trial'; await act('/api/projects/' + enc(project.id) + '/motors/status', 'PATCH', { die: motorDie, config: m.config, status }, td('motorStatus.updated')); }}>{['current', 'trial'].map((status) => <MenuItem key={status} value={status}>{td(`motorStatus.${status}`)}</MenuItem>)}</TextField></Tooltip>
            <Tooltip title={m.recommended ? `${td('recommended.tip')} ${flagWho(m.recommended)}` : td('recommended.setTip')}><span><IconButton size="small" disabled={busy} aria-label={td('recommended.label')} aria-pressed={!!m.recommended}
              onClick={() => void flag('recommended', !m.recommended)} sx={{ color: m.recommended ? '#f59e0b' : 'var(--text-4)' }}>{m.recommended ? <StarIcon sx={{ fontSize: 17 }} /> : <StarBorderIcon sx={{ fontSize: 17 }} />}</IconButton></span></Tooltip>
            <Tooltip title={m.report_reviewed ? `${td('report.reviewedTip')} ${flagWho(m.report_reviewed)}` : td('report.reviewedTip')}><span><IconButton size="small" disabled={busy} aria-label={td('report.reviewed')} aria-pressed={!!m.report_reviewed}
              onClick={() => void flag('report_reviewed', !m.report_reviewed)} sx={{ color: m.report_reviewed ? '#22c55e' : 'var(--text-4)' }}>{m.report_reviewed ? <TaskAltIcon sx={{ fontSize: 17 }} /> : <RadioButtonUncheckedIcon sx={{ fontSize: 17 }} />}</IconButton></span></Tooltip>
            <IconButton size="small" disabled={busy} aria-label={t('projects.unlinkMotor')} onClick={() => setUnlinkTarget({ die: motorDie, config: m.config })}><CloseIcon sx={{ fontSize: 14 }} /></IconButton>
          </> : <><span /><span /><span /><span /></>}
        </Box>);
    })}
    <Box sx={{ display: 'flex', gap: 0.75, mt: 1 }}><Button size="small" disabled={busy} onClick={() => { setPickerOpen(true); void loadPicker(); }} sx={{ textTransform: 'none' }}>{td('linkMotor')}</Button></Box>
    <Dialog open={pickerOpen} onClose={() => !busy && setPickerOpen(false)} fullWidth maxWidth="sm"><DialogTitle>{td('linkMotor')}</DialogTitle><DialogContent>
      <Typography sx={{ fontSize: 12, fontWeight: 700, mb: 0.5 }}>{td('picker.thisProject')}</Typography>{picker.this_project.map((item) => <Box key={`${item.die}/${item.config}`} sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 0.3 }}><Tooltip title={`${item.unlinked_by ?? ''} ${item.unlinked_at ?? ''}`}><Typography sx={{ flex: 1, fontSize: 11 }}>{pickerDisplayLabel(item, td)} · {item.die}/{item.config}</Typography></Tooltip><Tooltip title={item.disabled ? td(pickerReasonKey(item.reason)) : ''}><span><Button size="small" disabled={busy || item.disabled} onClick={() => void act(adminProjectPaths.motors(project.id), 'POST', { die: item.die, config: item.config }, td('picker.restored'))}>{td('picker.restore')}</Button></span></Tooltip></Box>)}
      <Divider sx={{ my: 1 }} /><Typography sx={{ fontSize: 12, fontWeight: 700 }}>{td('picker.catalog')}</Typography><TextField size="small" fullWidth placeholder={td('picker.search')} value={pickerSearch} onChange={(e) => setPickerSearch(e.target.value)} sx={{ my: 0.75 }} />
      <Box sx={{ maxHeight: 300, overflowY: 'auto' }}>{groupPickerCatalog(filterPickerCatalog(picker.catalog, pickerSearch)).map(([die, items]) => <Box key={die}><Typography sx={{ fontSize: 11, fontWeight: 700 }}>{die}</Typography>{items.map((item) => <Box key={`${die}/${item.config}`} sx={{ display: 'flex', alignItems: 'center', py: 0.25 }}><Tooltip title={item.disabled ? td(pickerReasonKey(item.reason)) : ''}><Typography sx={{ flex: 1, fontSize: 11, color: item.disabled ? 'var(--text-4)' : 'var(--text-1)' }}>{item.config} · {pickerDisplayLabel(item, td)}</Typography></Tooltip><Button size="small" disabled={busy || item.disabled} onClick={() => void act(adminProjectPaths.motors(project.id), 'POST', { die: item.die, config: item.config }, t('projects.motorLinked'))}>{td('link')}</Button></Box>)}</Box>)}</Box>
    </DialogContent><DialogActions><Button disabled={busy} onClick={() => setPickerOpen(false)}>{td('close')}</Button></DialogActions></Dialog>
    <Dialog open={!!unlinkTarget} onClose={() => !busy && setUnlinkTarget(null)}><DialogTitle>{td('unlink')} {unlinkTarget?.die} / {unlinkTarget?.config}?</DialogTitle><DialogContent>{td('unlinkConfirm')}</DialogContent><DialogActions><Button disabled={busy} onClick={() => setUnlinkTarget(null)}>{td('cancel')}</Button><Button color="error" disabled={busy} onClick={async () => { if (unlinkTarget && await act(adminProjectPaths.motor(project.id, unlinkTarget.die, unlinkTarget.config), 'DELETE', undefined, t('projects.motorUnlinked'))) setUnlinkTarget(null); }}>{td('unlink')}</Button></DialogActions></Dialog>
    {/* Grants follow the project automatically (owner 2026-10-27): only what the server could not settle is listed (normally nothing). */}
    {(project.grant_removal_candidates ?? []).length > 0 && <>
    <Divider sx={{ my: 1.5, borderColor: 'var(--line-soft)' }} />
    <Typography sx={{ fontSize: 11, fontWeight: 700, color: 'var(--text-3)', textTransform: 'uppercase', mb: 0.5 }}>{t('projects.grantCandidates')}</Typography>
    <Tooltip title={td('grantReviewTip')}><Typography sx={{ fontSize: 10.5, color: 'var(--text-4)', mb: 0.5 }}>{td('grantReviewCount', { count: project.grant_removal_candidates?.length ?? 0 })}</Typography></Tooltip>
    {(project.grant_removal_candidates ?? []).map((c, i) => <Box key={`${c.email}/${c.die}/${c.config}/${i}`} sx={{ display: 'flex', alignItems: 'center', gap: 0.5, py: 0.25 }}><Tooltip title={c.reason ? td(`grantReason.${c.reason}`, { defaultValue: c.reason }) : ''}><Typography sx={{ flex: 1, fontSize: 11, color: 'var(--text-2)' }}>{c.email} · {c.die}/{c.config}</Typography></Tooltip><Button size="small" disabled={!candidateUserFor(c.email)} onClick={() => onManageGrants(candidateUserFor(c.email))} sx={{ textTransform: 'none', fontSize: 10 }}>{t('projects.manageMotors')}</Button></Box>)}
    </>}
    {error && <Typography sx={{ color: '#f87171', fontSize: 11, mt: 1 }}>{error}</Typography>}
  </>;
};

export default ProjectsSection;
