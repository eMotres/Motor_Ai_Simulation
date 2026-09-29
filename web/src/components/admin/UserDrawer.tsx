// Admin · Users — side drawer with one account's details and actions, so the
// table row itself stays a single compact line.
import React, { useEffect, useState } from 'react';
import {
  Drawer, Box, Typography, Select, MenuItem, Button, Chip, Divider, CircularProgress,
} from '@mui/material';
import KeyIcon from '@mui/icons-material/Key';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import BlockIcon from '@mui/icons-material/Block';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import HelpTip from '../common/HelpTip';
import { TIERS } from './dialogs/InviteDialog';
import type { RegistryUser } from './dialogs/MotorsDialog';

const TIER_COLOR: Record<string, string> = {
  anon: 'var(--text-4)', free: 'var(--text-3)', pro: '#3b82f6', team: '#a855f7', admin: '#fbbf24',
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
  onTier: (tier: string) => void;
  onToggleDisabled: () => void;
  onDelete: () => void;
  onResetPassword: () => void;
  onOpenMotors: () => void;
  onRevokeAllSessions: () => void;
}> = ({
  user, liveSessions, lastLogin, cpuH30d, jobs30d, onClose, busy,
  onTier, onToggleDisabled, onDelete, onResetPassword, onOpenMotors, onRevokeAllSessions,
}) => {
  const [revoking, setRevoking] = useState(false);
  useEffect(() => setRevoking(false), [user?.email]);

  const g = user?.motors;
  const motorsLabel = user?.tier === 'admin' ? 'all (admin)' : g?.all ? 'all' : g?.dies?.length ? `${g.dies.length} granted` : 'none';

  return (
    <Drawer anchor="right" open={!!user} onClose={onClose} PaperProps={{ sx: { width: 340, bgcolor: 'var(--panel)', p: 2.5 } }}>
      {user && (
        <>
          <Typography sx={{ fontSize: 15, fontWeight: 800, color: 'var(--text-0)', wordBreak: 'break-all' }}>{user.email}</Typography>
          {user.name && <Typography sx={{ fontSize: 12, color: 'var(--text-4)', mb: 1 }}>{user.name}</Typography>}

          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 1.5, mb: 0.5 }}>
            <Typography sx={{ fontSize: 11, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase' }}>Plan</Typography>
            <HelpTip title="Changing this takes effect on the account's next request." />
          </Box>
          <Select size="small" value={user.tier} disabled={busy} onChange={(e) => onTier(e.target.value)}
            sx={{ fontSize: 13, fontWeight: 700, color: TIER_COLOR[user.tier] ?? 'var(--text-2)', width: '100%' }}>
            {TIERS.map((t) => <MenuItem key={t} value={t} sx={{ fontSize: 13, color: TIER_COLOR[t] }}>{t}</MenuItem>)}
          </Select>

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
            {user.tier !== 'admin' && (
              <Button size="small" onClick={onOpenMotors} sx={{ textTransform: 'none', fontSize: 11 }}>Change…</Button>
            )}
          </Box>

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
