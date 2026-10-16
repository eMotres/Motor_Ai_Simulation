// "My Motors" — the signed-in user's saved designs (Firestore users/{uid}/designs).
// Save snapshots the current backend geometry+operating point; Load pushes a saved
// design back. Hidden entirely when auth is disabled; prompts sign-in when logged out.
import React, { useCallback, useEffect, useState } from 'react';
import {
  Box, Typography, Button, IconButton, CircularProgress, Tooltip,
  Table, TableBody, TableCell, TableHead, TableRow, TextField,
} from '@mui/material';
import SaveIcon from '@mui/icons-material/Save';
import BoltIcon from '@mui/icons-material/Bolt';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import { useAuth } from '../../contexts/AuthContext';
import { useUIStore } from '../../stores/motorStore';
import {
  listDesigns, saveCurrentDesign, deleteDesign, applyDesign, type SavedDesign,
} from '../../lib/designs';

const MyDesigns: React.FC = () => {
  const { enabled, user } = useAuth();
  const setActiveTab = useUIStore((s) => s.setActiveTab);
  const [designs, setDesigns] = useState<SavedDesign[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(async (uid: string) => {
    setLoading(true);
    try { setDesigns(await listDesigns(uid)); setErr(null); }
    catch (e) { setErr((e as Error).message); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { if (user) refresh(user.uid); else setDesigns([]); }, [user, refresh]);

  if (!enabled) return null; // auth not configured → no saved-designs feature

  // Signed out → render nothing; the header already has a Sign-in button.
  if (!user) return null;

  const save = async () => {
    if (!user) return;
    setBusy('__save__');
    try {
      await saveCurrentDesign(user.uid, name.trim() || `Design ${designs.length + 1}`);
      setName('');
      await refresh(user.uid);
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(null); }
  };

  const load = async (d: SavedDesign) => {
    setBusy(d.id);
    try { await applyDesign(d); setActiveTab('geometry'); window.location.reload(); }
    catch (e) { setErr((e as Error).message); setBusy(null); }
  };

  const remove = async (d: SavedDesign) => {
    if (!user) return;
    setBusy(d.id);
    try { await deleteDesign(user.uid, d.id); await refresh(user.uid); }
    catch (e) { setErr((e as Error).message); }
    finally { setBusy(null); }
  };

  return (
    <Box sx={{ mb: 2, p: 2, borderRadius: 2, border: '1px solid var(--line-soft)', bgcolor: 'var(--panel-2)' }}>
      <Typography sx={{ fontSize: 14, fontWeight: 700, color: 'var(--text-1)', mb: 1 }}>My Motors</Typography>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1.5, flexWrap: 'wrap' }}>
        <TextField size="small" placeholder="Name this design…" value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') save(); }}
          sx={{ minWidth: 200, '& .MuiInputBase-input': { fontSize: '0.8rem', py: 0.6 } }} />
        <Button size="small" variant="contained" onClick={save} disabled={busy === '__save__'}
          startIcon={busy === '__save__' ? <CircularProgress size={12} /> : <SaveIcon sx={{ fontSize: 16 }} />}
          sx={{ textTransform: 'none', fontSize: '0.75rem' }}>
          Save current
        </Button>
      </Box>

      {err && <Typography sx={{ color: '#f87171', fontSize: '0.72rem', mb: 1 }}>{err}</Typography>}

      {loading ? (
        <CircularProgress size={18} />
      ) : designs.length === 0 ? (
        <Typography sx={{ color: 'var(--text-4)', fontSize: '0.78rem', fontStyle: 'italic' }}>
          No saved designs yet. Tune a motor in the editor, then click <b>Save current</b>.
        </Typography>
      ) : (
        <Table size="small" sx={{
          '& td, & th': { borderColor: 'var(--panel)', py: 0.6, fontSize: '0.78rem', color: 'var(--text-1)' },
          '& th': { color: 'var(--text-3)', fontWeight: 600, fontSize: '0.65rem', textTransform: 'uppercase' },
        }}>
          <TableHead>
            <TableRow>
              <TableCell>Design</TableCell>
              <TableCell align="center">Torque</TableCell>
              <TableCell align="center">Ripple</TableCell>
              <TableCell align="right"></TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {designs.map((d) => (
              <TableRow key={d.id} hover sx={{ '&:hover': { bgcolor: 'var(--panel-2)' } }}>
                <TableCell sx={{ fontWeight: 600, color: 'var(--text-0)' }}>{d.name}</TableCell>
                <TableCell align="center" sx={{ color: '#86efac' }}>
                  {d.metrics?.T_avg_Nm != null ? `${d.metrics.T_avg_Nm.toFixed(1)} N·m` : '—'}
                </TableCell>
                <TableCell align="center" sx={{ color: '#fdba74' }}>
                  {d.metrics?.ripple_pct != null ? `${d.metrics.ripple_pct.toFixed(1)}%` : '—'}
                </TableCell>
                <TableCell align="right">
                  <Button size="small" variant="outlined" disabled={!!busy} onClick={() => load(d)}
                    startIcon={busy === d.id ? <CircularProgress size={12} /> : <BoltIcon sx={{ fontSize: 14 }} />}
                    sx={{ textTransform: 'none', fontSize: '0.72rem', py: 0.2, mr: 0.5 }}>
                    Load
                  </Button>
                  <Tooltip title="Delete" arrow>
                    <span>
                      <IconButton size="small" disabled={!!busy} onClick={() => remove(d)}
                        sx={{ color: 'var(--text-3)', '&:hover': { color: '#f87171' } }}>
                        <DeleteOutlineIcon sx={{ fontSize: 16 }} />
                      </IconButton>
                    </span>
                  </Tooltip>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Box>
  );
};

export default MyDesigns;
