// What the core does not cover, always on screen under the results (CORE_API_AND_LIMITATIONS.md, "Limitations the UI must show").
import { Box, Paper, Tooltip, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { LIMITATIONS, PRIMARY_LIMITATIONS } from '../view';
import { StateChip } from './parts';

export default function Limitations() {
  const { t } = useTranslation('gears');
  return (
    <Paper variant="outlined" data-testid="gears-limitations" sx={{ p: 1, borderColor: 'var(--line)' }}>
      <Typography sx={{ fontWeight: 600, fontSize: 13, mb: 0.5 }}>
        <Tooltip title={t('tip.limitations')}><span>{t('l.limitations')}</span></Tooltip>
      </Typography>
      <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
        {LIMITATIONS.map((id) => (
          <li key={id} data-limitation={id} data-primary={PRIMARY_LIMITATIONS.includes(id) ? 'true' : 'false'}>
            <Tooltip title={t(`limitTip.${id}`)} placement="top-start" arrow enterTouchDelay={0}>
              <Typography component="span" sx={{ fontSize: 12, fontWeight: PRIMARY_LIMITATIONS.includes(id) ? 700 : 400 }}>
                {t(`limit.${id}`)}{' '}
              </Typography>
            </Tooltip>
            {PRIMARY_LIMITATIONS.includes(id) && <StateChip state="unsupported" />}
          </li>
        ))}
      </Box>
    </Paper>
  );
}
