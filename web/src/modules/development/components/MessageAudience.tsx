import { ToggleButton, ToggleButtonGroup, Tooltip } from '@mui/material';
import type { Audience } from '../internalMessage';

/** The always visible audience choice of a message composer (staff only: the caller does not render it for anybody else). */
export function MessageAudience({ value, fixed, onChange, t }: { value: Audience | null; fixed: Audience | null; onChange: (value: Audience) => void; t: (key: string) => string }) {
  const shown = fixed ?? value;
  return <Tooltip title={t('audienceTip')}><ToggleButtonGroup size="small" exclusive value={shown} disabled={fixed !== null} onChange={(_, next: Audience | null) => { if (next) onChange(next); }} aria-label={t('audience')}>
    <ToggleButton value="all" sx={{ fontSize: 12, textTransform: 'none' }}>{t('audienceEveryone')}</ToggleButton>
    <ToggleButton value="internal" color="warning" sx={{ fontSize: 12, textTransform: 'none' }}>{t('internalSwitch')}</ToggleButton>
  </ToggleButtonGroup></Tooltip>;
}
