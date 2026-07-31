import React, { useEffect, useState } from 'react';
import {
  Box, Typography, Chip, CircularProgress, Button, Tooltip, IconButton,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField,
} from '@mui/material';
import BoltIcon from '@mui/icons-material/Bolt';
import CheckIcon from '@mui/icons-material/Check';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import DriveFileRenameOutlineIcon from '@mui/icons-material/DriveFileRenameOutline';
import { useUIStore } from '../../stores/motorStore';
import { useAuth } from '../../contexts/AuthContext';
import MyDesigns from './MyDesigns';
import MotorThumbnail from './MotorThumbnail';
import { openMotor } from '../common/motorSettings';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

interface Motor {
  id: string; diameter_mm: number; name: string; topology: string;
  slots: number; poles: number; rpm: number; current_a: number;
  T_avg_Nm: number; ripple_pct: number | null; gamma_deg: number;
  tier: string; description: string; preset?: string;
  owner?: string;       // "user" for saved motors (renamable), absent for curated
  thumb_svg?: string;   // inline real-geometry cross-section (user-saved motors)
  // enriched (optional) — shown when present
  power_w?: number; efficiency_pct?: number; voltage_pk_v?: number;
  magnet?: string; steel?: string; length_mm?: number; wire?: string;
  mass_kg?: number;     // active mass (stator+Cu+magnets+rotor+shaft)
}
interface Tier {
  id: string; name: string; price_usd: number; highlight: boolean;
  tagline: string; features: string[];
}
interface Catalog { tiers: Tier[]; diameters_mm: number[]; motors: Motor[]; }

const tierColor: Record<string, string> = { free: '#10b981', pro: '#3b82f6', team: '#a855f7' };

const fmtT = (t: number) => (t >= 10 ? t.toFixed(1) : t.toFixed(2));
const fmtP = (w: number) => (w >= 1000 ? `${(w / 1000).toFixed(2)} kW` : `${Math.round(w)} W`);

// one headline stat (big number + caption)
const Stat: React.FC<{ value: string; unit?: string; label: string; color?: string }> = ({ value, unit, label, color }) => (
  <Box sx={{ textAlign: 'center', flex: 1, minWidth: 0 }}>
    <Typography sx={{ fontWeight: 800, color: color ?? 'var(--text-0)', fontSize: '0.98rem', lineHeight: 1.1, whiteSpace: 'nowrap' }}>
      {value}{unit && <span style={{ fontSize: '0.62rem', color: 'var(--text-2)', fontWeight: 600 }}> {unit}</span>}
    </Typography>
    <Typography sx={{ color: 'var(--text-3)', fontSize: '0.58rem', textTransform: 'uppercase', letterSpacing: '0.04em', mt: 0.2 }}>{label}</Typography>
  </Box>
);

