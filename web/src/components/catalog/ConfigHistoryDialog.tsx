// Configuration history — the snapshots `_save_yaml` keeps under
// config/dies/.history/<die>/, listed with what a restore would CHANGE, and a
// button to put one back (user 2026-09-12: "история сохранённых параметров по
// каждой конфигурации, чтобы в любой момент можно было откатиться").
//
// One short line per snapshot, the diff behind it on hover / expand — no text
// walls (project rule).  A restore is undoable: the replaced version is
// snapshotted first by the same writer, so the newest row after a restore is
// the "before".
import React, { useEffect, useState } from 'react';
import {
  Box, Button, Dialog, DialogTitle, DialogContent, DialogActions,
  Typography, CircularProgress, Tooltip,
} from '@mui/material';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

interface Snapshot {
  stamp: string; taken: string; note: string; size: number;
  diff_vs_now: string[];
}

const ConfigHistoryDialog: React.FC<{
  open: boolean; onClose: () => void;
  die: string; config: string | null;          // null = the die's base geometry
  canWrite: boolean;
  onRestored?: () => void;
}> = ({ open, onClose, die, config, canWrite, onRestored }) => {
  const [rows, setRows] = useState<Snapshot[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [openStamp, setOpenStamp] = useState<string | null>(null);

  const load = async () => {
    setRows(null); setErr(null);
    try {
      const q = config != null ? `?config=${encodeURIComponent(config)}` : '';
      const r = await fetch(`${API}/api/family/history/${encodeURIComponent(die)}${q}`,
                            { cache: 'no-store' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setRows((await r.json()).snapshots ?? []);
    } catch (e) { setErr(String(e)); setRows([]); }
  };
  useEffect(() => { if (open) void load(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, [open, die, config]);

  const restore = async (s: Snapshot) => {
    const what = config != null ? `configuration '${config}'` : `die '${die}' geometry`;
    if (!window.confirm(`Restore ${what} to the ${s.taken} snapshot?\n\n`
        + (s.diff_vs_now.length ? s.diff_vs_now.slice(0, 12).join('\n') : '(identical to now)')
        + `\n\nThe current version is snapshotted first, so this can be undone.`)) return;
    setBusy(s.stamp);
    try {
      const r = await fetch(`${API}/api/family/history/restore`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ die, config, stamp: s.stamp }),
      });
      if (!r.ok) {
        let d = `HTTP ${r.status}`; try { d = (await r.json()).detail ?? d; } catch { /* keep */ }
        throw new Error(d);
      }
      onRestored?.();
      await load();
    } catch (e) { setErr(String(e)); }
    setBusy(null);
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ fontSize: 14, pb: 0.5 }}>
        History — {config != null ? `${die} / ${config}` : `${die} (die geometry)`}
        <Typography component="span" sx={{ fontSize: 11, color: 'var(--text-4)', ml: 1 }}>
          newest first · what each restore would change
        </Typography>
      </DialogTitle>
      <DialogContent sx={{ pt: 1 }}>
        {rows == null && <CircularProgress size={18}/>}
        {err && <Typography sx={{ fontSize: 11.5, color: '#f87171' }}>✗ {err}</Typography>}
        {rows != null && rows.length === 0 && !err && (
          <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>
            No snapshots yet — one is taken on every save that changes the file.
          </Typography>
        )}
        {rows?.map((s) => {
          const same = s.diff_vs_now.length === 0;
          const isOpen = openStamp === s.stamp;
          return (
            <Box key={s.stamp} sx={{ display: 'flex', alignItems: 'flex-start', gap: 1,
                                     py: 0.5, borderBottom: '1px solid var(--line-soft)' }}>
              <Typography sx={{ fontSize: 12, fontFamily: 'monospace', whiteSpace: 'nowrap',
                                color: 'var(--text-2)', pt: 0.2 }}>
                {s.taken}
              </Typography>
              <Box sx={{ flex: 1, minWidth: 0, cursor: same ? 'default' : 'pointer' }}
                   onClick={() => !same && setOpenStamp(isOpen ? null : s.stamp)}>
                <Typography sx={{ fontSize: 11.5, color: same ? 'var(--text-4)' : 'var(--text-3)',
                                  overflow: isOpen ? 'visible' : 'hidden',
                                  textOverflow: 'ellipsis', whiteSpace: isOpen ? 'pre-wrap' : 'nowrap' }}>
                  {same ? 'identical to the current file'
                        : (isOpen ? s.diff_vs_now.join('\n')
                                  : `${s.diff_vs_now.length} change${s.diff_vs_now.length > 1 ? 's' : ''}: ${s.diff_vs_now.slice(0, 3).join('; ')}`)}
                </Typography>
                {s.note && <Typography sx={{ fontSize: 10.5, color: 'var(--text-4)' }}>{s.note}</Typography>}
              </Box>
              {canWrite && !same && (
                <Tooltip title="Put this snapshot back as the file. The current version is snapshotted first, so it can be undone.">
                  <span>
                    <Button size="small" disabled={!!busy} onClick={() => restore(s)}
                      sx={{ fontSize: 11, py: 0, px: 0.8, minWidth: 0, textTransform: 'none',
                            color: '#fbbf24' }}>
                      {busy === s.stamp ? '…' : '⟲ restore'}
                    </Button>
                  </span>
                </Tooltip>
              )}
            </Box>
          );
        })}
      </DialogContent>
      <DialogActions>
        <Button size="small" onClick={onClose} sx={{ textTransform: 'none' }}>Close</Button>
      </DialogActions>
    </Dialog>
  );
};

export default ConfigHistoryDialog;
