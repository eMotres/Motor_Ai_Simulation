/**
 * DeviceCatalog — the Controller tab's power-device catalogue.
 *
 * Owner, 2026-09-22: a CATALOG of devices in the Controller menu, with the
 * card details on click and a way to add one.  Since 2026-09-28 (unified
 * catalogues, stage 1) the list, search, card view (provenance badges,
 * datasheet links, validation, "used by") and compare are the COMMON
 * `CatalogBrowser`; what stays here is the Controller's own part: the radio
 * that picks the device, the parallel count, the "fits board" filter and the
 * admin-only "Add device" form (the backend refuses anybody else).
 */
import React, { useMemo, useState } from 'react';
import { Box, Button, TextField, Dialog, DialogTitle, DialogContent,
         DialogActions, MenuItem, Typography } from '@mui/material';
import HelpTip from '../common/HelpTip';
import CatalogBrowser from '../catalogBrowser/CatalogBrowser';
import { addDeviceCard } from '../catalogBrowser/catalogApi';
import { useAuth } from '../../contexts/AuthContext';
import { fmt, listDevices, type DeviceRow } from './controllerApi';
import { ANY_BOARD, boardGroups, fitsBoard, boardWarning } from './footprintFilter';
const BLANK_CARD = `part: MY_PART
manufacturer: ""
package: ""
ratings:
  source: "datasheet table N"
  v_dss_V: 1200
  t_j_max_c: 175
  i_d_continuous:
    - {t_case_c: 100, i_a: 100, basis: table}
r_ds_on:
  source: "datasheet table N"
  curves:
    - v_gs_on_V: 18
      points:
        - {t_j_c: 25, r_mohm: 10, basis: table}
        - {t_j_c: 175, r_mohm: 22, basis: table}
switching:
  source: "datasheet table N"
  v_dd_ref_V: 800
  i_d_ref_A: 100
  r_g_ext_ref_ohm: 2.3
  curves:
    - v_gs_off_V: 0
      t_j_c: 175
      e_on_uJ: {points: [[100, 1000]], basis: table}
      e_off_uJ: {points: [[100, 900]], basis: table}
third_quadrant:
  source: "datasheet table N"
  curves:
    - v_gs_off_V: 0
      t_j_c: 175
      points: [[0, 0], [4.0, 100]]
thermal:
  source: "datasheet table N"
  r_th_jc_k_w: {typ: 0.2, max: 0.25}
`;

interface Props {
  devices: DeviceRow[];
  selected: string;
  onSelect: (part: string) => void;
  onChanged: (rows: DeviceRow[]) => void;
  /** N per switch, editable here because this is where the device is chosen. */
  parallel: number | '';
  onParallel: (n: number | '') => void;
  switchCurrent?: { i_switch_rms_A: number | null; basis: string | null; note: string } | null;
}

