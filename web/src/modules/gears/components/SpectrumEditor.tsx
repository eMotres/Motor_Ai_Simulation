// Duty spectrum: output torque, output speed, hours and ambient per row (two rows by default, more allowed) + the peak event.
import { Box, Button, IconButton, Table, TableBody, TableCell, TableHead, TableRow, Tooltip } from '@mui/material';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import { useTranslation } from 'react-i18next';
import type { SpectrumState } from '../plain';
import type { PlainBin } from '../types';
import { Lbl, NumField } from './parts';

export default function SpectrumEditor({ value, onChange, errors }: { value: SpectrumState; onChange: (v: SpectrumState) => void; errors: Record<string, string> }) {
  const { t } = useTranslation('gears');
  const setBin = (i: number, patch: Partial<PlainBin>) => onChange({ ...value, bins: value.bins.map((b, k) => (k === i ? { ...b, ...patch } : b)) });
  const err = (i: number, f: string) => errors[`/spectrum/bins/${i}/${f}`];
  const head = (k: string) => <TableCell sx={{ fontSize: 12, fontWeight: 600, py: 0.4 }}><Lbl k={k} /></TableCell>;
  return (
    <Box data-testid="gears-spectrum-editor" sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
      <Table size="small">
        <TableHead><TableRow>{head('binTorque')}{head('binSpeed')}{head('binHours')}{head('binAmbient')}<TableCell /></TableRow></TableHead>
        <TableBody>
          {value.bins.map((b, i) => (
            <TableRow key={i} data-testid={`gears-bin-${i}`}>
              <TableCell sx={{ py: 0.4 }}><NumField label={t('l.binTorque')} value={b.torque_out_Nm} error={err(i, 'torque_out_Nm')} onChange={(v) => setBin(i, { torque_out_Nm: v })} /></TableCell>
              <TableCell sx={{ py: 0.4 }}><NumField label={t('l.binSpeed')} value={b.speed_out_rpm} error={err(i, 'speed_out_rpm')} onChange={(v) => setBin(i, { speed_out_rpm: v })} /></TableCell>
              <TableCell sx={{ py: 0.4 }}><NumField label={t('l.binHours')} value={b.duration_h} error={err(i, 'duration_h')} onChange={(v) => setBin(i, { duration_h: v })} /></TableCell>
              <TableCell sx={{ py: 0.4 }}><NumField label={t('l.binAmbient')} value={b.ambient_temp_C} error={err(i, 'ambient_temp_C')} onChange={(v) => setBin(i, { ambient_temp_C: v })} /></TableCell>
              <TableCell sx={{ py: 0.4 }}>
                <Tooltip title={t('l.remove')}><span><IconButton size="small" disabled={value.bins.length <= 1} aria-label={t('l.remove')}
                  onClick={() => onChange({ ...value, bins: value.bins.filter((_, k) => k !== i) })}><DeleteOutlineIcon fontSize="small" /></IconButton></span></Tooltip>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
        <Tooltip title={t('tip.addBin')}><span><Button size="small" disabled={value.bins.length >= 20} data-testid="gears-add-bin"
          onClick={() => onChange({ ...value, bins: [...value.bins, { torque_out_Nm: 1, speed_out_rpm: 100, duration_h: 100, ambient_temp_C: 25, label: '' }] })}>+ {t('l.addBin')}</Button></span></Tooltip>
        <NumField label={t('l.peakTorque')} tip={t('tip.peakTorque')} value={value.peak_torque_out_Nm} error={errors['/spectrum/peak_torque_out_Nm']} width={110} onChange={(v) => onChange({ ...value, peak_torque_out_Nm: v })} />
        <NumField label={t('l.peakEvents')} tip={t('tip.peakEvents')} value={value.peak_events} error={errors['/spectrum/peak_events']} width={96} step={1} onChange={(v) => onChange({ ...value, peak_events: Math.round(v) })} />
        <NumField label={t('l.peakSpeed')} tip={t('tip.peakSpeed')} value={value.peak_speed_out_rpm} error={errors['/spectrum/peak_speed_out_rpm']} width={110} onChange={(v) => onChange({ ...value, peak_speed_out_rpm: v })} />
      </Box>
    </Box>
  );
}
