// "With motor" results: one card per load row.  A missing number is a dash with its reason (never 0); the gearbox and motor
// efficiencies are always separate, each with its own point; motoring-only / reverse-not-computed / core limitations stay on screen.
import { Box, Chip, Paper, Table, TableBody, TableCell, TableHead, TableRow, Tooltip, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { cell, chainCell, lossCells, partCells, rowTone, type Cell, type DrivetrainResponse, type DrivetrainRow } from '../drivetrain';
import { Lbl, StateChip } from './parts';
import { TONE_COLOUR } from './tone';

const c = { fontSize: 12, py: 0.4, whiteSpace: 'nowrap' } as const;

function Val({ v, unit, why }: { v: Cell; unit?: string; why?: string | null }) {
  const { t } = useTranslation('gears');
  const reason = v.reason ?? why ?? null;
  const node = <span style={{ fontVariantNumeric: 'tabular-nums' }}>{v.text}{unit && !v.missing ? ` ${unit}` : ''}</span>;
  return v.missing && reason ? <Tooltip title={t(`dtReason.${reason}`, { defaultValue: reason })}>{node}</Tooltip> : node;
}

const TONE: Record<string, string> = { ok: TONE_COLOUR.ok, warn: TONE_COLOUR.estimated, missing: TONE_COLOUR.unsupported, failed: TONE_COLOUR.failed };

function RowCard({ row }: { row: DrivetrainRow }) {
  const { t } = useTranslation('gears');
  const tone = rowTone(row);
  const losses = lossCells(row.gearbox);
  const parts = partCells(row);
  const chain = chainCell(row);
  const mass = row.mass;
  return (
    <Paper variant="outlined" data-testid={`gears-dt-row-${row.name}`} data-status={row.status} sx={{ p: 1, borderColor: 'var(--line)', display: 'flex', flexDirection: 'column', gap: 1 }}>
      <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
        <Typography sx={{ fontWeight: 600, fontSize: 14 }}>{row.name}</Typography>
        <Typography sx={{ fontSize: 12 }}>{cell(row.torque_out_Nm).text} N*m, {cell(row.speed_out_rpm, 1).text} rpm</Typography>
        <Tooltip title={t(`dtStatusTip.${row.status}`, { defaultValue: row.status })}>
          <Chip size="small" variant="outlined" label={t(`dtStatus.${row.status}`, { defaultValue: row.status })} data-testid="gears-dt-status"
            sx={{ height: 20, fontSize: 11, color: TONE[tone], borderColor: TONE[tone] }} />
        </Tooltip>
        {row.motor_duty && <Typography sx={{ fontSize: 12 }}>{t('dtDuty', { name: row.motor_duty })}</Typography>}
        {row.covered_by_saved_point !== null && row.covered_by_saved_point !== undefined && (
          <Tooltip title={t('tip.covered')}>
            <Chip size="small" variant="outlined" data-testid="gears-dt-covered" label={row.covered_by_saved_point ? t('covered') : t('notCovered')}
              sx={{ height: 20, fontSize: 11, color: row.covered_by_saved_point ? TONE_COLOUR.ok : TONE_COLOUR.failed, borderColor: 'currentColor' }} />
          </Tooltip>
        )}
        {row.limited_by && <Tooltip title={t('tip.limitedBy')}><Chip size="small" variant="outlined" data-testid="gears-dt-limited" label={t(`limitedBy.${row.limited_by}`, { defaultValue: row.limited_by })} sx={{ height: 20, fontSize: 11 }} /></Tooltip>}
        {row.motor_saved_point && (
          <Tooltip title={t(`basisTip.${row.motor_saved_point.torque_basis}`)}>
            <Chip size="small" variant="outlined" data-testid="gears-dt-basis" label={t(`basis.${row.motor_saved_point.torque_basis}`)} sx={{ height: 20, fontSize: 11 }} />
          </Tooltip>
        )}
      </Box>

      {row.status === 'missing' && (
        <Tooltip title={t(`dtMissingTip.${row.reason}`, { defaultValue: row.reason ?? '' })}>
          <Typography data-testid="gears-dt-reason" sx={{ fontSize: 13 }}>{t(`dtMissing.${row.reason}`, { defaultValue: row.reason ?? '' })}</Typography>
        </Tooltip>
      )}
      {row.status === 'gearbox_failed' && (
        <Box data-testid="gears-dt-failed" sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap', alignItems: 'center' }}>
          <Typography sx={{ fontSize: 13, color: 'error.main' }}>{row.reason}</Typography>
          {row.gearbox_state && <StateChip state={row.gearbox_state} />}
          {(row.missing_inputs ?? []).map((m) => <Chip key={m} size="small" variant="outlined" label={m} sx={{ height: 20, fontSize: 11 }} />)}
          {(row.gearbox_flags ?? []).length > 0 && <Tooltip title={(row.gearbox_flags ?? []).join(', ')}><Chip size="small" variant="outlined" label={t('dtFlags', { count: (row.gearbox_flags ?? []).length })} sx={{ height: 20, fontSize: 11 }} /></Tooltip>}
        </Box>
      )}

      {row.motor_required && (
        <Table size="small" data-testid="gears-dt-required">
          <TableHead><TableRow><TableCell sx={c} /><TableCell sx={c}><Lbl k="dtTorque" /></TableCell><TableCell sx={c}><Lbl k="dtSpeed" /></TableCell></TableRow></TableHead>
          <TableBody>
            <TableRow><TableCell sx={c}><Lbl k="dtRequired" /></TableCell>
              <TableCell sx={c}><Val v={cell(row.motor_required.torque_Nm, 4)} unit="N*m" /></TableCell><TableCell sx={c}><Val v={cell(row.motor_required.speed_rpm, 1)} unit="rpm" /></TableCell></TableRow>
            {row.motor_saved_point && <TableRow><TableCell sx={c}><Lbl k="dtSaved" /></TableCell>
              <TableCell sx={c}><Val v={cell(row.motor_saved_point.torque_Nm, 4)} unit="N*m" /></TableCell><TableCell sx={c}><Val v={cell(row.motor_saved_point.speed_rpm, 1)} unit="rpm" /></TableCell></TableRow>}
            {row.margin && <TableRow><TableCell sx={c}><Lbl k="dtMargin" /></TableCell>
              <TableCell sx={c} data-testid="gears-dt-margin-torque"><Val v={cell(row.margin.torque_Nm, 4)} unit="N*m" /> ({cell(row.margin.torque_pct, 1).text}%)</TableCell>
              <TableCell sx={c} data-testid="gears-dt-margin-speed"><Val v={cell(row.margin.speed_rpm, 1)} unit="rpm" /> ({cell(row.margin.speed_pct, 1).text}%)</TableCell></TableRow>}
          </TableBody>
        </Table>
      )}

      {losses.length > 0 && (
        <Table size="small" data-testid="gears-dt-losses">
          <TableHead><TableRow><TableCell sx={c}><Lbl k="stage" /></TableCell><TableCell sx={c}><Lbl k="dtMesh" /></TableCell><TableCell sx={c}><Lbl k="dtBearings" /></TableCell><TableCell sx={c}><Lbl k="dtChurning" /></TableCell><TableCell sx={c}><Lbl k="dtTotal" /></TableCell></TableRow></TableHead>
          <TableBody>
            {losses.map((l) => (
              <TableRow key={l.stage}>
                <TableCell sx={c}>{l.stage}</TableCell>
                <TableCell sx={c}><Val v={l.mesh} unit="W" /></TableCell>
                <TableCell sx={c} data-testid="gears-dt-bearings"><Val v={l.bearings} unit="W" /></TableCell>
                <TableCell sx={c} data-testid="gears-dt-churning"><Val v={l.churning} unit="W" /></TableCell>
                <TableCell sx={c}><Val v={l.total} unit="W" />{l.partial && <Tooltip title={t('tip.partialLoss', { missing: l.missingInputs.join(', ') })}><Chip size="small" variant="outlined" label={t('partial')} data-testid="gears-dt-partial" sx={{ ml: 0.5, height: 18, fontSize: 10, color: TONE_COLOUR.estimated }} /></Tooltip>}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      {row.status === 'ok' && (
        <Table size="small" data-testid="gears-dt-efficiency">
          <TableBody>
            <TableRow><TableCell sx={c}><Lbl k="dtEffGearbox" /></TableCell><TableCell sx={c}><Val v={parts.gearbox!.efficiency} />{row.efficiency_parts?.gearbox.partial_losses && <Chip size="small" variant="outlined" label={t('partial')} sx={{ ml: 0.5, height: 18, fontSize: 10 }} />}</TableCell><TableCell sx={c}>{parts.gearbox!.at}</TableCell></TableRow>
            <TableRow><TableCell sx={c}><Lbl k="dtEffMotor" /></TableCell><TableCell sx={c}>{parts.motor ? <Val v={parts.motor.efficiency} /> : '-'}</TableCell><TableCell sx={c}>{parts.motor?.at ?? ''}</TableCell></TableRow>
            <TableRow><TableCell sx={c}><Lbl k="dtEffChain" /></TableCell><TableCell sx={c} data-testid="gears-dt-chain"><Val v={chain} /></TableCell><TableCell sx={c} /></TableRow>
          </TableBody>
        </Table>
      )}

      {mass && (
        <Box data-testid="gears-dt-mass" sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', fontSize: 12 }}>
          <span><Lbl k="dtMotorMass" />: <Val v={cell(mass.motor_kg, 3)} unit="kg" /></span>
          <span><Lbl k="dtGearboxMass" />: <Val v={cell(mass.gearbox_kg, 3)} unit="kg" /></span>
          <span><Lbl k="dtTotalMass" />: <Val v={cell(mass.total_kg, 3)} unit="kg" /></span>
          <span><Lbl k="dtTorquePerMass" />: <Val v={cell(row.output_torque_per_total_mass_Nm_per_kg, 2)} unit="N*m/kg" /></span>
        </Box>
      )}
    </Paper>
  );
}

export default function DrivetrainResults({ result }: { result: DrivetrainResponse }) {
  const { t } = useTranslation('gears');
  return (
    <Box data-testid="gears-dt-results" sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
      <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
        <Tooltip title={t('tip.motoringOnly')}><Chip size="small" label={t('motoringOnly')} data-testid="gears-dt-motoring" sx={{ fontWeight: 600 }} variant="outlined" /></Tooltip>
        <Tooltip title={t('tip.reverseNotComputed')}><Chip size="small" label={t('reverseNotComputed')} data-testid="gears-dt-reverse" sx={{ fontWeight: 600 }} variant="outlined" /></Tooltip>
        <Typography sx={{ fontSize: 12 }}>{t('dtSummary', { die: result.motor.die, configuration: result.motor.configuration, ratio: cell(result.gearbox.ratio_abs, 3).text })}</Typography>
        {result.gearbox.record_flags?.map((f) => <Tooltip key={f} title={t(`flagTip.${f}`, { defaultValue: f })}><Chip size="small" variant="outlined" label={t(`flag.${f}`, { defaultValue: f })} sx={{ height: 20, fontSize: 11 }} /></Tooltip>)}
      </Box>
      {result.rows.map((r) => <RowCard key={r.name} row={r} />)}
      <Paper variant="outlined" data-testid="gears-dt-limitations" sx={{ p: 1, borderColor: 'var(--line)' }}>
        <Typography sx={{ fontWeight: 600, fontSize: 13, mb: 0.5 }}><Tooltip title={t('tip.dtLimitations')}><span>{t('l.dtLimitations')}</span></Tooltip></Typography>
        <Box component="ul" sx={{ m: 0, pl: 2.5 }}>{result.core_limitations.map((x) => <li key={x}><Typography component="span" sx={{ fontSize: 12 }}>{x}</Typography></li>)}</Box>
      </Paper>
    </Box>
  );
}
