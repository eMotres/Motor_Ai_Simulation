import { Alert, Box } from '@mui/material';
import type { StepEnv } from './env';
import { deliveryConflictLines } from './reviewSummary';

/** The commercial requirements (6_1 / 6_2) and the production plan disagree: both values, side by side, neither chosen. */
export default function DeliveryConflict({ env }: { env: Pick<StepEnv, 'steps' | 'draft' | 'lang' | 'tr'> }) {
  const lines = deliveryConflictLines(env.steps, env.draft, env.lang, env.tr);
  if (!lines.length) return null;
  return <Alert severity="warning" data-testid="delivery-conflict">{env.tr('deliveryConflict')}
    <Box component="ul" sx={{ m: 0, mt: 0.5, pl: 2.5 }}>{lines.map((l) => <li key={l}>{l}</li>)}</Box>
  </Alert>;
}
