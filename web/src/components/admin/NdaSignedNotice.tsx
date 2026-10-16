/**
 * Admin · NDA signed online — one row per click-through signature (company, user, country, version), newest first, with the
 * signed PDF.  A free-mail address (gmail, outlook, qq, 163, yahoo, ...) is flagged, never blocked.  Revoking is the NDA
 * switch in the user drawer; the signature record itself is never deleted.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Box, Chip, Link, Paper, Typography } from '@mui/material';
import HelpTip from '../common/HelpTip';
import { openNdaPdf } from './ndaPdf';

const API = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001') as string;

interface Sig {
  id: string; iso: string; user: string; email: string; company: string; country: string; signatory: string; title: string;
  version: string; free_mail: boolean; data_sharing?: { consent: boolean }; data_sharing_now?: boolean;
}

const NdaSignedNotice: React.FC = () => {
  const [rows, setRows] = useState<Sig[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [consenting, setConsenting] = useState(0);
  const load = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/nda/signatures?limit=50`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = (await r.json()) as { signatures: Sig[]; consenting_users?: number };
      setRows(j.signatures); setConsenting(j.consenting_users ?? 0); setErr(null);
    } catch (e) { setErr((e as Error).message); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  return (
    <Paper sx={{ bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, p: 1.5, mb: 2 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, mb: 1 }}>
        <Typography sx={{ fontSize: 10, color: 'var(--text-3)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
          NDA signed online ({rows?.length ?? 0})
        </Typography>
        <Chip size="small" data-testid="consenting-count" label={`${consenting} sharing data with the dataset`} sx={{ height: 18, fontSize: 10, bgcolor: 'var(--panel)' }} />
        <HelpTip title="Click-through NDA signatures, newest first. A free-mail address is flagged for a look, not blocked. To revoke, switch the NDA off in the user drawer; the record stays." />
      </Box>
      {err && <Typography sx={{ fontSize: 12, color: '#f87171' }}>{err}</Typography>}
      {rows && !rows.length && <Typography sx={{ fontSize: 12, color: 'var(--text-4)' }}>No online signatures yet.</Typography>}
      {(rows ?? []).slice(0, 10).map((s) => (
        <Box key={s.id} sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 0.5, borderTop: '1px solid var(--line-soft)', flexWrap: 'wrap' }}>
          <Typography sx={{ fontSize: 12.5, color: 'var(--text-0)', fontWeight: 700 }}>{s.company}</Typography>
          <Typography sx={{ fontSize: 12, color: 'var(--text-2)' }}>{s.signatory}, {s.email}</Typography>
          <Chip size="small" label={s.country} sx={{ height: 18, fontSize: 10, bgcolor: 'var(--panel)' }} />
          <Chip size="small" label={s.data_sharing_now ? 'dataset: shares' : 'dataset: no'} sx={{ height: 18, fontSize: 10, bgcolor: 'var(--panel)', color: s.data_sharing_now ? '#4ade80' : 'var(--text-4)' }} />
          {s.free_mail && <Chip size="small" label="free-mail domain" sx={{ height: 18, fontSize: 10, bgcolor: 'var(--panel)', color: '#fbbf24' }} />}
          <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>{s.version} · {s.iso.replace('T', ' ').replace('Z', ' UTC')}</Typography>
          <Link component="button" underline="hover" onClick={() => void openNdaPdf(s.id)} sx={{ fontSize: 11.5, ml: 'auto' }}>PDF</Link>
        </Box>
      ))}
    </Paper>
  );
};

export default NdaSignedNotice;
