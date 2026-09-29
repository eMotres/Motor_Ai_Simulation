/**
 * HistoryPopover — "last 10 stored results", a click away from solving again.
 *
 * Owner, 2026-09-22, first sentence of the ask: *"если я запускаю те же
 * параметры каплинга, он не считается, а подгружает уже рассчитанный
 * вариант; ... нужна проверка и хранить небольшую историю, 10 вычислений"*.
 * The "Loaded from history — computed … · Recompute" one-liner
 * (`lib/historyNotice.ts`) answers HALF of that: it tells you when the
 * result on screen already came from a stored run. This is the other half —
 * a small button that lists the last 10 rows for one solve kind, so a run
 * you are not currently looking at can still be found and loaded WITHOUT
 * re-typing its parameters. Click a row to load it (nothing solved); the
 * trash icon deletes one entry.
 *
 * Presentational only: `list`/`onLoad`/`onDelete` are plugged in by the
 * caller, so the Simulation tab's EM ledger (its own pre-`run_history` store,
 * `routes/simulation.py`'s `/ledger/recent|/ledger/{key}/load|delete`) and
 * the Coupled result (the generic `motor_ai_sim.run_history`-backed
 * `/api/history` — kind `coupled.run`) share ONE popover instead of growing
 * two copies of the same list/load/delete/error-handling dance — the same
 * split `historyNotice.ts` keeps between formatting and fetching.
 */
import React from 'react';
import {
  Box, IconButton, Menu, MenuItem, Tooltip, CircularProgress, Divider,
} from '@mui/material';
import HistoryIcon from '@mui/icons-material/History';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import { historyRowLabel } from '../../lib/historyNotice';

export interface HistoryRow {
  key: string;
  summary?: string | null;
  computed_at?: string | null;
}

interface Props {
  /** The last-10 rows, newest first — whatever shape the backend for this
   *  kind returns, already reduced to `{key, summary, computed_at}`. */
  list: () => Promise<HistoryRow[]>;
  /** Load row `key` as the current result. Throws on failure (shown inline);
   *  resolving closes the popover — the caller's own state update is what
   *  makes the panel show the loaded run. */
  onLoad: (key: string) => Promise<void>;
  onDelete: (key: string) => Promise<void>;
  /** Disabled while a run/solve of the SAME kind is already in flight — a
   *  load mid-solve would race the solve's own state update. */
  disabled?: boolean;
  /** What the button's tooltip and the empty-list line call these rows —
   *  "EM transient runs", "coupled runs". */
  label: string;
}

const HistoryPopover: React.FC<Props> = ({ list, onLoad, onDelete, disabled, label }) => {
  const [anchor, setAnchor] = React.useState<HTMLElement | null>(null);
  const [rows, setRows] = React.useState<HistoryRow[] | null>(null);
  const [busyKey, setBusyKey] = React.useState<string | null>(null);
  const [err, setErr] = React.useState<string | null>(null);

  const reload = React.useCallback(() => {
    setRows(null);
    setErr(null);
    list().then(setRows).catch((e: unknown) => {
      setErr(String((e as Error)?.message ?? e));
      setRows([]);
    });
  }, [list]);

  const open = (e: React.MouseEvent<HTMLElement>) => {
    setAnchor(e.currentTarget);
    reload();
  };
  const close = () => { if (!busyKey) setAnchor(null); };

  const doLoad = async (key: string) => {
    if (busyKey) return;
    setBusyKey(key); setErr(null);
    try {
      await onLoad(key);
      setBusyKey(null);
      setAnchor(null);
    } catch (e: unknown) {
      setBusyKey(null);
      setErr(String((e as Error)?.message ?? e));
    }
  };

  const doDelete = async (key: string, ev: React.MouseEvent) => {
    ev.stopPropagation();
    if (busyKey) return;
    setBusyKey(key); setErr(null);
    try {
      await onDelete(key);
      setRows(r => (r ?? []).filter(x => x.key !== key));
    } catch (e: unknown) {
      setErr(String((e as Error)?.message ?? e));
    }
    setBusyKey(null);
  };

  return (
    <>
      <Tooltip title={`History — the last 10 stored ${label}, load one instead of solving again`}>
        <span>
          <IconButton size="small" onClick={open} disabled={disabled}
            sx={{ color: 'var(--text-3)', p: 0.5 }}>
            <HistoryIcon sx={{ fontSize: 16 }} />
          </IconButton>
        </span>
      </Tooltip>
      <Menu anchorEl={anchor} open={!!anchor} onClose={close}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
        slotProps={{ paper: { sx: { minWidth: 260, maxWidth: 420 } } }}>
        <Box sx={{ px: 1.5, py: 0.5, fontSize: 10, color: 'var(--text-4)',
          textTransform: 'uppercase', letterSpacing: 0.4 }}>
          {label} — last {rows?.length ?? 10}
        </Box>
        <Divider />
        {rows === null && (
          <MenuItem disabled sx={{ fontSize: 12, gap: 1 }}>
            <CircularProgress size={13} />loading…
          </MenuItem>
        )}
        {rows !== null && rows.length === 0 && !err && (
          <MenuItem disabled sx={{ fontSize: 12 }}>no stored runs yet</MenuItem>
        )}
        {err && (
          <MenuItem disabled sx={{ fontSize: 11, color: '#f87171', whiteSpace: 'normal' }}>
            {err}
          </MenuItem>
        )}
        {(rows ?? []).map(r => (
          <MenuItem key={r.key} onClick={() => void doLoad(r.key)}
            disabled={!!busyKey}
            sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                 gap: 1, fontSize: 12 }}>
            <Box component="span" sx={{ display: 'flex', alignItems: 'center', gap: 0.6,
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {busyKey === r.key && <CircularProgress size={12} />}
              {historyRowLabel(r)}
            </Box>
            <IconButton size="small" onClick={(e) => void doDelete(r.key, e)}
              disabled={!!busyKey}
              sx={{ p: 0.25, color: 'var(--text-4)', '&:hover': { color: '#f87171' } }}>
              <DeleteOutlineIcon sx={{ fontSize: 14 }} />
            </IconButton>
          </MenuItem>
        ))}
      </Menu>
    </>
  );
};

export default HistoryPopover;
