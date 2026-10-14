// Admin · Users — side drawer with one account's details and actions, so the
// table row itself stays a single compact line.
import React, { useEffect, useState } from 'react';
import {
  Drawer, Box, Typography, Select, MenuItem, Button, Chip, Divider, CircularProgress, Switch, TextField,
} from '@mui/material';
import KeyIcon from '@mui/icons-material/Key';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import BlockIcon from '@mui/icons-material/Block';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import HelpTip from '../common/HelpTip';
import { openNdaPdf } from './ndaPdf';
import { ROLES } from './dialogs/InviteDialog';
import { roleLabel } from '../../lib/accessUi';
import type { RegistryUser } from './dialogs/MotorsDialog';

const ROLE_COLOR: Record<string, string> = {
  anon: 'var(--text-4)', user: 'var(--text-3)', engineer: '#60a5fa', admin: '#fbbf24',
};

const fmt = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—';

interface Row { label: string; value: React.ReactNode }
const InfoRow: React.FC<Row> = ({ label, value }) => (
  <Box sx={{ display: 'flex', justifyContent: 'space-between', py: 0.5, borderBottom: '1px solid var(--line-soft)' }}>
    <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{label}</Typography>
    <Typography sx={{ fontSize: 12.5, color: 'var(--text-0)', fontWeight: 600 }}>{value}</Typography>
  </Box>
);

