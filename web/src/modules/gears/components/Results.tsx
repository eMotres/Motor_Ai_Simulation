// Point / spectrum results: pass flag with its reasons, operating values, ratings, bearings, lubrication.
// Every value carries the core's availability: a missing number shows "-" (never zero, never green); endurance is named.
import { Box, Chip, Paper, Table, TableBody, TableCell, TableHead, TableRow, Tooltip, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import type { EvaluationResult } from '../types';
import { bearingRows, lubricationRows, minerRows, ratingRows, reasonParts, show, showField } from '../view';
import { Lbl, StateChip } from './parts';
import { TONE_COLOUR } from './tone';

const cell = { fontSize: 12, py: 0.4, whiteSpace: 'nowrap' } as const;

function Value({ s }: { s: ReturnType<typeof show> }) {
  const { t } = useTranslation('gears');
  const text = s.endurance ? t('endurance') : s.text;
  const node = <span style={{ fontVariantNumeric: 'tabular-nums' }}>{text}</span>;
  if (s.endurance) return <Tooltip title={t('tip.endurance')}>{node}</Tooltip>;
  if (!s.available && s.reason) return <Tooltip title={t(`why.${s.reason}`, { defaultValue: s.reason })}>{node}</Tooltip>;
  return node;
}

export function PassBanner({ r, isPoint }: { r: EvaluationResult; isPoint: boolean }) {
  const { t } = useTranslation('gears');
  const colour = r.passed ? TONE_COLOUR.ok : TONE_COLOUR.failed;
  return (
    <Box data-testid="gears-pass" sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
      {isPoint ? (
        // a single point is never a duty qualification: no red verdict, the reasons stay below
        <Tooltip title={t('tip.noVerdict')}>
          <Chip size="small" variant="outlined" label={t('noVerdict')} data-verdict="none" sx={{ fontWeight: 600 }} />
        </Tooltip>
      ) : (
        <Tooltip title={t('tip.pass')}>
          <Chip size="small" label={r.passed ? t('passed') : t('notPassed')} data-passed={r.passed ? 'true' : 'false'}
            sx={{ color: '#fff', bgcolor: colour, fontWeight: 600 }} />
        </Tooltip>
      )}
      <StateChip state={r.state} />
      <Typography sx={{ fontSize: 12 }}>{t('presetUsed', { name: t(`presetName.${r.preset_name}`, { defaultValue: r.preset_name }) })}</Typography>
      {r.pass_reasons?.length > 0 && (
        <Box component="ul" data-testid="gears-pass-reasons" sx={{ m: 0, pl: 2.5, width: '100%', fontSize: 12 }}>
          {r.pass_reasons.map((reason) => {
            const p = reasonParts(reason);
            return <li key={reason}><Tooltip title={t(`reasonTip.${p.kind}`, { defaultValue: reason })}><span>{t(`reason.${p.kind}`, { defaultValue: p.kind })}{p.subject ? ` ${p.subject}` : ''}{p.state ? ` (${t(`state.${p.state}`, { defaultValue: p.state })})` : ''}</span></Tooltip></li>;
          })}
        </Box>
      )}
    </Box>
  );
}

const VALUE_ROWS: Array<[string, string, string, number]> = [
  ['overall_ratio', 'ratio', '-', 4], ['input_torque_Nm', 'inputTorque', 'N*m', 3], ['input_speed_rpm', 'inputSpeed', 'rpm', 1],
  ['output_torque_Nm', 'outputTorque', 'N*m', 3], ['output_speed_rpm', 'outputSpeed', 'rpm', 1],
  ['overall_efficiency', 'effForward', '-', 4], ['reverse_efficiency', 'effReverse', '-', 4],
  ['power_in_W', 'powerIn', 'W', 1], ['power_loss_W', 'powerLoss', 'W', 2],
  ['backlash_out_arcmin', 'backlash', 'arcmin', 2], ['torsional_stiffness_out_Nm_rad', 'stiffness', 'N*m/rad', 0],
  ['mass_kg', 'mass', 'kg', 3], ['envelope_diameter_mm', 'envDiameter', 'mm', 1], ['envelope_length_mm', 'envLength', 'mm', 1],
  ['min_gear_life_h', 'gearLife', 'h', 0], ['min_bearing_life_h', 'bearingLife', 'h', 0],
];

export default function Results({ result, isPoint }: { result: EvaluationResult; isPoint: boolean }) {
  const { t } = useTranslation('gears');
  const ratings = ratingRows(result), bearings = bearingRows(result), lube = lubricationRows(result), miner = minerRows(result);
  const head = (k: string) => <TableCell sx={{ ...cell, fontWeight: 600 }}><Lbl k={k} /></TableCell>;
  return (
    <Box data-testid="gears-results" sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
      <PassBanner r={result} isPoint={isPoint} />
      <Paper variant="outlined" sx={{ p: 1, borderColor: 'var(--line)', overflowX: 'auto' }}>
        <Table size="small" data-testid="gears-values">
          <TableBody>
            {VALUE_ROWS.filter(([f]) => !(isPoint && (f === 'min_gear_life_h' || f === 'min_bearing_life_h'))).map(([f, k, unit, digits]) => {
              const s = showField(result, f, digits);
              return (<TableRow key={f}><TableCell sx={cell}><Lbl k={k} /></TableCell><TableCell sx={{ ...cell, textAlign: 'right' }} data-field={f}><Value s={s} /></TableCell><TableCell sx={cell}>{unit}</TableCell></TableRow>);
            })}
          </TableBody>
        </Table>
      </Paper>

      <Paper variant="outlined" sx={{ p: 1, borderColor: 'var(--line)', overflowX: 'auto' }}>
        <Table size="small" data-testid="gears-ratings">
          <TableHead><TableRow>{head('stage')}{head('mesh')}{head('member')}{head('sf')}{head('sh')}<TableCell sx={cell} /></TableRow></TableHead>
          <TableBody>
            {ratings.map((row, i) => (
              <TableRow key={i}>
                <TableCell sx={cell}>{row.stage}</TableCell><TableCell sx={cell}>{row.mesh}</TableCell>
                <TableCell sx={cell}>{row.member}{row.internal ? ` (${t('internal')})` : ''}</TableCell>
                <TableCell sx={{ ...cell, textAlign: 'right' }} data-testid={`gears-sf-${i}`}><Value s={show(row.S_F, row.S_F === null ? { status: 'unavailable', reason: row.bending === 'unsupported' ? 'unsupported_or_missing_input' : undefined } : null, 2)} /> <StateChip state={row.bending} /></TableCell>
                <TableCell sx={{ ...cell, textAlign: 'right' }} data-testid={`gears-sh-${i}`}><Value s={show(row.S_H, null, 2)} /> <StateChip state={row.pitting} /></TableCell>
                <TableCell sx={cell} />
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Paper>

      <Paper variant="outlined" sx={{ p: 1, borderColor: 'var(--line)', overflowX: 'auto' }}>
        <Table size="small" data-testid="gears-bearings">
          <TableHead><TableRow>{head('stage')}{head('bearingRole')}{head('bearing')}{head('l10h')}{head('s0')}<TableCell sx={cell} /></TableRow></TableHead>
          <TableBody>
            {bearings.length === 0 && <TableRow><TableCell colSpan={6} sx={cell}>{t('noBearings')}</TableCell></TableRow>}
            {bearings.map((b, i) => (
              <TableRow key={i}>
                <TableCell sx={cell}>{b.stage}</TableCell><TableCell sx={cell}>{b.role}</TableCell><TableCell sx={cell}>{b.name}</TableCell>
                <TableCell sx={{ ...cell, textAlign: 'right' }}><Value s={show(b.life_h, null, 0)} /></TableCell>
                <TableCell sx={{ ...cell, textAlign: 'right' }}><Value s={show(b.S0, null, 2)} /></TableCell>
                <TableCell sx={cell}>{b.state === 'failed' || b.state === 'unsupported' ? <Tooltip title={t('tip.bearingDiagnostic')}><span><StateChip state={b.state} /></span></Tooltip> : <StateChip state={b.state} />}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Paper>

      <Paper variant="outlined" sx={{ p: 1, borderColor: 'var(--line)', overflowX: 'auto' }}>
        <Table size="small" data-testid="gears-lubrication">
          <TableHead><TableRow>{head('stage')}{head('mesh')}{head('eta')}{head('lambda')}{head('hMin')}{head('regime')}</TableRow></TableHead>
          <TableBody>
            {lube.map((l, i) => (
              <TableRow key={i}>
                <TableCell sx={cell}>{l.stage}</TableCell><TableCell sx={cell}>{l.mesh}</TableCell>
                <TableCell sx={{ ...cell, textAlign: 'right' }}><Value s={show(l.eta, null, 1)} /></TableCell>
                <TableCell sx={{ ...cell, textAlign: 'right' }}><Value s={show(l.lambda, null, 3)} /></TableCell>
                <TableCell sx={{ ...cell, textAlign: 'right' }}><Value s={show(l.h_min_um, null, 3)} /></TableCell>
                <TableCell sx={cell}>{l.boundary ? <Tooltip title={t('tip.boundary')}><span style={{ color: TONE_COLOUR.estimated }}>{t('boundary')}</span></Tooltip> : t('filmOk')}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Paper>

      {!isPoint && miner.length > 0 && (
        <Paper variant="outlined" sx={{ p: 1, borderColor: 'var(--line)', overflowX: 'auto' }}>
          <Table size="small" data-testid="gears-miner">
            <TableHead><TableRow>{head('member')}{head('mechanism')}{head('damage')}{head('life')}<TableCell sx={cell} /></TableRow></TableHead>
            <TableBody>
              {miner.map((m, i) => (
                <TableRow key={i}>
                  <TableCell sx={cell}>{m.target_name}</TableCell><TableCell sx={cell}>{t(`mechanism.${m.mechanism}`, { defaultValue: m.mechanism })}</TableCell>
                  <TableCell sx={{ ...cell, textAlign: 'right' }}><Value s={showField(m, 'damage_D', 4)} /></TableCell>
                  <TableCell sx={{ ...cell, textAlign: 'right' }}><Value s={showField(m, 'predicted_life_hours', 0)} /></TableCell>
                  <TableCell sx={cell}><StateChip state={m.state} /></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      )}

      {!isPoint && result.preset_evaluation && (
        <Paper variant="outlined" sx={{ p: 1, borderColor: 'var(--line)', overflowX: 'auto' }}>
          <Table size="small" data-testid="gears-checks">
            <TableHead><TableRow>{head('criterion')}{head('actual')}{head('limit')}<TableCell sx={cell} /><TableCell sx={cell} /></TableRow></TableHead>
            <TableBody>
              {result.preset_evaluation.checks.map((c) => (
                <TableRow key={c.criterion}>
                  <TableCell sx={cell}>{t(`criterion.${c.criterion}`, { defaultValue: c.criterion })}</TableCell>
                  <TableCell sx={{ ...cell, textAlign: 'right' }}><Value s={showField(c, 'actual_value', 3)} /></TableCell>
                  <TableCell sx={{ ...cell, textAlign: 'right' }}><Value s={showField(c, 'limit_value', 3)} /> {c.unit !== '-' ? c.unit : ''}</TableCell>
                  <TableCell sx={cell}><StateChip state={c.state} /></TableCell>
                  <TableCell sx={{ ...cell, color: c.passed ? TONE_COLOUR.ok : TONE_COLOUR.failed }}>{c.passed ? t('passed') : t('notPassed')}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      )}

      {(result.missing_inputs?.length ?? 0) > 0 && (
        <Box data-testid="gears-missing" sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', alignItems: 'center' }}>
          <Typography sx={{ fontSize: 12, fontWeight: 600 }}><Lbl k="missingInputs" /></Typography>
          {(result.missing_inputs ?? []).map((m) => <Chip key={m} size="small" variant="outlined" label={m} sx={{ height: 20, fontSize: 11 }} />)}
        </Box>
      )}
    </Box>
  );
}
