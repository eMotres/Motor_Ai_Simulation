// "Where am I signed in" — the account's own sessions (GET /api/auth/sessions).
//
// Its real job is diagnostic: a browser profile that cannot keep localStorage
// (a preview pane, a private window) shows up here as a stream of sessions
// that are each seen once and never again — which looks nothing like a token
// that expired, and that difference was invisible until 2026-09-03.
import React, { useCallback, useEffect, useState } from 'react';
import {
  Dialog, DialogTitle, DialogContent, DialogActions, Button, Table, TableBody,
  TableCell, TableHead, TableRow, Typography, Tooltip, Chip, Box, CircularProgress,
} from '@mui/material';
import {
  listMySessions, revokeMySession, type SessionRow,
} from '../../lib/localAuth';
import { useTranslation } from 'react-i18next';
import { nsT } from '../../i18n/nsT';

// UI strings: locales/<lng>/common.json (docs/I18N.md).
const tx = nsT('common');

const when = (s?: number | null) =>
  (s ? new Date(s * 1000).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }) : '—');

/** "Chrome 140 · Windows" from a user-agent string, best effort. */
export function shortUA(ua: string): string {
  if (!ua) return 'unknown';
  const browser = /Edg\/(\d+)/.exec(ua) ? `Edge ${/Edg\/(\d+)/.exec(ua)![1]}`
    : /Chrome\/(\d+)/.exec(ua) ? `Chrome ${/Chrome\/(\d+)/.exec(ua)![1]}`
    : /Firefox\/(\d+)/.exec(ua) ? `Firefox ${/Firefox\/(\d+)/.exec(ua)![1]}`
    : /Version\/(\d+).*Safari/.exec(ua) ? `Safari ${/Version\/(\d+)/.exec(ua)![1]}`
    : 'browser';
  const os = /Windows/.test(ua) ? 'Windows' : /Mac OS X/.test(ua) ? 'macOS'
    : /Android/.test(ua) ? 'Android' : /(iPhone|iPad)/.test(ua) ? 'iOS'
    : /Linux/.test(ua) ? 'Linux' : '';
  return os ? `${browser} · ${os}` : browser;
}

const SessionsDialog: React.FC<{ open: boolean; onClose: () => void; onSignedOut: () => void }> =
  ({ open, onClose, onSignedOut }) => {
  useTranslation('common'); // re-render on language change; lazy-loads the namespace
    const [rows, setRows] = useState<SessionRow[]>([]);
    const [current, setCurrent] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState<string | null>(null);

    const load = useCallback(async () => {
      setBusy(true); setErr(null);
      try {
        const j = await listMySessions();
        setRows(j.sessions); setCurrent(j.current);
      } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
    }, []);
    useEffect(() => { if (open) void load(); }, [open, load]);

    const revoke = async (sid: string) => {
      setBusy(true);
      try {
        await revokeMySession(sid);
        if (sid === current) { onSignedOut(); onClose(); return; }
        await load();
      } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
    };

    const signOutEverywhere = async () => {
      setBusy(true);
      try {
        for (const r of rows.filter((x) => !x.revoked && x.sid !== current)) {
          await revokeMySession(r.sid).catch(() => {});
        }
        onSignedOut(); onClose();
      } finally { setBusy(false); }
    };

    return (
      <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
        <DialogTitle sx={{ fontSize: '1rem' }}>
          Signed in on {rows.filter((r) => !r.revoked).length} device(s)
        </DialogTitle>
        <DialogContent sx={{ pt: '8px !important' }}>
          {err && <Typography variant="caption" color="error">{err}</Typography>}
          {busy && <CircularProgress size={16} sx={{ ml: 1 }} />}
          <Table size="small" sx={{ '& td, & th': { fontSize: 12 } }}>
            <TableHead>
              <TableRow>
                <TableCell>{tx('browser')}</TableCell>
                <TableCell>{tx('signedIn')}</TableCell>
                <TableCell>{tx('lastSeen')}</TableCell>
                <TableCell>{tx('expires')}</TableCell>
                <TableCell>{tx('how')}</TableCell>
                <TableCell align="right" />
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.sid} hover sx={{ opacity: r.revoked ? 0.45 : 1 }}>
                  <TableCell>
                    <Tooltip title={`${r.userAgent || 'no user agent'} · ${r.ip || 'no ip'} · sid ${r.sid}`} arrow>
                      <span>{shortUA(r.userAgent)}</span>
                    </Tooltip>
                    {r.sid === current && (
                      <Chip label={tx('thisOne')} size="small" sx={{ ml: 0.75, height: 16, fontSize: 9 }} />
                    )}
                  </TableCell>
                  <TableCell>{when(r.created)}</TableCell>
                  <TableCell>{when(r.lastSeen)}</TableCell>
                  <TableCell>{when(r.expires)}</TableCell>
                  <TableCell>{r.loginMethod}</TableCell>
                  <TableCell align="right">
                    {r.revoked
                      ? <Typography sx={{ fontSize: 11, color: 'var(--text-4)' }}>{tx('revoked')}</Typography>
                      : (
                        <Button size="small" disabled={busy} onClick={() => void revoke(r.sid)}
                          sx={{ textTransform: 'none', fontSize: 11, minWidth: 0 }}>
                          {tx('revoke')}
                        </Button>
                      )}
                  </TableCell>
                </TableRow>
              ))}
              {rows.length === 0 && !busy && (
                <TableRow>
                  <TableCell colSpan={6} sx={{ textAlign: 'center', color: 'var(--text-4)', py: 2 }}>
                    {tx('noSessionsRecordedYet')}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </DialogContent>
        <DialogActions>
          <Box sx={{ flex: 1 }} />
          <Button onClick={() => void signOutEverywhere()} disabled={busy}
            sx={{ textTransform: 'none', color: '#f87171' }}>
            {tx('signOutEverywhere')}
          </Button>
          <Button onClick={onClose} sx={{ textTransform: 'none' }}>{tx('close')}</Button>
        </DialogActions>
      </Dialog>
    );
  };

export default SessionsDialog;
