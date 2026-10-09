export type RpmMetric = 'thrust' | 'power' | 'torque';
export type RpmPoint = { rpm: number; value: number; provenance: string };
export type RpmGroup = {
  id: string;
  label: string;
  points: RpmPoint[];
  provenance: string;
  sourceUrl: string | null;
  fitUse: 'included in fit' | 'reference only';
};

type Dict = Record<string, unknown>;
type CpFit = {
  cRef: number;
  rpmRef: number;
  exponent: number;
  rpmMin: number;
  rpmMax: number;
  estimated: boolean;
} | null;

const isDict = (value: unknown): value is Dict =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const finite = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);
const positive = (value: unknown): value is number => finite(value) && value > 0;
const stringValue = (value: unknown): string | null =>
  typeof value === 'string' && value.trim() ? value.trim() : null;

function readCpFit(detail: Dict): CpFit {
  const fits = isDict(detail.fit) ? detail.fit : {};
  const raw = isDict(fits.cp) ? fits.cp : null;
  if (!raw || !positive(raw.c_ref) || !positive(raw.rpm_ref) || !finite(raw.exponent) ||
      !positive(raw.rpm_min) || !positive(raw.rpm_max) || raw.rpm_min > raw.rpm_max ||
      typeof raw.estimated !== 'boolean') return null;
  return {
    cRef: raw.c_ref,
    rpmRef: raw.rpm_ref,
    exponent: raw.exponent,
    rpmMin: raw.rpm_min,
    rpmMax: raw.rpm_max,
    estimated: raw.estimated,
  };
}

function cpShaftPower(rpm: number, detail: Dict, fit: CpFit): number | null {
  const performance = isDict(detail.performance) ? detail.performance : {};
  const rho = performance.test_density_kg_m3;
  // `disc_diameter_mm` is the actual D used by the backend coefficient fit;
  // `diameter_mm` is the nominal blade diameter when a disc D is published.
  const diameterMm = positive(detail.disc_diameter_mm) ? detail.disc_diameter_mm : detail.diameter_mm;
  if (!fit || !positive(rho) || !positive(diameterMm) || rpm < fit.rpmMin || rpm > fit.rpmMax) return null;
  const revPerSecond = rpm / 60;
  const cp = fit.cRef * (rpm / fit.rpmRef) ** fit.exponent;
  const power = cp * rho * revPerSecond ** 3 * (diameterMm / 1000) ** 5;
  return finite(power) && power >= 0 ? power : null;
}

function fitOnlyPoints(detail: Dict, metric: RpmMetric): RpmPoint[] {
  const fits = isDict(detail.fit) ? detail.fit : {};
  const fit = isDict(metric === 'thrust' ? fits.ct : fits.cp) ? (metric === 'thrust' ? fits.ct : fits.cp) as Dict : null;
  const rho = isDict(detail.performance) ? detail.performance.test_density_kg_m3 : null;
  const diameterMm = positive(detail.disc_diameter_mm) ? detail.disc_diameter_mm : detail.diameter_mm;
  if (!fit || typeof fit.estimated !== 'boolean' || !positive(fit.c_ref) || !positive(fit.rpm_ref) || !finite(fit.exponent) || !positive(fit.rpm_min) || !positive(fit.rpm_max) || fit.rpm_min > fit.rpm_max || !positive(rho) || !positive(diameterMm)) return [];
  const points: RpmPoint[] = [];
  const intervals = fit.rpm_max === fit.rpm_min ? 0 : 24;
  for (let i = 0; i <= intervals; i += 1) {
    const rpm = intervals ? fit.rpm_min + (fit.rpm_max - fit.rpm_min) * i / intervals : fit.rpm_min;
    const n = rpm / 60; const coeff = fit.c_ref * (rpm / fit.rpm_ref) ** fit.exponent;
    const value = metric === 'thrust' ? coeff * rho * n ** 2 * (diameterMm / 1000) ** 4 : coeff * rho * n ** 3 * (diameterMm / 1000) ** 5;
    const shaft = metric === 'torque' ? value / (2 * Math.PI * n) : value;
    if (finite(shaft) && shaft >= 0) {
      const fitBasis = fit.estimated ? 'estimated bounded fit' : 'published-data bounded fit';
      points.push({ rpm, value: shaft, provenance: `${fitBasis} for ${metric}` });
    }
  }
  return points;
}

