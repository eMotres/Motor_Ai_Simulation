import React, { useEffect, useState } from 'react';
import {
  Box, Typography, Chip, CircularProgress, Button, Tooltip, IconButton,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField,
} from '@mui/material';
import DriveFileRenameOutlineIcon from '@mui/icons-material/DriveFileRenameOutline';
import LockIcon from '@mui/icons-material/Lock';
import LockOpenIcon from '@mui/icons-material/LockOpen';
import { useUIStore } from '../../stores/motorStore';
import { useAuth } from '../../contexts/AuthContext';
import MyDesigns from './MyDesigns';
import MotorThumbnail from './MotorThumbnail';
import { openMotor } from '../common/motorSettings';
import HelpTip from '../common/HelpTip';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

interface Motor {
  id: string; diameter_mm: number; name: string; topology: string;
  slots: number; poles: number; rpm: number; current_a: number;
  T_avg_Nm: number; ripple_pct: number | null; gamma_deg: number;
  tier: string; description: string; preset?: string;
  // Who this motor belongs to (an account, or "admin" for everything created
  // before ownership existed).  Only the owner or an admin may write it.
  owner?: string;
  // Admin lock: read-only for EVERYONE but an admin — the owner included.
  locked?: boolean;
  // Computed per request for the CALLER: may you rename / delete this motor?
  can_write?: boolean;
  thumb_svg?: string;   // inline real-geometry cross-section (user-saved motors)
  // enriched (optional) — shown when present
  power_w?: number; efficiency_pct?: number; voltage_pk_v?: number;
  magnet?: string; steel?: string; length_mm?: number; wire?: string;
  mass_kg?: number;     // active mass (stator+Cu+magnets+rotor+shaft)
  // When this motor was last written (tz-aware ISO-8601 UTC).  ABSENT on motors
  // saved before the field existed — those show "—", never a fabricated time.
  saved_at?: string;
  // Computed per request by the backend: this card's geometry IS the geometry
  // loaded in the editor right now (compared by stamp, not by name).  None
  // active is a legitimate state — it means the geometry was edited since the
  // last save and matches nothing saved.
  is_active?: boolean;
}
interface Tier {
  id: string; name: string; price_usd: number; highlight: boolean;
  tagline: string; features: string[];
}
interface Catalog { tiers: Tier[]; diameters_mm: number[]; motors: Motor[]; }

const fmtT = (t: number) => (t >= 10 ? t.toFixed(1) : t.toFixed(2));

// The colour that means "this is the machine in your editor" — the same accent
// the highlighted pricing tier uses, so the card reads as emphasised, not as
// a different kind of card.
const ACTIVE = '#60a5fa';
// The colour of "protected" — the padlock chip and the admin's lock toggle.
const LOCKED = '#fbbf24';

/** "2 h ago" for an ISO instant, floored (never rounded UP into a time that has
 *  not happened yet).  `now` is passed in so the label re-renders on the clock
 *  tick instead of freezing at whatever it said when the page was opened.
 *  Returns null for anything unparseable — a bad stamp shows as no stamp. */
function relTime(iso: string, now: number): string | null {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return null;
  // A stamp slightly in the future (server/browser clock skew) is not "in -3
  // minutes"; it is as recent as it gets.
  const s = Math.max(0, Math.floor((now - t) / 1000));
  if (s < 60) return 'just now';
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(s / 3600);
  if (h < 24) return `${h} h ago`;
  const d = Math.floor(s / 86400);
  if (d < 31) return `${d} d ago`;
  const mo = Math.floor(d / 30);
  return mo < 12 ? `${mo} mo ago` : `${Math.floor(d / 365)} y ago`;
}