const UserDrawer: React.FC<{
  user: RegistryUser | null;
  liveSessions: number;
  lastLogin: number | null;
  cpuH30d: number | null;
  jobs30d: number | null;
  onClose: () => void;
  busy: boolean;
  onRole: (role: string) => void;
  onQuota: (hours: number | null) => void;
  onToggleDisabled: () => void;
  onDelete: () => void;
  onResetPassword: () => void;
  onOpenMotors: () => void;
  onNda: (signed: boolean, date?: string) => void;
  onRevokeAllSessions: () => void;
}> = ({
  user, liveSessions, lastLogin, cpuH30d, jobs30d, onClose, busy,
  onRole, onQuota, onToggleDisabled, onDelete, onResetPassword, onOpenMotors, onNda, onRevokeAllSessions,
}) => {
  const [revoking, setRevoking] = useState(false);
  const [quota, setQuota] = useState('');
  useEffect(() => { setRevoking(false); setQuota(user?.sim_quota_cpu_h_month != null ? String(user.sim_quota_cpu_h_month) : ''); },
    [user?.email, user?.sim_quota_cpu_h_month]);
  const saveQuota = () => {
    const t = quota.trim();
    if (t === '') { if (user?.sim_quota_cpu_h_month != null) onQuota(null); return; }
    const v = Number(t);
    if (Number.isFinite(v) && v >= 0 && v !== user?.sim_quota_cpu_h_month) onQuota(v);
  };

  const g = user?.motors;
  const motorsLabel = user?.role === 'admin' ? 'all (admin)' : g?.all ? 'all' : g?.dies?.length ? `${g.dies.length} granted` : 'none';

  return (
    <Drawer anchor="right" open={!!user} onClose={onClose} PaperProps={{ sx: { width: 340, bgcolor: 'var(--panel)', p: 2.5 } }}>
      {user && (
        <>
          <Typography sx={{ fontSize: 15, fontWeight: 800, color: 'var(--text-0)', wordBreak: 'break-all' }}>{user.email}</Typography>
          {user.name && <Typography sx={{ fontSize: 12, color: 'var(--text-4)', mb: 1 }}>{user.name}</Typography>}

          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 1.5, mb: 0.5 }}>
            <Typography sx={{ fontSize: 11, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase' }}>Role</Typography>
            <HelpTip title={`Configure: Motors, Configure and the 3D model. Simulation: the whole simulation UI on the account's own workspace and granted motors, no admin sections. Admin: everything. Takes effect on the account's next request.`} />
          </Box>
          <Select size="small" value={user.role} disabled={busy} onChange={(e) => onRole(e.target.value)}
            sx={{ fontSize: 13, fontWeight: 700, color: ROLE_COLOR[user.role] ?? 'var(--text-2)', width: '100%' }}>
            {ROLES.map((r) => <MenuItem key={r} value={r} sx={{ fontSize: 13, color: ROLE_COLOR[r] }}>{roleLabel(r)}</MenuItem>)}
          </Select>

          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 1.5, mb: 0.5 }}>
            <Typography sx={{ fontSize: 11, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase' }}>CPU quota (h / month)</Typography>
            <HelpTip title="Preparation only: the number is stored but no job is blocked by it yet. Empty means not set." />
          </Box>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <TextField size="small" type="number" value={quota} disabled={busy} placeholder="not set"
              onChange={(e) => setQuota(e.target.value)} onBlur={saveQuota}
              onKeyDown={(e) => { if (e.key === 'Enter') saveQuota(); }}
              inputProps={{ min: 0, step: 1, 'aria-label': 'CPU quota hours per month' }}
              sx={{ width: 120, '& input': { fontSize: 12.5, py: 0.5 } }} />
            <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>
              used this month: {user.cpu_h_month != null ? `${user.cpu_h_month.toFixed(2)} h` : '—'}
            </Typography>
          </Box>

          <Box sx={{ mt: 2 }}>
            <InfoRow label="Created" value={fmt(user.created)} />
            <InfoRow label="Last login" value={lastLogin ? new Date(lastLogin * 1000).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }) : '—'} />
            <InfoRow label="Live sessions" value={liveSessions} />
            <InfoRow label="Jobs (30 d)" value={jobs30d ?? '—'} />
            <InfoRow label="CPU-h (30 d)" value={cpuH30d != null ? cpuH30d.toFixed(2) : '—'} />
            <InfoRow label="Status" value={
              <Chip size="small" label={user.disabled ? 'disabled' : 'active'}
                sx={{ height: 18, fontSize: 10, bgcolor: 'var(--panel-2)', color: user.disabled ? '#f87171' : '#4ade80' }} />
            } />
          </Box>

          <Divider sx={{ my: 2, borderColor: 'var(--line-soft)' }} />

          <Typography sx={{ fontSize: 11, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', mb: 1 }}>Motor grants</Typography>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
            <Typography sx={{ fontSize: 13, color: 'var(--text-0)' }}>{motorsLabel}</Typography>
            {user.role !== 'admin' && (
              <Button size="small" onClick={onOpenMotors} sx={{ textTransform: 'none', fontSize: 11 }}>Change…</Button>
            )}
          </Box>

          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
            <Typography sx={{ fontSize: 11, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase' }}>NDA signed</Typography>
            <HelpTip title="3D model menu: everybody can look at and rotate the model; with the NDA signed the account also gets the tools (parts, exploded view, section, measure) and the STEP download. Every download is logged." />
          </Box>
          {user.role === 'admin' ? (
            <Typography sx={{ fontSize: 13, color: 'var(--text-0)', mb: 2 }}>download open (admin)</Typography>
          ) : (
            <Box sx={{ mb: 2 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Switch size="small" checked={!!user.nda?.signed} disabled={busy}
                onChange={(_, on) => onNda(on, on ? (user.nda?.date || undefined) : undefined)} />
              <TextField size="small" type="date" disabled={busy || !user.nda?.signed} value={user.nda?.date ?? ''}
                onChange={(e) => e.target.value && onNda(true, e.target.value)}
                inputProps={{ 'aria-label': 'NDA date' }} sx={{ '& input': { fontSize: 12.5, py: 0.5 } }} />
            </Box>
            {(user.nda?.signed || user.nda?.record_id) && (
              <Typography sx={{ fontSize: 11.5, color: 'var(--text-3)', mt: 0.5 }}>
                {user.nda?.signed ? (user.nda.by === 'online' ? 'signed online' : 'set by admin') : `revoked ${user.nda?.revoked ?? ''}`}
                {user.nda?.version ? ` · ${user.nda.version}` : ''}{user.nda?.company ? ` · ${user.nda.company}` : ''}
                {user.nda?.record_id && (
                  <Button size="small" onClick={() => void openNdaPdf(user.nda!.record_id!)} sx={{ textTransform: 'none', fontSize: 11, ml: 0.5, minWidth: 0 }}>PDF</Button>
                )}
              </Typography>
            )}
            </Box>
          )}

          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
            <Typography sx={{ fontSize: 11, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase' }}>Dataset sharing</Typography>
            <HelpTip title="The account's own choice (NDA dialog or account menu), off by default and separate from the NDA. Only the account can change it; every change is an audit line. Data is exported to the dataset only while it is on and was created while it was on." />
          </Box>
          <Typography data-testid="data-sharing-state" sx={{ fontSize: 13, color: 'var(--text-0)', mb: 2 }}>
            {user.data_sharing?.consent ? 'consent given' : 'no consent'}
            {user.data_sharing?.consent && user.data_sharing.version ? ` · ${user.data_sharing.version}` : ''}
            {user.data_sharing?.consent && user.data_sharing.time ? ` · ${user.data_sharing.time.replace('T', ' ').replace('Z', ' UTC')}` : ''}
          </Typography>

          <Typography sx={{ fontSize: 11, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', mb: 1 }}>Actions</Typography>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
            <Button size="small" disabled={busy} onClick={onToggleDisabled}
              startIcon={user.disabled ? <CheckCircleIcon sx={{ fontSize: 14 }} /> : <BlockIcon sx={{ fontSize: 14 }} />}
              sx={{ justifyContent: 'flex-start', textTransform: 'none', fontSize: 12.5, color: user.disabled ? '#4ade80' : 'var(--text-2)' }}>
              {user.disabled ? 'Enable account' : 'Disable account'}
            </Button>
            <Button size="small" disabled={busy} onClick={onResetPassword} startIcon={<KeyIcon sx={{ fontSize: 14 }} />}
              sx={{ justifyContent: 'flex-start', textTransform: 'none', fontSize: 12.5, color: 'var(--text-2)' }}>
              Reset password
            </Button>
            <Button size="small" disabled={busy || !liveSessions || revoking}
              onClick={() => { setRevoking(true); onRevokeAllSessions(); }}
              startIcon={revoking ? <CircularProgress size={12} /> : undefined}
              sx={{ justifyContent: 'flex-start', textTransform: 'none', fontSize: 12.5, color: 'var(--text-2)' }}>
              Revoke all sessions ({liveSessions})
            </Button>
            <Button size="small" disabled={busy} onClick={onDelete} startIcon={<DeleteOutlineIcon sx={{ fontSize: 14 }} />}
              sx={{ justifyContent: 'flex-start', textTransform: 'none', fontSize: 12.5, color: 'var(--text-3)', '&:hover': { color: '#f87171' } }}>
              Delete account
            </Button>
          </Box>
        </>
      )}
    </Drawer>
  );
};

export default UserDrawer;