// one detail key/value row cell
const Detail: React.FC<{ k: string; v: React.ReactNode }> = ({ k, v }) => (
  <Box sx={{ display: 'flex', justifyContent: 'space-between', gap: 1, minWidth: 0 }}>
    <Typography sx={{ color: 'var(--text-3)', fontSize: '0.68rem', whiteSpace: 'nowrap' }}>{k}</Typography>
    <Typography sx={{ color: 'var(--text-1)', fontSize: '0.68rem', fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{v}</Typography>
  </Box>
);

const MotorsCatalog: React.FC = () => {
  const { isAdmin, user, signIn, enforced } = useAuth();
  const needsSignIn = enforced && !user;   // anon must register to load/configure
  const [cat, setCat] = useState<Catalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const setActiveTab = useUIStore((s) => s.setActiveTab);

  const load = () => fetch(`${API}/api/catalog`).then((r) => r.json()).then(setCat)
    .catch(() => setCat(null)).finally(() => setLoading(false));
  useEffect(() => { load(); }, []);

  // Confirm through a proper dialog — window.confirm is silently suppressed
  // by Chrome once "prevent additional dialogs" was ever ticked (the exact
  // failure that made the rename pencil look dead), so the trash icon
  // clicked and nothing visibly happened.
  const [deleteTarget, setDeleteTarget] = useState<Motor | null>(null);
  const [deleteErr, setDeleteErr] = useState<string | null>(null);
  const deleteMotor = (m: Motor) => { setDeleteTarget(m); setDeleteErr(null); };
  const submitDelete = async () => {
    const m = deleteTarget;
    if (!m) return;
    setBusy(m.id);
    try {
      const r = await fetch(`${API}/api/catalog/${encodeURIComponent(m.id)}`,
                            { method: 'DELETE' });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        setDeleteErr(String((j as any).detail || `HTTP ${r.status}`));
        return;
      }
      setDeleteTarget(null);
      await load();
    } finally { setBusy(null); }
  };

  // Rename runs through a proper dialog — window.prompt is silently
  // suppressed by Chrome once "prevent additional dialogs" was ever ticked,
  // which made the pencil look dead.
  const [renameTarget, setRenameTarget] = useState<Motor | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [renameErr, setRenameErr] = useState<string | null>(null);
  const openRename = (m: Motor) => { setRenameTarget(m); setRenameValue(m.name); setRenameErr(null); };
  const submitRename = async () => {
    const m = renameTarget;
    const name = renameValue.trim();
    if (!m?.preset || !name) return;
    if (name === m.name) { setRenameTarget(null); return; }
    setBusy(m.id);
    try {
      const r = await fetch(`${API}/api/presets/${encodeURIComponent(m.preset)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        setRenameErr(String(j.detail || `HTTP ${r.status}`));
        return;
      }
      setRenameTarget(null);
      await load();
    } catch (e) {
      setRenameErr(String(e));
    } finally { setBusy(null); }
  };

  const loadMotor = async (m: Motor) => {
    // Browsing is open to everyone; WORKING with a motor requires sign-in.
    // Anonymous visitors get the Google sign-in prompt instead of a broken load.
    if (needsSignIn) { void signIn(); return; }
    setBusy(m.id);
    try {
      // Open as YOUR editable copy: a curated template forks into "my_<id>" so
      // it stays pristine; your own motor opens in place.  Seeds the browser
      // mesh/sim + marks it the active working motor before the reload.
      if (m.preset) await openMotor(m.preset, m.name);
      else await fetch(`${API}/api/catalog/${m.id}/load`, { method: 'POST' });
      setActiveTab('geometry');
      window.location.reload();
    } catch { setBusy(null); }
  };

  if (loading) return <Box sx={{ p: 4 }}><CircularProgress size={22} /></Box>;
  if (!cat) return <Box sx={{ p: 4, color: '#f87171' }}>Catalog unavailable (backend offline).</Box>;

  const byDiameter = (d: number) => cat.motors.filter((m) => m.diameter_mm === d);

  const renderCard = (m: Motor) => {
    // UNIFORM card layout: every motor shows the same stats and the same
    // detail rows in the same places; a missing value renders as a dash.
    const hasEff = typeof m.efficiency_pct === 'number';
    const powerW = typeof m.power_w === 'number'
      ? m.power_w
      : (m.T_avg_Nm > 0 && m.rpm > 0 ? m.T_avg_Nm * 2 * Math.PI * m.rpm / 60 : null);
    return (
      <Box key={m.id} sx={{
        flex: '1 1 290px', maxWidth: 360, display: 'flex', flexDirection: 'column',
        p: 1.5, borderRadius: 2, border: '1px solid var(--line-soft)', bgcolor: 'var(--panel-2)',
        transition: 'border-color .15s, box-shadow .15s',
        '&:hover': { borderColor: '#334a6b', boxShadow: '0 0 0 1px var(--line-accent)55' },
      }}>
        {/* header: thumbnail + identity */}
        <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'center', mb: 1 }}>
          <Box sx={{ flexShrink: 0, lineHeight: 0 }}><MotorThumbnail motorId={m.id} thumbSvg={m.thumb_svg} slots={m.slots} poles={m.poles} size={84} /></Box>
          <Box sx={{ minWidth: 0, flex: 1 }}>
            <Typography sx={{ fontWeight: 700, color: 'var(--text-0)', fontSize: '0.86rem', lineHeight: 1.2, mb: 0.6 }}>{m.name}</Typography>
            <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
              <Chip size="small" label={`Ø ${m.diameter_mm} mm`} sx={{ height: 18, fontSize: '0.6rem', bgcolor: 'var(--panel-2)', color: '#60a5fa', fontWeight: 700 }} />
              <Chip size="small" label={`${m.slots}s / ${m.poles}p`} sx={{ height: 18, fontSize: '0.6rem', bgcolor: 'var(--panel-2)', color: 'var(--text-2)' }} />
              <Chip size="small" label={m.tier} sx={{ height: 18, fontSize: '0.58rem', textTransform: 'capitalize', bgcolor: `${tierColor[m.tier] ?? 'var(--line)'}22`, color: tierColor[m.tier] ?? 'var(--text-2)' }} />
            </Box>
          </Box>
        </Box>

        {/* headline stats — SAME three slots on every card */}
        <Box sx={{ display: 'flex', gap: 0.5, py: 1, my: 0.5, borderTop: '1px solid var(--line-soft)', borderBottom: '1px solid var(--line-soft)' }}>
          <Stat value={m.T_avg_Nm > 0 ? fmtT(m.T_avg_Nm) : '—'}
            unit={m.T_avg_Nm > 0 ? 'N·m' : undefined} label="torque" color="#86efac" />
          <Stat value={powerW != null ? fmtP(powerW) : '—'} label="power" color="#7dd3fc" />
          <Stat value={hasEff ? m.efficiency_pct!.toFixed(1) : '—'}
            unit={hasEff ? '%' : undefined} label="efficiency" color="#fcd34d" />
        </Box>

        {/* detail grid — SAME eight rows on every card; missing value = dash */}
        <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', columnGap: 1.5, rowGap: 0.35, mb: 1 }}>
          <Detail k="Speed" v={m.rpm > 0 ? `${m.rpm} rpm` : '—'} />
          <Detail k="Current" v={m.current_a > 0 ? `${Math.round(m.current_a)} A` : '—'} />
          <Detail k="Voltage" v={typeof m.voltage_pk_v === 'number' ? `${m.voltage_pk_v} V pk LL` : '—'} />
          <Detail k="Length" v={typeof m.length_mm === 'number' ? `${m.length_mm} mm` : '—'} />
          <Detail k="Magnet" v={m.magnet || '—'} />
          <Detail k="Steel" v={m.steel || '—'} />
          <Detail k="Wire" v={m.wire || '—'} />
          <Detail k="MTPA γ" v={typeof m.gamma_deg === 'number' ? `${m.gamma_deg}°` : '—'} />
          <Detail k="Mass" v={typeof m.mass_kg === 'number' ? `${m.mass_kg} kg` : '—'} />
          <Detail k="Torque/kg" v={typeof m.mass_kg === 'number' && m.mass_kg > 0 && m.T_avg_Nm > 0
            ? `${(m.T_avg_Nm / m.mass_kg).toFixed(1)} N·m/kg` : '—'} />
        </Box>

        <Box sx={{ flex: 1 }} />

        {/* action */}
        <Box sx={{ display: 'flex', gap: 0.5 }}>
          <Button size="small" fullWidth variant="outlined"
            startIcon={busy === m.id ? <CircularProgress size={12} /> : <BoltIcon sx={{ fontSize: 15 }} />}
            disabled={!!busy} onClick={() => loadMotor(m)}
            sx={{ textTransform: 'none', fontSize: '0.74rem', py: 0.4, flex: 1 }}>
            {needsSignIn ? 'Sign in to load' : 'Load into editor'}
          </Button>
          {m.preset && (isAdmin || m.owner === 'user') && (
            <Tooltip title="Rename">
              <span>
                <IconButton size="small" disabled={!!busy} onClick={() => openRename(m)}
                  sx={{ color: 'var(--text-2)', border: '1px solid var(--line)', borderRadius: 1,
                    '&:hover': { bgcolor: 'var(--panel)', borderColor: 'var(--text-3)' } }}>
                  <DriveFileRenameOutlineIcon sx={{ fontSize: 16 }} />
                </IconButton>
              </span>
            </Tooltip>
          )}
          {/* Same ownership rule as rename: your own motors are yours to
              delete; factory/catalog motors stay admin-only. The old
              admin-only gate hid the button entirely for a signed-out user —
              "I can't delete my motor" with nothing on screen saying why. */}
          {(isAdmin || m.owner === 'user') && (
            <Tooltip title={isAdmin ? 'Delete from catalog (admin)'
                                    : 'Delete this motor (yours)'}>
              <span>
                <IconButton size="small" disabled={!!busy} onClick={() => deleteMotor(m)}
                  sx={{ color: '#f87171', border: '1px solid #7f1d1d', borderRadius: 1,
                    '&:hover': { bgcolor: '#7f1d1d33', borderColor: '#ef4444' } }}>
                  <DeleteOutlineIcon sx={{ fontSize: 16 }} />
                </IconButton>
              </span>
            </Tooltip>
          )}
        </Box>
      </Box>
    );
  };

  return (
    <Box sx={{ p: 3, overflowY: 'auto', height: '100%' }}>
      <Typography variant="h5" sx={{ fontWeight: 800, color: 'var(--text-0)', mb: 0.5 }}>
        Motor Catalog
      </Typography>
      <Typography sx={{ color: 'var(--text-2)', mb: 3, fontSize: '0.9rem' }}>
        Proven motor designs for <b>aerospace, robotics, EV and marine</b> drivetrains. Pick one, tune it to your spec (stack length, winding, wire), see the price, and request manufacturing — click <b>Load</b> to open one as your own editable copy.
      </Typography>

      {/* ── User's saved designs ──────────────────────────────────── */}
      <MyDesigns />

      {/* ── Pricing tiers ─────────────────────────────────────────── */}
      <Box sx={{ display: 'flex', gap: 2, mb: 4, flexWrap: 'wrap' }}>
        {cat.tiers.map((t) => (
          <Box key={t.id} sx={{
            flex: '1 1 220px', maxWidth: 300, p: 2, borderRadius: 2,
            border: `1px solid ${t.highlight ? tierColor[t.id] : 'var(--panel)'}`,
            bgcolor: t.highlight ? 'var(--panel-2)' : 'var(--panel-2)',
            boxShadow: t.highlight ? `0 0 0 1px ${tierColor[t.id]}55` : 'none',
          }}>
            <Box sx={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
              <Typography sx={{ fontWeight: 700, color: tierColor[t.id] }}>{t.name}</Typography>
              <Typography sx={{ color: 'var(--text-0)', fontWeight: 700 }}>
                {t.price_usd === 0 ? 'Free' : `$${t.price_usd}/mo`}
              </Typography>
            </Box>
            <Typography sx={{ color: 'var(--text-3)', fontSize: '0.7rem', mb: 1 }}>{t.tagline}</Typography>
            {t.features.map((f, i) => (
              <Box key={i} sx={{ display: 'flex', gap: 0.5, alignItems: 'flex-start', mb: 0.25 }}>
                <CheckIcon sx={{ fontSize: 14, color: tierColor[t.id], mt: '2px' }} />
                <Typography sx={{ color: 'var(--text-1)', fontSize: '0.72rem' }}>{f}</Typography>
              </Box>
            ))}
            <Button size="small" fullWidth disabled={t.price_usd === 0}
              variant={t.highlight ? 'contained' : 'outlined'}
              sx={{ mt: 1.5, textTransform: 'none', fontSize: '0.72rem',
                ...(t.price_usd === 0 ? { color: 'var(--text-3)' } : {}) }}>
              {t.price_usd === 0 ? 'Current plan' : 'Upgrade (soon)'}
            </Button>
          </Box>
        ))}
      </Box>

      {/* ── Catalog by diameter (cards) ───────────────────────────── */}
      {cat.diameters_mm.map((d) => {
        const motors = byDiameter(d);
        return (
          <Box key={d} sx={{ mb: 3 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.25 }}>
              <Typography sx={{ fontWeight: 800, color: '#60a5fa', fontSize: '1rem' }}>
                Ø {d} mm
              </Typography>
              <Box sx={{ flex: 1, height: '1px', bgcolor: 'var(--panel)' }} />
              <Chip size="small" label={`${motors.length} motor${motors.length === 1 ? '' : 's'}`}
                sx={{ height: 18, fontSize: '0.62rem', bgcolor: 'var(--panel-2)', color: 'var(--text-3)' }} />
            </Box>
            {motors.length === 0 ? (
              <Typography sx={{ color: 'var(--text-4)', fontSize: '0.78rem', fontStyle: 'italic', pl: 1, py: 0.5 }}>
                — no motors yet (spec coming) —
              </Typography>
            ) : (
              <Box sx={{ display: 'flex', gap: 1.5, flexWrap: 'wrap', alignItems: 'stretch' }}>
                {motors.map(renderCard)}
              </Box>
            )}
          </Box>
        );
      })}

      {/* ── Rename dialog (window.prompt is unreliable — Chrome suppresses it) ── */}
      <Dialog open={!!renameTarget} onClose={() => setRenameTarget(null)}
        PaperProps={{ sx: { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 2, minWidth: 360 } }}>
        <DialogTitle sx={{ color: 'var(--text-0)', fontSize: '0.95rem', fontWeight: 700 }}>
          Rename motor
        </DialogTitle>
        <DialogContent>
          <TextField autoFocus fullWidth size="small" label="Name"
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') void submitRename(); }}
            error={!!renameErr} helperText={renameErr ?? ' '}
            sx={{ mt: 1 }} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRenameTarget(null)}
            sx={{ textTransform: 'none', color: 'var(--text-2)' }}>Cancel</Button>
          <Button variant="contained" disabled={!!busy || !renameValue.trim()}
            onClick={() => void submitRename()}
            sx={{ textTransform: 'none' }}>
            {busy ? <CircularProgress size={14} /> : 'Save'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* ── Delete confirm dialog (window.confirm is unreliable the same way) ── */}
      <Dialog open={!!deleteTarget} onClose={() => setDeleteTarget(null)}
        PaperProps={{ sx: { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 2, minWidth: 360 } }}>
        <DialogTitle sx={{ color: 'var(--text-0)', fontSize: '0.95rem', fontWeight: 700 }}>
          Delete "{deleteTarget?.name}"?
        </DialogTitle>
        <DialogContent>
          <Box sx={{ fontSize: 12.5, color: 'var(--text-2)' }}>
            This removes the card and its underlying preset. It cannot be undone.
          </Box>
          {deleteErr && (
            <Box sx={{ fontSize: 12, color: '#f87171', mt: 1 }}>{deleteErr}</Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteTarget(null)}
            sx={{ textTransform: 'none', color: 'var(--text-2)' }}>Cancel</Button>
          <Button variant="contained" color="error" disabled={!!busy}
            onClick={() => void submitDelete()}
            sx={{ textTransform: 'none' }}>
            {busy ? <CircularProgress size={14} /> : 'Delete'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default MotorsCatalog;