/** Exact local wall-clock time behind the relative label. */
function exactLocal(iso: string): string {
  const t = new Date(iso);
  return Number.isNaN(t.getTime()) ? iso : t.toLocaleString();
}

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

  // The "saved N ago" labels are relative to NOW, so they rot while the tab sits
  // open — a card that said "just now" an hour ago is simply wrong.  One clock
  // for the whole list, ticking every 60 s (the finest granularity any label
  // shows), re-renders every label without refetching anything.
  const [nowTs, setNowTs] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNowTs(Date.now()), 60_000);
    return () => clearInterval(id);
  }, []);

  // `is_active` is a fact about the editor, computed server-side per request.
  // Loading a motor from a card reloads the page (so the highlight moves by
  // remount), but the active motor can also change from elsewhere in the app —
  // that path announces itself with `motor:active-changed`, so listen and
  // refetch rather than showing a highlight that has moved on.
  useEffect(() => {
    const onActiveChanged = () => { void load(); };
    window.addEventListener('motor:active-changed', onActiveChanged);
    // Applying a design from the Optimization tab files it here as a new motor
    // (lib/appliedAutoSave).  The card that card-line promises ("saved as X ·
    // Motors") has to actually be here when the user looks, tab mounted or not.
    window.addEventListener('applied-design-saved', onActiveChanged);
    return () => {
      window.removeEventListener('motor:active-changed', onActiveChanged);
      window.removeEventListener('applied-design-saved', onActiveChanged);
    };
  }, []);

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

  // Lock / unlock — admin only (the backend enforces it with require_admin;
  // this just keeps the control out of everyone else's way).
  const [lockErr, setLockErr] = useState<{ id: string; msg: string } | null>(null);
  const toggleLock = async (m: Motor) => {
    if (!m.preset) return;
    setBusy(m.id); setLockErr(null);
    try {
      const r = await fetch(`${API}/api/presets/${encodeURIComponent(m.preset)}/lock`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ locked: !m.locked }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        setLockErr({ id: m.id, msg: String((j as any).detail || `HTTP ${r.status}`) });
        return;
      }
      await load();
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

  // One motor = ONE table row, same table style as the Families duty tables —
  // the whole tab reads as one system.  Missing values render as a dash.
  const renderRow = (m: Motor) => {
    const powerW = typeof m.power_w === 'number'
      ? m.power_w
      : (m.T_avg_Nm > 0 && m.rpm > 0 ? m.T_avg_Nm * 2 * Math.PI * m.rpm / 60 : null);
    const active = m.is_active === true;
    const rel = m.saved_at ? relTime(m.saved_at, nowTs) : null;
    const writable = m.can_write !== false;
    const cannotWhy = m.locked
      ? `Locked by an admin — ask an admin to unlock it (owner: ${m.owner ?? '—'}).`
      : `This motor belongs to ${m.owner ?? 'another account'} — ask them or an admin.`;
    const dash = '—';
    const num = (v: number | null | undefined, digits = 1, ok = true) =>
      ok && v != null && Number.isFinite(Number(v))
        ? Number(v).toFixed(digits).replace(/\.0+$/, '') : dash;
    return (
      <tr key={m.id} style={active ? { background: `${ACTIVE}14` } : undefined}>
        <td>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.8, minWidth: 0 }}>
            <Box sx={{ flexShrink: 0, lineHeight: 0 }}>
              <MotorThumbnail motorId={m.id} thumbSvg={m.thumb_svg}
                slots={m.slots} poles={m.poles} size={34} />
            </Box>
            <Tooltip placement="top-start" title={<>
              {m.slots}s/{m.poles}p · {m.wire ? `wire ${m.wire} · ` : ''}
              {typeof m.gamma_deg === 'number' ? `γ=${m.gamma_deg}° · ` : ''}
              {rel ? `saved ${rel} (${exactLocal(m.saved_at!)})` : 'no recorded save time'}
              <br />Owner: {m.owner ?? '—'}
              {m.locked ? ' · locked (admin only)'
                        : (m.can_write === false ? ' · read-only for you' : '')}
            </>}>
              <span style={{ fontWeight: 600, color: 'var(--text-1)', cursor: 'help',
                             overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {m.name}
              </span>
            </Tooltip>
            {active && (
              <Chip size="small" label="in editor"
                sx={{ height: 16, fontSize: '0.56rem', fontWeight: 700,
                      bgcolor: `${ACTIVE}22`, color: ACTIVE }} />
            )}
            {m.locked && (
              <LockIcon sx={{ fontSize: 12, color: LOCKED, flexShrink: 0 }} />
            )}
          </Box>
        </td>
        <td>{num(powerW != null ? powerW / 1000 : null)}</td>
        <td>{m.T_avg_Nm > 0 ? fmtT(m.T_avg_Nm) : dash}</td>
        <td>{num(m.rpm, 0, m.rpm > 0)}</td>
        <td>{num(m.current_a, 0, m.current_a > 0)}</td>
        <td>{num(m.voltage_pk_v, 0)}</td>
        <td>{num(m.efficiency_pct, 1)}</td>
        <td>{num(m.ripple_pct)}</td>
        <td>{num(m.mass_kg, 2)}</td>
        <td>{typeof m.mass_kg === 'number' && m.mass_kg > 0 && m.T_avg_Nm > 0
             ? (m.T_avg_Nm / m.mass_kg).toFixed(1) : dash}</td>
        <td>{num(m.length_mm, 0)}</td>
        <td>{m.magnet || dash}</td>
        <td>{m.steel || dash}</td>
        <td style={{ textAlign: 'center' }}>
          <Tooltip title={needsSignIn ? 'Sign in to load' : 'Load into editor'}>
            <span>
              <IconButton size="small" disabled={!!busy} onClick={() => loadMotor(m)}
                sx={{ fontSize: 12, p: 0.2, color: '#34d399' }}>
                {busy === m.id ? <CircularProgress size={11} /> : '▶'}
              </IconButton>
            </span>
          </Tooltip>
          {m.preset && (
            <Tooltip title={writable ? 'Rename' : cannotWhy}>
              <span>
                <IconButton size="small" disabled={!!busy || !writable}
                  onClick={() => openRename(m)}
                  sx={{ p: 0.2, color: 'var(--text-3)' }}>
                  <DriveFileRenameOutlineIcon sx={{ fontSize: 14 }} />
                </IconButton>
              </span>
            </Tooltip>
          )}
          <Tooltip title={writable
            ? (isAdmin ? 'Delete from catalog (admin)' : 'Delete this motor (yours)')
            : cannotWhy}>
            <span>
              <IconButton size="small" disabled={!!busy || !writable}
                onClick={() => deleteMotor(m)}
                sx={{ fontSize: 12, p: 0.2, color: 'var(--text-4)' }}>✕</IconButton>
            </span>
          </Tooltip>
          {isAdmin && m.preset && (
            <Tooltip title={m.locked
              ? `Unlock — restore write access to ${m.owner ?? 'its owner'}`
              : `Lock — make this read-only for everyone but an admin (owner: ${m.owner ?? '—'})`}>
              <span>
                <IconButton size="small" disabled={!!busy} onClick={() => void toggleLock(m)}
                  sx={{ p: 0.2, color: m.locked ? LOCKED : 'var(--text-3)' }}>
                  {m.locked ? <LockIcon sx={{ fontSize: 14 }} />
                            : <LockOpenIcon sx={{ fontSize: 14 }} />}
                </IconButton>
              </span>
            </Tooltip>
          )}
          {lockErr?.id === m.id && (
            <Box sx={{ fontSize: 10, color: '#f87171' }}>{lockErr.msg}</Box>
          )}
        </td>
      </tr>
    );
  };

  return (
    <Box>
      {/* ── User's saved designs ──────────────────────────────────── */}
      <MyDesigns />

      {/* ── Catalog by diameter (cards) — ONE section, same style as the
             Families and My Motors panels above it ─────────────────── */}
      <Box sx={{ p: 2, borderRadius: 2, border: '1px solid var(--line-soft)',
                 bgcolor: 'var(--panel-2)' }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mb: 1.5 }}>
        <Typography sx={{ fontSize: 14, fontWeight: 700, color: 'var(--text-1)' }}>
          Motor catalog
        </Typography>
        <HelpTip title="Proven motor designs — aerospace, robotics, EV, marine. Pick one, tune it to your spec (stack length, winding, wire), see the price, and request manufacturing — click Load to open one as your own editable copy." />
      </Box>
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
              <Box component="table" sx={{
                width: '100%', borderCollapse: 'collapse',
                '& th': { fontSize: 10, fontWeight: 600, color: 'var(--text-4)',
                          textAlign: 'right', p: '2px 6px', whiteSpace: 'nowrap' },
                '& td': { fontSize: 11.5, color: 'var(--text-2)', textAlign: 'right',
                          p: '2px 6px', whiteSpace: 'nowrap',
                          fontVariantNumeric: 'tabular-nums',
                          borderTop: '1px solid var(--panel)' },
                '& th:first-of-type, & td:first-of-type': { textAlign: 'left' },
              }}>
                <thead>
                  <tr>
                    <th>motor</th><th>kW</th><th>Nm</th><th>rpm</th>
                    <th>A</th><th>V L-L</th><th>η %</th><th>ripple %</th>
                    <th>kg</th><th>Nm/kg</th><th>L mm</th>
                    <th>magnet</th><th>steel</th>
                    <th style={{ textAlign: 'center' }} />
                  </tr>
                </thead>
                <tbody>{motors.map(renderRow)}</tbody>
              </Box>
            )}
          </Box>
        );
      })}
      </Box>

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
