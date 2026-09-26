/**
 * DeviceCatalog — the browsable MOSFET / power-module table.
 *
 * Owner, 2026-09-22: a CATALOG of devices in the Controller menu, with the
 * card details on click and a way to add one.  The storage is a YAML card in
 * ``config/devices/``; this shows it, validates what the form posts and never
 * scrapes a datasheet — somebody transcribes the numbers and says where each
 * block came from, which is what the card's ``source`` lines are for.
 */
import React, { useMemo, useState } from 'react';
import { Box, Paper, Typography, Button, TextField, Dialog, DialogTitle,
         DialogContent, DialogActions, Link, Chip, MenuItem } from '@mui/material';
import SectionLabel from '../common/SectionLabel';
import HelpTip from '../common/HelpTip';
import { addDevice, getDevice, fmt, type DeviceRow } from './controllerApi';
import { ANY_BOARD, boardGroups, fitsBoard, boardWarning } from './footprintFilter';

const CARD = { bgcolor: 'var(--panel-2)', border: '1px solid var(--line-soft)', borderRadius: 1.5, p: 2 } as const;
const TH = { fontSize: 11, color: 'var(--text-3)', whiteSpace: 'nowrap' } as const;
const TD = { fontSize: 12, color: 'var(--text-1)', fontFamily: 'monospace', whiteSpace: 'nowrap' } as const;

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
  const [detail, setDetail] = useState<any | null>(null);
  const [adding, setAdding] = useState(false);
  const [yaml, setYaml] = useState(BLANK_CARD);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [board, setBoard] = useState<string>(ANY_BOARD);

  const rows = useMemo(() => devices.filter(d => !d.error), [devices]);
  const broken = useMemo(() => devices.filter(d => d.error), [devices]);
  const groups = useMemo(() => boardGroups(rows), [rows]);
  const shown = useMemo(() => fitsBoard(rows, board), [rows, board]);
  const boardWarn = boardWarning(rows, board);

  const open = async (part: string) => {
    setErr(null);
    try { setDetail(await getDevice(part)); } catch (e) { setErr(String(e)); }
  };

  const save = async () => {
    setBusy(true); setErr(null);
    try {
      // The form posts YAML text; the browser has no YAML parser, so it goes
      // as a string and the ROUTE parses and validates it — one validator,
      // server-side, is the only way the stored card and the loaded card agree.
      const r = await addDevice(yaml, true);
      onChanged(r.devices); setAdding(false);
    } catch (e) { setErr(String(e)); }
    setBusy(false);
  };

  return (
    <Paper sx={{ ...CARD }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5 }}>
        <SectionLabel sx={{ m: 0 }}>Device catalogue</SectionLabel>
        <HelpTip title="Every power device this project has a transcribed datasheet card for (config/devices/*.yaml). Click a row for the card, including which block came from which table or figure. Add a card by pasting its YAML — nothing is scraped; a value the datasheet does not publish stays empty and the loss model says so." />
        <Box sx={{ flex: 1 }} />
        <TextField select size="small" label="Fits board" value={board}
          onChange={e => setBoard(e.target.value)}
          sx={{ minWidth: 170, '& .MuiInputBase-input': { fontSize: 12, py: 0.5 } }}>
          <MenuItem value={ANY_BOARD} sx={{ fontSize: 12 }}>any board</MenuItem>
          {groups.map(g => <MenuItem key={g} value={g} sx={{ fontSize: 12 }}>{g}</MenuItem>)}
        </TextField>
        <HelpTip title="Show only the parts whose card puts them in this footprint compatibility group — one land pattern, so they drop onto the same board. Height and top cooling tab may still differ; the line below says when they do or when a card does not publish them. The group itself is stated on each card (compatibility_basis), not derived." />
        <Button size="small" variant="outlined" onClick={() => setAdding(true)}
          sx={{ textTransform: 'none', fontSize: 11 }}>Add device</Button>
      </Box>

      {boardWarn && (
        <Typography sx={{ fontSize: 11.5, color: '#fbbf24', mb: 1 }}>{boardWarn}</Typography>)}
      <Box sx={{ overflowX: 'auto' }}>
        <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(12, auto)', rowGap: 0.5, columnGap: 1.5, alignItems: 'center', minWidth: 900 }}>
          <Typography sx={TH} />
          <Typography sx={TH}>Part</Typography>
          <Typography sx={TH}>Package</Typography>
          <Typography sx={TH}>Size L×W×H</Typography>
          <Typography sx={TH}>Footprint</Typography>
          <Typography sx={TH}>Weight</Typography>
          <Typography sx={TH}>V_DSS</Typography>
          <Typography sx={TH}>I_D @100 °C</Typography>
          <Typography sx={TH}>R_DS(on) 25 °C</Typography>
          <Typography sx={TH}>R_DS(on) 175 °C</Typography>
          <Typography sx={TH}>T_j max</Typography>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.4 }}>
            <Typography sx={TH}>Parallel</Typography>
            <HelpTip title={switchCurrent?.note
              || 'Devices per switch position on the continuous current rating alone.'} />
          </Box>
          {shown.map(d => (
            <React.Fragment key={d.part}>
              <Box sx={{ width: 52, height: 52, color: 'var(--text-2)', display: 'flex',
                         alignItems: 'center', justifyContent: 'center' }}
                title={d.package_common_name || d.package || ''}
                dangerouslySetInnerHTML={{ __html: d.package_svg || '' }} />
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                <input type="radio" checked={selected === d.part} onChange={() => onSelect(d.part)}
                  aria-label={`select ${d.part}`} />
                <Link component="button" onClick={() => void open(d.part)}
                  sx={{ ...TD, color: 'var(--text-0)', textAlign: 'left' }}>{d.part}</Link>
              </Box>
              <Typography sx={TD}>{d.package_common_name || d.package || '—'}</Typography>
              <Typography sx={TD}>
                {d.package_size_mm?.length_mm != null
                  ? `${fmt(d.package_size_mm.length_mm, 2)} × ${fmt(d.package_size_mm.width_mm, 2)} × ${fmt(d.package_size_mm.height_mm, 2)} mm`
                  : '—'}
              </Typography>
              <Typography sx={TD} title={d.footprint
                  ? `${d.footprint.package_outline_id ?? '—'} · land pattern: ${d.footprint.land_pattern_ref ?? 'not transcribed'}`
                  : 'no footprint block on this card'}>
                {d.footprint?.compatibility_group ?? '—'}
              </Typography>
              <Typography sx={TD}>{d.weight_g != null ? `${fmt(d.weight_g, 1)} g` : '—'}</Typography>
              <Typography sx={TD}>{fmt(d.v_dss_V, 0)} V</Typography>
              <Typography sx={TD}>{fmt(d.i_d_100c_A, 0)} A</Typography>
              <Typography sx={TD}>{fmt(d.r_ds_on_25c_mohm, 2)} mΩ</Typography>
              <Typography sx={TD}>{fmt(d.r_ds_on_175c_mohm, 2)} mΩ</Typography>
              <Typography sx={TD}>{fmt(d.t_j_max_c, 0)} °C</Typography>
              {selected === d.part ? (
                <TextField type="number" size="small" value={parallel}
                  onChange={e => onParallel(e.target.value === '' ? '' : parseInt(e.target.value, 10))}
                  sx={{ width: 78, '& input': { fontSize: 12, py: 0.4 } }} />
              ) : (
                <Typography sx={TD}>
                  {d.suggested_parallel != null ? `≥ ${d.suggested_parallel}` : '—'}
                </Typography>
              )}
            </React.Fragment>
          ))}
        </Box>
      </Box>
      {switchCurrent?.i_switch_rms_A != null && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mt: 0.75 }}>
          <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>
            One switch carries {fmt(switchCurrent.i_switch_rms_A, 0)} A rms
            {switchCurrent.basis ? ` · ${switchCurrent.basis}` : ''}
          </Typography>
          <HelpTip title={switchCurrent.note} />
        </Box>)}
      {broken.map(b => (
        <Typography key={b.part} sx={{ fontSize: 11.5, color: '#fca5a5', mt: 1 }}>
          {b.part}: {b.error}
        </Typography>
      ))}
      {err && <Typography sx={{ fontSize: 12, color: '#fca5a5', mt: 1 }}>{err}</Typography>}

      {/* ── one card, whole ── */}
      <Dialog open={!!detail} onClose={() => setDetail(null)} maxWidth="md" fullWidth>
        <DialogTitle sx={{ fontSize: 15 }}>{detail?.part}</DialogTitle>
        <DialogContent dividers>
          <Box sx={{ mb: 1.5, display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
            {Object.entries(detail?.provenance || {}).map(([k, v]) => (
              <Chip key={k} size="small" label={`${k}: ${String(v).slice(0, 90)}`}
                sx={{ fontSize: 10, height: 20 }} />
            ))}
          </Box>
          <Box component="pre" sx={{ fontSize: 11, fontFamily: 'monospace', m: 0,
            whiteSpace: 'pre-wrap', color: 'var(--text-1)' }}>
            {JSON.stringify(detail?.card, null, 1)}
          </Box>
        </DialogContent>
        <DialogActions><Button onClick={() => setDetail(null)}>Close</Button></DialogActions>
      </Dialog>

      {/* ── add a card ── */}
      <Dialog open={adding} onClose={() => setAdding(false)} maxWidth="md" fullWidth>
        <DialogTitle sx={{ fontSize: 15 }}>Add a device card</DialogTitle>
        <DialogContent dividers>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 1 }}>
            <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>
              Paste the card's YAML.
            </Typography>
            <HelpTip title="Every block needs a source line naming the table or figure it was transcribed from. A value the datasheet does not publish stays out — the loss model says so rather than guessing. Nothing is scraped." />
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
    </Paper>
  );
};

export default DeviceCatalog;