function pointFor(metric: RpmMetric, row: Dict, detail: Dict, fit: CpFit): RpmPoint | null {
  const rpm = row.rpm;
  if (!positive(rpm)) return null;

  if (metric === 'thrust') {
    const grams = row.thrust_g;
    if (!finite(grams) || grams < 0) return null;
    const value = grams * 0.00980665;
    return finite(value) ? { rpm, value, provenance: 'published thrust' } : null;
  }
  const torque = row.torque_Nm;
  if (finite(torque) && torque < 0) return null;
  const omega = 2 * Math.PI * (rpm / 60);
  if (metric === 'torque' && finite(torque)) {
    return { rpm, value: torque, provenance: 'published shaft torque' };
  }
  if (metric === 'power' && finite(torque)) {
    const value = torque * omega;
    return finite(value) ? { rpm, value, provenance: 'shaft power calculated from published shaft torque' } : null;
  }

  const power = cpShaftPower(rpm, detail, fit);
  if (power === null) return null;
  const fitOrigin = fit?.estimated ? 'estimated C_P fit' : 'C_P fit to published shaft torque';
  if (metric === 'power') return { rpm, value: power, provenance: `shaft power from ${fitOrigin}` };
  const fittedTorque = power / omega;
  return finite(fittedTorque) ? { rpm, value: fittedTorque, provenance: `shaft torque from ${fitOrigin}` } : null;
}

export function buildRpmGroups(detailValue: unknown, metric: RpmMetric): RpmGroup[] {
  if (!isDict(detailValue)) return [];
  const performance = isDict(detailValue.performance) ? detailValue.performance : {};
  const tables = Array.isArray(performance.tables) ? performance.tables.filter(isDict) : [];
  const fit = readCpFit(detailValue);
  // Pick one source table for every dropdown metric. Rank actual measured
  // values before RPM coverage, then use the stable table id as the tie-break.
  const measuredScore = (table: Dict) => {
    const rows = Array.isArray(table.rows) ? table.rows.filter(isDict) : [];
    const validRows = rows.filter((row) => positive(row.rpm));
    const observations = validRows.reduce((count, row) => {
      const thrust = finite(row.thrust_g) && row.thrust_g >= 0;
      const torque = finite(row.torque_Nm) && row.torque_Nm >= 0;
      return count + Number(thrust) + Number(torque);
    }, 0);
    const rpms = validRows.map((row) => row.rpm as number);
    const coverage = rpms.length ? Math.max(...rpms) - Math.min(...rpms) : 0;
    return { observations, coverage, id: stringValue(table.id) ?? '' };
  };
  const selectedTable = tables.slice().sort((a, b) => {
    const sa = measuredScore(a); const sb = measuredScore(b);
    return sb.observations - sa.observations || sb.coverage - sa.coverage || sa.id.localeCompare(sb.id);
  })[0];
  const allGroups = (selectedTable ? [selectedTable] : []).flatMap((table) => {
    const rows = Array.isArray(table.rows) ? table.rows.filter(isDict) : [];
    const points = rows.flatMap((row) => {
      const point = pointFor(metric, row, detailValue, fit);
      return point ? [point] : [];
    }).sort((a, b) => a.rpm - b.rpm);
    if (!points.length) return [];
    const provenance = [...new Set(points.map((point) => point.provenance))].join('; ');
    const id = stringValue(table.id) ?? 'selected-table';
    const label = 'Catalogue series';
    const columns = Array.isArray(table.columns_published) ? table.columns_published : [];
    const sourceUrl = stringValue(table.source_url);
    const fitUse = table.use_in_fit === false ? 'reference only' : 'included in fit';
    const detailProvenance = [provenance,
      columns.includes('Pel') || columns.includes('power_W') ? 'Pel is electrical input' : null,
      fitUse].filter((item): item is string => typeof item === 'string').join(' · ');
    const groups: RpmGroup[] = [{ id, label: metric === 'power' ? 'Shaft power' : label,
      points, provenance: detailProvenance, sourceUrl, fitUse }];
    return groups;
  });
  if (!allGroups.length) {
    const points = fitOnlyPoints(detailValue, metric);
    const fits = isDict(detailValue.fit) ? detailValue.fit : {};
    const fit = metric === 'thrust' ? fits.ct : fits.cp;
    const estimated = isDict(fit) ? fit.estimated !== false : true;
    return points.length ? [{ id: 'fit-only', label: estimated ? 'Estimated curve' : 'Published-data fit', points, provenance: points[0].provenance, sourceUrl: null, fitUse: 'included in fit' }] : [];
  }
  return allGroups;
}
