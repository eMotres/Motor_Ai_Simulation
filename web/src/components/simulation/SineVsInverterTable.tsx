/* ── Sine | Inverter | Δ % (owner 2026-09-25) ────────────────────────────────
   «нужно давать сравнение, как изменились характеристики мотора с контроллером
   по сравнению с синусоидой».  A compact table under the coupled result of a
   drive=inverter run: the ideal sine current beside the controller's PWM, at
   the SAME temperatures (the drive the only difference), plus — on the
   final-pass algorithm, when the PWM's own losses moved the temperatures — the
   thermally corrected PWM state.  One caption line + a HelpTip (UI rule: no
   walls of text). */
import { Box, Typography } from '@mui/material';
import HelpTip from '../common/HelpTip';
import { sineCmpDelta } from './coupledApi';
import type { SineComparison } from './coupledApi';

const DIGITS: Record<string, number> = { 'N·m': 3, W: 1, V: 2, A: 2, '%': 2 };

function val(x: number | null | undefined, unit: string): string {
  if (x == null || !Number.isFinite(x)) return '—';
  return `${x.toFixed(DIGITS[unit] ?? 2)} ${unit}`;
}

const cell = { px: 0.75, py: 0.25, fontSize: 12, whiteSpace: 'nowrap' as const };

export default function SineVsInverterTable({ sc }: { sc: SineComparison }) {
  if (!sc?.rows?.length) return null;
  const corr = !!sc.has_corrected;
  const inv = sc.inverter ?? {};
  const b = (sc.basis ?? {}) as Record<string, unknown>;
  const help =
    (sc.algorithm === 'final_pass'
      ? 'The coupled loop ran on the ideal sine current; the controller\'s PWM '
        + '(carrier, dead time, device drops) was then solved once on that state. '
      : 'One extra pass on the ideal sine current at the PWM run\'s own '
        + 'fundamental current and temperatures. ')
    + '"Inverter" is the PWM at the SAME temperatures — the supply is the only '
    + 'difference, so Δ is the drive\'s own cost.'
    + (corr ? ' "Own T" is the PWM state after its extra losses were fed back '
              + `(winding ${String(b.corrected_coil_temp_c ?? '—')} °C) — the `
              + 'state the tiles above report.' : '')
    + ' Δ is % of the sine value, or percentage points (pp) for quantities '
    + 'already in %.';
  return (
    <Box sx={{ mt: 0.5, mb: 1 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.25 }}>
        <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>
          {sc.caption ?? 'Sine vs inverter at the same point'}
        </Typography>
        <HelpTip title={help} />
      </Box>
      <Box component="table" sx={{ borderCollapse: 'collapse',
        '& td, & th': { borderBottom: '1px solid var(--border-1, #ddd)' },
        '& td:not(:first-of-type), & th:not(:first-of-type)': { textAlign: 'right' } }}>
        <thead>
          <tr>
            <Box component="th" sx={cell}>Quantity</Box>
            <Box component="th" sx={cell}>Sine</Box>
            <Box component="th" sx={cell}>{corr ? 'Inverter, same T' : 'Inverter'}</Box>
            {corr && <Box component="th" sx={cell}>Inverter, own T</Box>}
            <Box component="th" sx={cell}>Δ</Box>
          </tr>
        </thead>
        <tbody>
          {sc.rows.map(r => (
            <tr key={r.key}>
              <Box component="td" sx={cell}>{r.label}</Box>
              <Box component="td" sx={cell}>{val(r.sine, r.unit)}</Box>
              <Box component="td" sx={cell}>{val(r.inverter, r.unit)}</Box>
              {corr && <Box component="td" sx={cell}>{val(r.inverter_corrected, r.unit)}</Box>}
              <Box component="td" sx={cell}>{sineCmpDelta(r.delta, r.delta_kind)}</Box>
            </tr>
          ))}
          {inv.P_inverter_W != null && (
            <tr>
              <Box component="td" sx={cell}>Inverter loss</Box>
              <Box component="td" sx={cell}>—</Box>
              {corr && <Box component="td" sx={cell}>—</Box>}
              <Box component="td" sx={cell}>{val(inv.P_inverter_W, 'W')}</Box>
              <Box component="td" sx={cell}>—</Box>
            </tr>
          )}
          {inv.eta_wall_to_shaft_pct != null && (
            <tr>
              <Box component="td" sx={cell}>η wall-to-shaft</Box>
              <Box component="td" sx={cell}>—</Box>
              {corr && <Box component="td" sx={cell}>—</Box>}
              <Box component="td" sx={cell}>{val(inv.eta_wall_to_shaft_pct, '%')}</Box>
              <Box component="td" sx={cell}>—</Box>
            </tr>
          )}
        </tbody>
      </Box>
    </Box>
  );
}
