/**
 * PassportManager — admin "References & passports" section.
 *
 * Lists the catalog motors and lets an admin GENERATE (or regenerate) each
 * motor's FEM passport via POST /api/catalog/{id}/passport.  Once a motor has a
 * passport it appears in the Configurator's REFERENCE dropdown and is scaled
 * instantly (no FEM) — this is the publish step of the
 * geometry → simulation → passport → configurator pipeline.
 *
 * Generation runs a FEM sweep (minutes) synchronously; "coarse" uses fewer
 * steps/rpm points for a quick check.  NB: a full run can exceed a serverless
 * request timeout in production — prefer coarse there until generation is async.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  Box, Typography, Paper, Button, CircularProgress, Chip,
  Table, TableBody, TableCell, TableHead, TableRow, FormControlLabel, Checkbox,
} from '@mui/material';
import BoltIcon from '@mui/icons-material/Bolt';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;
const PANEL = { bgcolor: '#0b1424', border: '1px solid #1e293b', borderRadius: 1.5 } as const;

interface CatMotor {
  id: string; name?: string; diameter_mm?: number; preset?: string;
  passport?: { passport?: { T0_Nm?: number; rpm0?: number; speed?: { rpm?: number[] } } };
}

const PassportManager: React.FC = () => {
  const [motors, setMotors] = useState<CatMotor[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [coarse, setCoarse] = useState(true);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(`${API}/api/catalog`, { cache: 'no-store' });
      const c = await r.json();
      setMotors(c.motors || []);
    } catch { /* keep prior */ } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const generate = async (m: CatMotor) => {
    setBusy(m.id); setMsg(null);
    try {
      const r = await fetch(`${API}/api/catalog/${m.id}/passport?coarse=${coarse}`, { method: 'POST' });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 200)}`);
      const d = await r.json();
      const t0 = d?.passport?.passport?.T0_Nm;
      setMsg(`✓ ${m.name ?? m.id}: passport generated (T0 ≈ ${t0} N·m)`);
      await load();
    } catch (e) {
      setMsg(`✗ ${m.name ?? m.id}: ${e instanceof Error ? e.message : String(e)}`);
    } finally { setBusy(null); }
  };

  const done = motors.filter((m) => m.passport?.passport).length;

  return (
    <>
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, mt: 3, mb: 1, flexWrap: 'wrap' }}>
        <Typography sx={{ fontSize: 15, fontWeight: 800, color: '#e2e8f0' }}>References &amp; passports</Typography>
        <Typography sx={{ fontSize: 11, color: '#64748b' }}>{done}/{motors.length} characterised</Typography>
        <Box sx={{ flex: 1 }} />
        <FormControlLabel
          control={<Checkbox size="small" checked={coarse} onChange={(e) => setCoarse(e.target.checked)} sx={{ color: '#475569', '&.Mui-checked': { color: '#60a5fa' } }} />}
          label={<Typography sx={{ fontSize: 11, color: '#94a3b8' }}>coarse (fast)</Typography>} />
      </Box>
      <Typography sx={{ fontSize: 11, color: '#475569', mb: 1 }}>
        Generating a passport runs a FEM sweep on the motor (minutes) and publishes it to the catalog, so the
        Configurator can scale it instantly. Switches the active motor while it runs.
      </Typography>
      {msg && (
        <Typography sx={{ fontSize: 12, color: msg.startsWith('✓') ? '#4ade80' : '#f87171', mb: 1 }}>{msg}</Typography>
      )}
      <Paper sx={{ ...PANEL, p: 0, overflow: 'hidden' }}>
        <Table size="small" sx={{
          '& td, & th': { borderColor: '#1e293b', fontSize: 12.5 },
          '& th': { color: '#64748b', fontWeight: 700, textTransform: 'uppercase', fontSize: 10, letterSpacing: '0.04em' },
        }}>
          <TableHead>
            <TableRow>
              <TableCell>Motor</TableCell>
              <TableCell align="right">Ø mm</TableCell>
              <TableCell>Passport</TableCell>
              <TableCell align="right">Action</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {motors.map((m) => {
              const p = m.passport?.passport;
              return (
                <TableRow key={m.id} hover>
                  <TableCell><Typography sx={{ fontSize: 13, color: '#e2e8f0', fontWeight: 600 }}>{m.name ?? m.id}</Typography></TableCell>
                  <TableCell align="right" sx={{ color: '#94a3b8' }}>{m.diameter_mm ?? '—'}</TableCell>
                  <TableCell>
                    {p
                      ? <Chip size="small" label={`T0 ${Number(p.T0_Nm ?? 0).toFixed((p.T0_Nm ?? 0) < 10 ? 1 : 0)} N·m · ${p.speed?.rpm?.length ?? 0} rpm pts`}
                          sx={{ height: 18, fontSize: 9.5, bgcolor: '#052e16', color: '#4ade80' }} />
                      : <Chip size="small" label="none" sx={{ height: 18, fontSize: 9.5, bgcolor: '#1e293b', color: '#64748b' }} />}
                  </TableCell>
                  <TableCell align="right">
                    <Button size="small" disabled={!!busy} onClick={() => void generate(m)}
                      startIcon={busy === m.id ? <CircularProgress size={12} /> : <BoltIcon sx={{ fontSize: 14 }} />}
                      sx={{ textTransform: 'none', fontSize: 11, color: '#60a5fa', minWidth: 0 }}>
                      {busy === m.id ? 'Generating…' : (p ? 'Regenerate' : 'Generate')}
                    </Button>
                  </TableCell>
                </TableRow>
              );
            })}
            {!loading && motors.length === 0 && (
              <TableRow><TableCell colSpan={4} sx={{ color: '#475569', textAlign: 'center', py: 3 }}>No catalog motors.</TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </Paper>
    </>
  );
};

export default PassportManager;
