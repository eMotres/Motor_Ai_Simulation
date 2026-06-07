import React, { useEffect, useState } from 'react';
import {
  Box, Typography, Chip, CircularProgress, Button, Tooltip,
  Table, TableBody, TableCell, TableHead, TableRow,
} from '@mui/material';
import BoltIcon from '@mui/icons-material/Bolt';
import CheckIcon from '@mui/icons-material/Check';
import { useMotorStore } from '../../stores/motorStore';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

interface Motor {
  id: string; diameter_mm: number; name: string; topology: string;
  slots: number; poles: number; rpm: number; current_a: number;
  T_avg_Nm: number; ripple_pct: number; gamma_deg: number;
  tier: string; description: string; preset?: string;
}
interface Tier {
  id: string; name: string; price_usd: number; highlight: boolean;
  tagline: string; features: string[];
}
interface Catalog { tiers: Tier[]; diameters_mm: number[]; motors: Motor[]; }

const tierColor: Record<string, string> = { free: '#10b981', pro: '#3b82f6', team: '#a855f7' };

const MotorsCatalog: React.FC = () => {
  const [cat, setCat] = useState<Catalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const setActiveTab = useMotorStore((s) => s.setActiveTab);

  useEffect(() => {
    fetch(`${API}/api/catalog`).then((r) => r.json()).then(setCat)
      .catch(() => setCat(null)).finally(() => setLoading(false));
  }, []);

  const loadMotor = async (m: Motor) => {
    setBusy(m.id);
    try {
      await fetch(`${API}/api/catalog/${m.id}/load`, { method: 'POST' });
      setActiveTab('geometry');
      window.location.reload();
    } catch { setBusy(null); }
  };

  if (loading) return <Box sx={{ p: 4 }}><CircularProgress size={22} /></Box>;
  if (!cat) return <Box sx={{ p: 4, color: '#f87171' }}>Catalog unavailable (backend offline).</Box>;

  const byDiameter = (d: number) => cat.motors.filter((m) => m.diameter_mm === d);

  return (
    <Box sx={{ p: 3, overflowY: 'auto', height: '100%' }}>
      <Typography variant="h5" sx={{ fontWeight: 800, color: '#e2e8f0', mb: 0.5 }}>
        Motor Catalog
      </Typography>
      <Typography sx={{ color: '#94a3b8', mb: 3, fontSize: '0.9rem' }}>
        Ready-made designs by stator diameter. Click <b>Load</b> to open one in the editor.
      </Typography>

      {/* ── Pricing tiers ─────────────────────────────────────────── */}
      <Box sx={{ display: 'flex', gap: 2, mb: 4, flexWrap: 'wrap' }}>
        {cat.tiers.map((t) => (
          <Box key={t.id} sx={{
            flex: '1 1 220px', maxWidth: 300, p: 2, borderRadius: 2,
            border: `1px solid ${t.highlight ? tierColor[t.id] : '#1e293b'}`,
            bgcolor: t.highlight ? '#0f1d33' : '#0b1220',
            boxShadow: t.highlight ? `0 0 0 1px ${tierColor[t.id]}55` : 'none',
          }}>
            <Box sx={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
              <Typography sx={{ fontWeight: 700, color: tierColor[t.id] }}>{t.name}</Typography>
              <Typography sx={{ color: '#e2e8f0', fontWeight: 700 }}>
                {t.price_usd === 0 ? 'Free' : `$${t.price_usd}/mo`}
              </Typography>
            </Box>
            <Typography sx={{ color: '#64748b', fontSize: '0.7rem', mb: 1 }}>{t.tagline}</Typography>
            {t.features.map((f, i) => (
              <Box key={i} sx={{ display: 'flex', gap: 0.5, alignItems: 'flex-start', mb: 0.25 }}>
                <CheckIcon sx={{ fontSize: 14, color: tierColor[t.id], mt: '2px' }} />
                <Typography sx={{ color: '#cbd5e1', fontSize: '0.72rem' }}>{f}</Typography>
              </Box>
            ))}
            <Button size="small" fullWidth disabled={t.price_usd === 0}
              variant={t.highlight ? 'contained' : 'outlined'}
              sx={{ mt: 1.5, textTransform: 'none', fontSize: '0.72rem',
                ...(t.price_usd === 0 ? { color: '#64748b' } : {}) }}>
              {t.price_usd === 0 ? 'Current plan' : 'Upgrade (soon)'}
            </Button>
          </Box>
        ))}
      </Box>

      {/* ── Catalog by diameter ───────────────────────────────────── */}
      {cat.diameters_mm.map((d) => {
        const motors = byDiameter(d);
        return (
          <Box key={d} sx={{ mb: 2.5 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
              <Typography sx={{ fontWeight: 800, color: '#60a5fa', fontSize: '1rem' }}>
                Ø {d} mm
              </Typography>
              <Box sx={{ flex: 1, height: '1px', bgcolor: '#1e293b' }} />
              <Chip size="small" label={`${motors.length} motor${motors.length === 1 ? '' : 's'}`}
                sx={{ height: 18, fontSize: '0.62rem', bgcolor: '#111827', color: '#64748b' }} />
            </Box>
            {motors.length === 0 ? (
              <Typography sx={{ color: '#475569', fontSize: '0.78rem', fontStyle: 'italic', pl: 1, py: 0.5 }}>
                — no motors yet (spec coming) —
              </Typography>
            ) : (
              <Table size="small" sx={{
                '& td, & th': { borderColor: '#1e293b', py: 0.6, fontSize: '0.78rem', color: '#cbd5e1' },
                '& th': { color: '#64748b', fontWeight: 600, fontSize: '0.65rem', textTransform: 'uppercase' },
              }}>
                <TableHead>
                  <TableRow>
                    <TableCell>Motor</TableCell><TableCell>Topology</TableCell>
                    <TableCell align="center">Slots/Poles</TableCell><TableCell align="center">RPM</TableCell>
                    <TableCell align="center">Torque</TableCell><TableCell align="center">Ripple</TableCell>
                    <TableCell align="center">Plan</TableCell><TableCell align="right"></TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {motors.map((m) => (
                    <TableRow key={m.id} hover sx={{ '&:hover': { bgcolor: '#111827' } }}>
                      <TableCell>
                        <Tooltip title={m.description} placement="top" arrow>
                          <Box>
                            <Typography sx={{ fontWeight: 600, color: '#e2e8f0', fontSize: '0.8rem' }}>{m.name}</Typography>
                          </Box>
                        </Tooltip>
                      </TableCell>
                      <TableCell sx={{ color: '#94a3b8' }}>{m.topology}</TableCell>
                      <TableCell align="center">{m.slots}/{m.poles}</TableCell>
                      <TableCell align="center">{m.rpm}</TableCell>
                      <TableCell align="center" sx={{ color: '#86efac' }}>{m.T_avg_Nm?.toFixed(1)} N·m</TableCell>
                      <TableCell align="center" sx={{ color: m.ripple_pct < 8 ? '#86efac' : '#fdba74' }}>
                        {m.ripple_pct?.toFixed(1)}%
                      </TableCell>
                      <TableCell align="center">
                        <Chip size="small" label={m.tier}
                          sx={{ height: 18, fontSize: '0.6rem', textTransform: 'capitalize',
                            bgcolor: `${tierColor[m.tier] ?? '#334155'}22`, color: tierColor[m.tier] ?? '#94a3b8' }} />
                      </TableCell>
                      <TableCell align="right">
                        <Button size="small" variant="outlined" startIcon={busy === m.id ? <CircularProgress size={12} /> : <BoltIcon sx={{ fontSize: 14 }} />}
                          disabled={!!busy} onClick={() => loadMotor(m)}
                          sx={{ textTransform: 'none', fontSize: '0.72rem', py: 0.2 }}>
                          Load
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Box>
        );
      })}
    </Box>
  );
};

export default MotorsCatalog;