const DeviceCatalog: React.FC<Props> = ({ devices, selected, onSelect, onChanged,
                                         parallel, onParallel, switchCurrent }) => {
  const { isAdmin } = useAuth();
  const [adding, setAdding] = useState(false);
  const [yaml, setYaml] = useState(BLANK_CARD);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [board, setBoard] = useState<string>(ANY_BOARD);
  const [reload, setReload] = useState(0);

  const rows = useMemo(() => devices.filter(d => !d.error), [devices]);
  const byPart = useMemo(() => new Map(rows.map(r => [r.part, r])), [rows]);
  const groups = useMemo(() => boardGroups(rows), [rows]);
  const visible = useMemo(() => (board === ANY_BOARD ? null
    : new Set(fitsBoard(rows, board).map(r => r.part))), [rows, board]);
  const boardWarn = boardWarning(rows, board);

  const save = async () => {
    setBusy(true); setErr(null);
    try {
      // The form posts YAML text; the ROUTE parses and validates it — one
      // validator, server-side, so the stored card and the loaded card agree.
      await addDeviceCard(yaml, true);
      onChanged((await listDevices()).devices);
      setReload(n => n + 1); setAdding(false);
    } catch (e) { setErr(String(e)); }
    setBusy(false);
  };

  const headerExtras = (
    <>
      <TextField select size="small" label="Fits board" value={board}
        onChange={e => setBoard(e.target.value)}
        sx={{ minWidth: 170, '& .MuiInputBase-input': { fontSize: 12, py: 0.5 } }}>
        <MenuItem value={ANY_BOARD} sx={{ fontSize: 12 }}>any board</MenuItem>
        {groups.map(g => <MenuItem key={g} value={g} sx={{ fontSize: 12 }}>{g}</MenuItem>)}
      </TextField>
      <HelpTip title="Only parts sharing this land pattern (the card's footprint compatibility group). Height and top tab may still differ." />
      {isAdmin && (
        <>
          <Button size="small" variant="outlined" onClick={() => setAdding(true)}
            sx={{ textTransform: 'none', fontSize: 11 }}>Add device</Button>
          <HelpTip title="Admin only: paste a card's YAML; the server validates it before writing config/devices." />
        </>
      )}
    </>
  );

  return (
    <Box>
      <CatalogBrowser
        kinds={['device']}
        title="Device catalogue"
        help="Every power device with a transcribed datasheet card (config/devices). Click a part for its card, sources and provenance; tick 2–3 to compare. Nothing is scraped; unpublished values stay empty."
        selectedId={selected}
        reloadKey={reload}
        visibleIds={visible}
        headerExtras={headerExtras}
        rowAction={c => (
          <input type="radio" checked={selected === c.id} onChange={() => onSelect(c.id)}
            aria-label={`select ${c.id}`} />
        )}
        extraColumn={{
          header: (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.4 }}>
              Parallel
              <HelpTip title={switchCurrent?.note
                || 'Devices per switch position on the continuous current rating alone.'} />
            </Box>
          ),
          cell: c => (selected === c.id ? (
            <TextField type="number" size="small" value={parallel}
              onChange={e => onParallel(e.target.value === '' ? '' : parseInt(e.target.value, 10))}
              sx={{ width: 78, '& input': { fontSize: 12, py: 0.4 } }} />
          ) : (
            <Typography sx={{ fontSize: 12, fontFamily: 'monospace', color: 'var(--text-1)' }}>
              {byPart.get(c.id)?.suggested_parallel != null
                ? `≥ ${byPart.get(c.id)?.suggested_parallel}` : '—'}
            </Typography>
          )),
        }}
      />
      {boardWarn && (
        <Typography sx={{ fontSize: 11.5, color: '#fbbf24', mt: 0.75 }}>{boardWarn}</Typography>)}
      {switchCurrent?.i_switch_rms_A != null && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mt: 0.75 }}>
          <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
            One switch carries {fmt(switchCurrent.i_switch_rms_A, 0)} A rms
            {switchCurrent.basis ? ` · ${switchCurrent.basis}` : ''}
          </Typography>
          <HelpTip title={switchCurrent.note} />
        </Box>)}

      {/* ── add a card (admin) ── */}
      <Dialog open={adding} onClose={() => setAdding(false)} maxWidth="md" fullWidth>
        <DialogTitle sx={{ fontSize: 15 }}>Add a device card</DialogTitle>
        <DialogContent dividers>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 1 }}>
            <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>
              Paste the card's YAML.
            </Typography>
            <HelpTip title="Every block needs a source line naming the table or figure it was transcribed from. A value the datasheet does not publish stays out — the loss model says so rather than guessing." />
          </Box>
          <TextField multiline minRows={18} fullWidth value={yaml}
            onChange={(e) => setYaml(e.target.value)}
            sx={{ '& textarea': { fontFamily: 'monospace', fontSize: 11.5 } }} />
          {err && <Typography sx={{ fontSize: 12, color: '#fca5a5', mt: 1 }}>{err}</Typography>}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setAdding(false)}>Cancel</Button>
          <Button variant="contained" disabled={busy} onClick={() => void save()}>
            {busy ? 'Saving…' : 'Save card'}</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default DeviceCatalog;