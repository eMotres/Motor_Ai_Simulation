import type { ProjectMotor, MotorResult } from './types';
import type { SharedComparisonColumn } from '../../components/compare/SharedComparisonTable';
import { comparisonNumber } from '../../components/compare/comparisonModel.ts';

export interface DevelopmentComparisonRow {
  id: string; variant: string; motor: ProjectMotor; result: MotorResult; hasResult: boolean;
  inputs: Record<string, unknown>; values: Record<string, unknown>; history: boolean; estimate: boolean;
}
const finite = (v: unknown): number | undefined => comparisonNumber(v);
const first = (...values: unknown[]) => values.find((v) => v !== null && v !== undefined && v !== '');
const temperaturePart = (value: string) => value.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '')
  .replace(/_(?:maximum|max|temperature|temp)(?:_c)?$/, '');
const fixedTemperatureParts = new Set(['winding', 'windings', 'coil', 'coils', 'magnet', 'magnets', 'stator', 'rotor', 'bearing', 'bearings']);
const translatedTemperatureParts: Record<string, string> = {
  sleeve: 'sleeve', liner: 'liner', insulation: 'insulation', enamel: 'enamel', slot_fill: 'slotFill',
  gap_air: 'gapAir', pocket_air: 'pocketAir', shaft: 'shaft', housing: 'housing', coolant: 'coolant',
};
const mappedTemperature = (temperatures: Record<string, unknown>, aliases: string[]) => {
  for (const [key, value] of Object.entries(temperatures)) if (aliases.includes(temperaturePart(key))) {
    const number = finite(value); if (number !== undefined) return number;
  }
  return undefined;
};
const formatValue = (value: unknown, partial = false, t: (key: string) => string = (x) => x) => {
  if (value === null || value === undefined || value === '') return '-';
  if (typeof value === 'boolean') return value ? t('comparison.yes') : t('comparison.no');
  if (typeof value === 'number') return `${Number(value.toFixed(3))}${partial ? '*' : ''}`;
  return `${String(value)}${partial ? '*' : ''}`;
};
export function developmentComparisonRows(motors: ProjectMotor[]): DevelopmentComparisonRow[] {
  return motors.flatMap((motor) => {
    const projected: MotorResult[] = motor.results?.length ? motor.results : (motor.duties ?? []).map((duty) => ({ duty: typeof duty === 'string' ? duty : duty.name ?? '' }));
    const dutyRows: MotorResult[] = projected.length ? projected : [{ duty: '' }];
    return dutyRows.map((result, index) => {
    const op = result.operating_point ?? {};
    const c = (result as MotorResult & { comparison?: Record<string, unknown> }).comparison ?? {};
    const die = motor.canonical_die ?? motor.die;
    const hasResult = result.has_result ?? (Object.keys(c).some((key) => key !== 'mass_partial') || Object.keys(result.electromagnetic ?? {}).length > 0 || Object.keys(result.stored_results ?? {}).length > 0);
    // Keep result columns empty for placeholders even if a legacy or malformed
    // payload carries comparison numbers without marking a computed result.
    const solved = hasResult ? c : {};
    const linePeakUnavailableReason = (
      (solved.line_peak_provenance !== undefined
        && solved.line_peak_provenance !== 'physical_terminal_peak')
      || solved.line_peak_physical === false || solved.v_line_peak_physical === false
    ) ? 'delta_equivalent_star' : undefined;
    const temperatures = solved.temperatures_c && typeof solved.temperatures_c === 'object' && !Array.isArray(solved.temperatures_c)
      ? solved.temperatures_c as Record<string, unknown> : {};
    // Key point columns (owner 2026-10-25): power, torque, speed, current and phase voltage peak on EVERY row.  Until a
    // solve exists, torque and power show the duty's requested targets, marked as targets.
    const rpmIn = finite(first(op.rpm, op.speed_rpm));
    const targetTorque = finite(op.torque_nm);
    const targetPowerW = finite(op.power_kw) !== undefined ? finite(op.power_kw)! * 1000
      : targetTorque !== undefined && rpmIn !== undefined ? targetTorque * rpmIn * Math.PI / 30 : undefined;
    const solvedTorque = finite(solved.torque_nm), solvedPower = finite(solved.power_w);
    const keyTorque = solvedTorque ?? targetTorque, keyPowerW = solvedPower ?? targetPowerW;
    const values: Record<string, unknown> = {
      torque: solvedTorque, power: solvedPower, efficiency: finite(solved.efficiency_pct),
      keyTorque, keyPowerKw: keyPowerW === undefined ? undefined : keyPowerW / 1000,
      keyTorqueTarget: solvedTorque === undefined && targetTorque !== undefined,
      keyPowerTarget: solvedPower === undefined && targetPowerW !== undefined,
      vPhase: finite(solved.v_phase_peak_v),
      losses: finite(solved.p_loss_total_w), lossPartial: solved.loss_total_partial === true,
      pCu: finite(solved.p_cu_w), pFe: finite(solved.p_fe_w), pSolid: finite(solved.p_solid_w), pMag: finite(solved.p_mag_w), pMech: finite(solved.p_mech_total_w),
      mass: finite(solved.mass_kg), torqueMass: finite(solved.torque_to_mass_nm_kg), massPartial: solved.mass_partial === true, ripple: finite(solved.ripple_pct),
      vLine: linePeakUnavailableReason ? null : finite(solved.v_line_peak_v), linePeakUnavailableReason,
      vDc: finite(solved.v_dc_required_v), currentDensity: finite(solved.current_density_a_mm2),
      coilTemp: finite(solved.coil_temp_c) ?? mappedTemperature(temperatures, ['winding', 'windings', 'coil', 'coils']),
      magnetTemp: finite(solved.magnet_temp_c) ?? mappedTemperature(temperatures, ['magnet', 'magnets']),
      statorTemp: finite(solved.stator_temp_c) ?? mappedTemperature(temperatures, ['stator']),
      rotorTemp: finite(solved.rotor_temp_c) ?? mappedTemperature(temperatures, ['rotor']),
      bearingTemp: finite(solved.bearing_temp_c) ?? mappedTemperature(temperatures, ['bearing', 'bearings']),
      demag: first(solved.demag, solved.demagnetization),
    };
    for (const [name, value] of Object.entries(temperatures)) {
      const part = temperaturePart(name);
      const number = finite(value);
      if (part && number !== undefined && !fixedTemperatureParts.has(part)) values[`temperature:${part}`] = number;
    }
    const duty = result.duty ?? '';
    const history = motor.kind === 'history';
    const variant = `${die} / ${motor.config}`;
    const provenance = (result as MotorResult & { provenance?: { kind?: string }; result_provenance?: string }).provenance;
    const estimate = provenance?.kind === 'estimate'
      || (result as MotorResult & { result_provenance?: string }).result_provenance === 'configure_interpolation'
      || solved.result_provenance === 'configure_interpolation';
    return { id: `${history ? motor.snapshot_id : 'motor'}/${die}/${motor.config}/${duty}/${index}`, variant, motor, result, hasResult, history, estimate,
      inputs: { die, config: motor.config, duty, current: first(op.current_arms, op.I_phase_rms_A, op.current_a, op.current_A, op.current_rms_a), rpm: first(op.rpm, op.speed_rpm), gamma: first(op.gamma_deg, op.gamma) }, values };
    });
  });
}
export function developmentComparisonColumns(t: (key: string, args?: Record<string, unknown>) => string, rows: DevelopmentComparisonRow[] = []): SharedComparisonColumn<DevelopmentComparisonRow>[] {
  const input = (key: string, label: string, unit?: string): SharedComparisonColumn<DevelopmentComparisonRow> => ({ key, label: t(`comparison.column.${label}`), unit, kind: 'input', value: (r) => r.inputs[key], format: (v) => formatValue(v, false, t) });
  const result = (key: string, label: string, unit: string | undefined, better?: 'hi' | 'lo'): SharedComparisonColumn<DevelopmentComparisonRow> => ({ key, label: t(`comparison.column.${label}`), unit, kind: 'result', better, value: (r) => r.values[key], format: (v) => formatValue(v, false, t) });
    const mass = result('mass', 'mass', 'kg', 'lo');
  mass.rankValues = (comparisonRows) => {
    const measured = comparisonRows.filter((r) => finite(r.values.mass) !== undefined);
    const full = measured.filter((r) => r.values.massPartial !== true);
    return (full.length ? full : measured).map((r) => r.values.mass);
  };
  mass.noColor = (r, comparisonRows) => r.values.massPartial === true
    && comparisonRows.some((row) => row.values.massPartial !== true && finite(row.values.mass) !== undefined);
  mass.format = (v, r) => formatValue(v, r.values.massPartial === true, t);
  mass.title = (_v, r) => r.values.massPartial === true ? t('comparison.partialMass') : undefined;
  const torqueMass = result('torqueMass', 'torqueMass', 'N·m/kg', 'hi');
  torqueMass.rankValues = (comparisonRows) => {
    const measured = comparisonRows.filter((r) => finite(r.values.torqueMass) !== undefined);
    const full = measured.filter((r) => r.values.massPartial !== true);
    return (full.length ? full : measured).map((r) => r.values.torqueMass);
  };
  torqueMass.noColor = (r, comparisonRows) => r.values.massPartial === true
    && comparisonRows.some((row) => row.values.massPartial !== true && finite(row.values.torqueMass) !== undefined);
  torqueMass.format = (v, r) => formatValue(v, r.values.massPartial === true, t);
  torqueMass.title = mass.title;
  const losses = result('losses', 'totalLosses', 'W', 'lo');
  losses.rankValues = (comparisonRows) => {
    const measured = comparisonRows.filter((r) => finite(r.values.losses) !== undefined);
    const complete = measured.filter((r) => r.values.lossPartial !== true);
    return (complete.length ? complete : measured).map((r) => r.values.losses);
  };
  losses.noColor = (r, comparisonRows) => r.values.lossPartial === true
    && comparisonRows.some((row) => row.values.lossPartial !== true && finite(row.values.losses) !== undefined);
  losses.format = (v, r) => formatValue(v, r.values.lossPartial === true, t);
  losses.title = (_v, r) => r.values.lossPartial === true ? t('comparison.partialLosses') : undefined;
  const dynamicTemperatures = Array.from(new Set(rows.flatMap((row) => Object.keys(row.values).filter((key) => key.startsWith('temperature:'))))).sort()
    .map((key): SharedComparisonColumn<DevelopmentComparisonRow> => ({
      key, label: t('comparison.componentTemp', { part: translatedTemperatureParts[key.slice('temperature:'.length)]
        ? t(`comparison.temperaturePart.${translatedTemperatureParts[key.slice('temperature:'.length)]}`)
        : key.slice('temperature:'.length).replace(/_/g, ' ') }),
      unit: '°C', kind: 'result', better: 'lo', value: (r) => r.values[key], format: (v) => formatValue(v, false, t),
    }));
  // Die and configuration are already in the row label (Variant) - no second copy of them (owner 2026-10-25).
  const keyPoint = (key: string, label: string, unit: string, targetFlag?: string, digits = 1): SharedComparisonColumn<DevelopmentComparisonRow> => ({
    key, label: t(`comparison.column.${label}`), unit, kind: 'input', value: (r) => r.values[key],
    format: (v, r) => { const n = finite(v); return n === undefined ? '-' : `${Number(n.toFixed(digits))}${targetFlag && r.values[targetFlag] === true ? '*' : ''}`; },
    title: (_v, r) => targetFlag && r.values[targetFlag] === true ? t('comparison.targetValue') : undefined,
    cellColor: () => 'var(--text-1)',
  });
  const neutral = (column: SharedComparisonColumn<DevelopmentComparisonRow>) => ({ ...column, cellColor: () => 'var(--text-1)' });
  return [
    neutral(input('duty', 'duty')),
    keyPoint('keyPowerKw', 'power', 'kW', 'keyPowerTarget', 1), keyPoint('keyTorque', 'torque', 'N·m', 'keyTorqueTarget', 1),
    neutral(input('rpm', 'speed', 'rpm')), neutral(input('current', 'current', 'A')),
    keyPoint('vPhase', 'phasePeak', 'V', undefined, 1), neutral(input('gamma', 'gamma', '°')),
    result('efficiency', 'efficiency', '%', 'hi'),
    losses, result('pCu', 'copperLoss', 'W', 'lo'), result('pFe', 'ironLoss', 'W', 'lo'), result('pMag', 'magnetLoss', 'W', 'lo'), result('pSolid', 'solidLoss', 'W', 'lo'), result('pMech', 'mechanicalLoss', 'W', 'lo'),
    mass, torqueMass, result('ripple', 'ripple', '%', 'lo'),
    (() => {
      const linePeak = result('vLine', 'linePeak', 'V', 'lo');
      linePeak.value = (row) => row.values.linePeakUnavailableReason ? null : row.values.vLine;
      linePeak.title = (_value, row) => row.values.linePeakUnavailableReason
        ? t('comparison.linePeakUnavailable') : undefined;
      return linePeak;
    })(), result('vDc', 'dcBus', 'V', 'lo'), result('currentDensity', 'currentDensity', 'A/mm²', 'lo'),
    result('coilTemp', 'coilTemp', '°C', 'lo'), result('magnetTemp', 'magnetTemp', '°C', 'lo'), result('statorTemp', 'statorTemp', '°C', 'lo'), result('rotorTemp', 'rotorTemp', '°C', 'lo'), result('bearingTemp', 'bearingTemp', '°C', 'lo'), ...dynamicTemperatures,
    result('demag', 'demag', undefined),
  ];
}
