// Saved sweeps: keep the finished last scan with its machine stamp, and reopen
// one later.  The store is the caller's WORKSPACE on the server
// (`<workspace>/optimization_saves/`, see sweep_saves.py) - it survives restarts
// and deploys.  Reopening makes a save the scan the tab shows; the tab then
// labels it "Previous run of <machine>" (lib/sweepRestore.ts).
import React, { useState } from 'react';
import { Box, Button, Typography } from '@mui/material';
import { machineLabel } from '../../lib/sweepRestore';
import type { MachineStamp } from '../../lib/sweepRestore';

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8001';

interface SavedRow {
  id: string;
  name: string;
  created_at: string;
  n_points: number | null;
  n_operating_points: number;
  variables: string[];
  machine: MachineStamp;
  matches_loaded_machine: boolean;
  imported: boolean;
}

interface Props {
  /** A finished, identified result is on screen (Save has something to save). */
  canSave: boolean;
  running: boolean;
  /** A save was made the server's scan - re-read it and show it. */
  onOpened: () => void | Promise<void>;
  onError: (msg: string) => void;
}

const detail = async (r: Response): Promise<string> => {
  try { const j = await r.json(); return String(j?.detail ?? `HTTP ${r.status}`); } catch { return `HTTP ${r.status}`; }
};

const SavedSweeps: React.FC<Props> = ({ canSave, running, onOpened, onError }) => {
  const [rows, setRows] = useState<SavedRow[] | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = async () => {
    try {
      const r = await fetch(`${API}/api/optimization/saved`);
      if (!r.ok) throw new Error(await detail(r));
      setRows((await r.json()).saved ?? []);
    } catch (e: any) { onError(`could not list saved sweeps: ${String(e?.message ?? e)}`); }
  };

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (next) await load();
  };

  const saveLast = async () => {
    setBusy(true); setNote(null);
    try {
      const r = await fetch(`${API}/api/optimization/saved/last`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}),
      });
      if (!r.ok) throw new Error(await detail(r));
      const j = await r.json();
      setNote(j.already_saved ? 'already saved' : `saved — ${machineLabel(j.machine)}`);
      if (open) await load();
    } catch (e: any) { onError(`could not save the sweep: ${String(e?.message ?? e)}`); }
    finally { setBusy(false); }
  };

  const reopen = async (id: string) => {
    setBusy(true); setNote(null);
    try {
      const r = await fetch(`${API}/api/optimization/saved/${id}/open`, { method: 'POST' });
      if (!r.ok) throw new Error(await detail(r));
      await onOpened();
      await load();
    } catch (e: any) { onError(`could not open the saved sweep: ${String(e?.message ?? e)}`); }
    finally { setBusy(false); }
  };

  const remove = async (id: string) => {
    setBusy(true);
    try {
      const r = await fetch(`${API}/api/optimization/saved/${id}`, { method: 'DELETE' });
      if (!r.ok) throw new Error(await detail(r));
      await load();
    } catch (e: any) { onError(`could not delete the saved sweep: ${String(e?.message ?? e)}`); }
    finally { setBusy(false); }
  };

  const btn = { textTransform: 'none', fontSize: 11, minWidth: 0 } as const;
  return (
    <>
      <Button size="small" variant="text" disabled={!canSave || running || busy} onClick={saveLast}
        sx={{ ...btn, color: 'var(--text-2)' }}
        title="Keep the finished sweep on the server with the machine it belongs to, so it can be reopened after a restart or a deploy.">
        Save sweep
      </Button>
      <Button size="small" variant="text" disabled={running} onClick={toggle}
        sx={{ ...btn, color: 'var(--text-2)' }}
        title="Sweeps saved in your workspace. Open one to see it again, with the machine it was computed on.">
        Saved sweeps{open ? ' ▴' : ' ▾'}
      </Button>
      {note && <Typography component="span" sx={{ fontSize: 11, color: '#34d399' }}>{note}</Typography>}
      {open && (
        <Box sx={{ flexBasis: '100%', mt: 0.5, border: '1px solid var(--line-soft)', borderRadius: 1, p: 0.75 }}>
          {rows == null && <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>loading…</Typography>}
          {rows != null && rows.length === 0 && (
            <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>no saved sweeps</Typography>
          )}
          {(rows ?? []).map(row => (
            <Box key={row.id} sx={{ display: 'flex', gap: 1, alignItems: 'center', py: 0.25, fontSize: 11 }}>
              <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                title={`${row.name} — ${row.created_at}${row.imported ? ' (imported)' : ''}`}>
                {row.name}
              </span>
              <span style={{ color: row.matches_loaded_machine ? '#34d399' : 'var(--text-3)', whiteSpace: 'nowrap' }}
                title={row.matches_loaded_machine
                  ? 'This is the machine loaded now.'
                  : 'Not the machine loaded now — it can be opened and viewed, but a point cannot be applied.'}>
                {machineLabel(row.machine)}
              </span>
              <span style={{ color: 'var(--text-3)', whiteSpace: 'nowrap' }}>
                {row.n_points ?? '?'} pts{row.variables.length ? ` · ${row.variables.join(', ')}` : ''}
              </span>
              <Button size="small" disabled={busy || running} onClick={() => reopen(row.id)} sx={btn}>Open</Button>
              <Button size="small" disabled={busy || running} onClick={() => remove(row.id)}
                sx={{ ...btn, color: 'var(--text-3)' }}>Delete</Button>
            </Box>
          ))}
        </Box>
      )}
    </>
  );
};

export default SavedSweeps;
