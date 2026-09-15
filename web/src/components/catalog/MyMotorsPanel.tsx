/**
 * MyMotorsPanel — the signed-in user's PRIVATE motor space (Motors tab).
 *
 * Shows the motors the user duplicated from the shared catalog plus motors
 * other users shared.  ▶ loads a motor as THIS CLIENT'S COPY (local geometry
 * + materials overlay + operating point — the same path as an ordinary
 * user's ▶ on a shared duty; nothing on the server changes).  Rename /
 * share / delete apply only to the user's own entries.
 */
import React, { useEffect, useState } from 'react';
import { Box, Typography, IconButton, Tooltip, Chip } from '@mui/material';
import { useMotorStore } from '../../stores/motorStore';
import { useAuth } from '../../contexts/AuthContext';
import { TextPromptDialog, ConfirmDialog,
         type TextPromptState, type ConfirmState } from '../common/PromptDialogs';
import { clearActiveDuty } from '../../lib/dutySettings';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

interface MyMotor {
  id: string; name: string; owner: string; shared: boolean;
  created_at?: string;
  src?: { die: string; config: string; duty?: string | null };
  machine?: { stator_diameter?: number; motor_length?: number;
              num_slots?: number; num_poles?: number };
}

const MyMotorsPanel: React.FC = () => {
  const { user } = useAuth();
  const { updateGeometryViaApi } = useMotorStore();
  const [mine, setMine] = useState<MyMotor[]>([]);
  const [shared, setShared] = useState<MyMotor[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [askText, setAskText] = useState<TextPromptState | null>(null);
  const [askConfirm, setAskConfirm] = useState<ConfirmState | null>(null);

  const load = async () => {
    try {
      const r = await fetch(`${API}/api/my_motors`);
      if (!r.ok) { setMine([]); setShared([]); return; }
      const d = await r.json();
      setMine(d.mine ?? []); setShared(d.shared ?? []);
    } catch { /* backend away — the section just stays empty */ }
  };
  useEffect(() => {
    if (!user) return;
    void load();
    const onChanged = () => { void load(); };
    window.addEventListener('family-changed', onChanged);
    return () => window.removeEventListener('family-changed', onChanged);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  if (!user || (mine.length === 0 && shared.length === 0)) return null;

  const applyMotor = async (m: MyMotor) => {
    setMsg(null);
    try {
      const r = await fetch(`${API}/api/my_motors/${encodeURIComponent(m.id)}/payload`);
      if (!r.ok) throw new Error((await r.json()).detail ?? `HTTP ${r.status}`);
      const p = await r.json();
      // A private copy is NOT a catalog duty: stop filing operating-point
      // edits under whichever duty was selected before, or this motor's
      // current / rpm / temperature would be written into that duty's
      // per-duty memory and come back the next time it is opened.
      try { clearActiveDuty(); } catch { /* memory is a convenience */ }
      // For the OWNER this apply is a real PUT — a private copy of a catalog
      // machine (same Ø, same slots/poles, edited magnet) would pass the die
      // identity guard and be synced INTO the catalog die.  Release the server
      // context first: no active die, nothing to sync into.
      const { releaseFamilyContext } = await import('../../lib/familyContext');
      await releaseFamilyContext(`My motors: ${m.name}`);
      // Same client-side apply as an ordinary user's ▶ on a shared duty:
      // local geometry merge (rides as ?geo=), materials overlay (?mat=),
      // operating point into the panel's persisted values.
      await updateGeometryViaApi(p.geometry);
      try {
        const cur = JSON.parse(localStorage.getItem('mat.assign.local') || '{}');
        for (const part of ['magnet', 'stator_core', 'rotor_core']) {
          if (p.materials?.[part]) cur[part] = p.materials[part];
        }
        localStorage.setItem('mat.assign.local', JSON.stringify(cur));
        window.dispatchEvent(new CustomEvent('mat-assign-local-changed'));
      } catch { /* quota */ }
      const set = (k: string, v: unknown) => {
        try { localStorage.setItem('sim.' + k, JSON.stringify(v)); } catch { /* quota */ }
      };
      if (p.sim) {
        set('current', p.sim.current_a); set('gamma', p.sim.gamma_deg);
        set('rpm', p.sim.rpm); set('frequency', p.sim.frequency);
        set('opMode', p.sim.mode);
        if (p.sim.connection) set('connection', p.sim.connection);
        set('starDelta', (String(p.sim.star_delta ?? 'star').toLowerCase().startsWith('d')) ? 'delta' : 'star');
      }
      // Panels re-read their persisted fields (same contract as the catalog
      // load — prevents the mounted panel's old state writing itself back).
      window.dispatchEvent(new CustomEvent('sim-settings-restored'));
      setMsg(`✓ '${m.name}' loaded (your copy)`);
    } catch (e: any) { setMsg(`✗ ${e?.message ?? e}`); }
  };

  const rename = (m: MyMotor) => setAskText({
    title: `Rename '${m.name}'`, label: 'New name', initial: m.name,
    onSubmit: async (name) => {
      const r = await fetch(`${API}/api/my_motors/${encodeURIComponent(m.id)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }) });
      setMsg(r.ok ? `✓ renamed to '${name}'` : `✗ ${(await r.json()).detail ?? r.status}`);
      void load();
    },
  });
  const toggleShare = async (m: MyMotor) => {
    const r = await fetch(`${API}/api/my_motors/${encodeURIComponent(m.id)}/share?unshare=${m.shared}`,
      { method: 'POST' });
    setMsg(r.ok ? (m.shared ? `✓ '${m.name}' is private again` : `✓ '${m.name}' is now shared`)
                : `✗ ${(await r.json()).detail ?? r.status}`);
    void load();
  };
  const remove = (m: MyMotor) => setAskConfirm({
    title: `Delete '${m.name}' from your motors?`,
    body: 'Only your private copy is deleted; the catalog motor it came from stays.',
    onConfirm: async () => {
      const r = await fetch(`${API}/api/my_motors/${encodeURIComponent(m.id)}`, { method: 'DELETE' });
      setMsg(r.ok ? `✓ deleted` : `✗ ${(await r.json()).detail ?? r.status}`);
      void load();
    },
  });

  const Row: React.FC<{ m: MyMotor; own: boolean }> = ({ m, own }) => (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 0.4 }}>
      <Tooltip title="Load this motor as your working copy (nothing shared changes)">
        <IconButton size="small" onClick={() => void applyMotor(m)}
          sx={{ color: '#34d399', p: 0.25 }}>▶</IconButton>
      </Tooltip>
      <Typography sx={{ fontSize: 12, color: 'var(--text-1)', fontWeight: 600 }}>{m.name}</Typography>
      {m.machine?.stator_diameter != null && (
        <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
          Ø {m.machine.stator_diameter} mm · {m.machine.num_slots}s/{m.machine.num_poles}p · {m.machine.motor_length} mm
        </Typography>
      )}
      {own ? (
        <>
          <Chip size="small" label={m.shared ? 'shared' : 'private'}
            sx={{ height: 16, fontSize: 9,
                  color: m.shared ? '#34d399' : 'var(--text-3)',
                  bgcolor: 'transparent',
                  border: `1px solid ${m.shared ? '#34d39955' : 'var(--line-soft)'}` }} />
          <Tooltip title="Rename"><IconButton size="small" onClick={() => rename(m)}
            sx={{ color: 'var(--text-3)', p: 0.25, fontSize: 13 }}>✎</IconButton></Tooltip>
          <Tooltip title={m.shared ? 'Make private again' : 'Share with everyone'}>
            <IconButton size="small" onClick={() => void toggleShare(m)}
              sx={{ color: '#60a5fa', p: 0.25, fontSize: 13 }}>{m.shared ? '🔒' : '⤴'}</IconButton>
          </Tooltip>
          <Tooltip title="Delete your copy"><IconButton size="small" onClick={() => remove(m)}
            sx={{ color: '#f87171', p: 0.25, fontSize: 13, ml: 1.5 }}>✕</IconButton></Tooltip>
        </>
      ) : (
        <Chip size="small" label={`by ${m.owner}`}
          sx={{ height: 16, fontSize: 9, color: 'var(--text-3)',
                bgcolor: 'transparent', border: '1px solid var(--line-soft)' }} />
      )}
    </Box>
  );

  return (
    <Box sx={{ p: 2, mb: 2, borderRadius: 2, border: '1px solid var(--line-soft)',
               bgcolor: 'var(--panel-2)' }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
        <Typography sx={{ fontWeight: 800, color: '#a78bfa', fontSize: '0.95rem' }}>
          My motors
        </Typography>
        <Box sx={{ flex: 1 }} />
        {msg && <Typography sx={{ fontSize: 11,
          color: msg.startsWith('✗') ? '#fca5a5' : '#34d399' }}>{msg}</Typography>}
      </Box>
      {mine.map(m => <Row key={m.id} m={m} own />)}
      {shared.length > 0 && (
        <>
          <Typography sx={{ fontSize: 10, color: 'var(--text-4)', mt: 1,
            textTransform: 'uppercase', letterSpacing: '0.04em' }}>Shared by others</Typography>
          {shared.map(m => <Row key={m.id} m={m} own={false} />)}
        </>
      )}
      <TextPromptDialog state={askText} onClose={() => setAskText(null)} />
      <ConfirmDialog state={askConfirm} onClose={() => setAskConfirm(null)} />
    </Box>
  );
};

export default MyMotorsPanel;
