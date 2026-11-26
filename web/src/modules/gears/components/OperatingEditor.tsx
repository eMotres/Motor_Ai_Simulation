// Operating point (torque, speed, boundary, direction, temperature) and the preset / lubricant choice.
import { Box, MenuItem, TextField, ToggleButton, ToggleButtonGroup, Tooltip } from '@mui/material';
import { useTranslation } from 'react-i18next';
import type { EditorState } from '../plain';
import type { CataloguesAnswer } from '../types';
import { NumField } from './parts';

export default function OperatingEditor({ state, onChange, errors, catalogues }: {
  state: EditorState; onChange: (next: EditorState) => void; errors: Record<string, string>; catalogues: CataloguesAnswer | null;
}) {
  const { t } = useTranslation('gears');
  const set = <K extends keyof EditorState>(k: K, v: EditorState[K]) => onChange({ ...state, [k]: v });
  return (
    <Box data-testid="gears-operating" sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
      <Tooltip title={errors['/preset_name'] ?? t('tip.preset')}>
        <TextField select size="small" label={t('l.preset')} value={state.preset_name} error={!!errors['/preset_name']} sx={{ width: 190 }}
          onChange={(e) => set('preset_name', e.target.value)} slotProps={{ inputLabel: { shrink: true } }}>
          {(catalogues?.presets ?? [{ name: state.preset_name }]).map((p) => <MenuItem key={p.name} value={p.name}>{t(`presetName.${p.name}`, { defaultValue: p.name })}</MenuItem>)}
        </TextField>
      </Tooltip>
      <Tooltip title={errors['/lubricant'] ?? t('tip.lubricant')}>
        <TextField select size="small" label={t('l.lubricant')} value={state.lubricant} error={!!errors['/lubricant']} sx={{ width: 230 }}
          onChange={(e) => set('lubricant', e.target.value)} slotProps={{ inputLabel: { shrink: true } }}>
          {(catalogues?.lubricants ?? [{ name: state.lubricant, kind: '' }]).map((l) => <MenuItem key={l.name} value={l.name}>{l.name}</MenuItem>)}
        </TextField>
      </Tooltip>
      <NumField label={t('l.torque')} tip={t('tip.torque')} value={state.torque_Nm} error={errors['/torque_Nm']} testId="gears-torque" onChange={(v) => set('torque_Nm', v)} />
      <Tooltip title={t('tip.torqueAt')}>
        <ToggleButtonGroup size="small" exclusive value={state.torque_at} onChange={(_, v) => v && set('torque_at', v)} aria-label={t('l.torqueAt')}>
          <ToggleButton value="input" sx={{ textTransform: 'none', py: 0.4 }}>{t('l.atInput')}</ToggleButton>
          <ToggleButton value="output" sx={{ textTransform: 'none', py: 0.4 }}>{t('l.atOutput')}</ToggleButton>
        </ToggleButtonGroup>
      </Tooltip>
      <NumField label={t('l.inputSpeed')} tip={t('tip.inputSpeed')} value={state.input_speed_rpm} error={errors['/input_speed_rpm']} testId="gears-speed" onChange={(v) => set('input_speed_rpm', v)} width={96} />
      <Tooltip title={t('tip.direction')}>
        <ToggleButtonGroup size="small" exclusive value={state.power_direction} onChange={(_, v) => v && set('power_direction', v)} aria-label={t('l.direction')}>
          <ToggleButton value="forward" sx={{ textTransform: 'none', py: 0.4 }}>{t('l.forward')}</ToggleButton>
          <ToggleButton value="reverse" sx={{ textTransform: 'none', py: 0.4 }}>{t('l.reverse')}</ToggleButton>
        </ToggleButtonGroup>
      </Tooltip>
      <NumField label={t('l.temp')} tip={t('tip.temp')} value={state.temp_C} error={errors['/temp_C']} onChange={(v) => set('temp_C', v)} width={72} />
    </Box>
  );
}
