import type { PropSeries } from './configuratorPropeller';

export interface PropCurvePoint {
  rpm: number;
  thrustTested: number | null;
  thrustBeyondTested: number | null;
  powerTested: number | null;
  powerBeyondTested: number | null;
}

export interface PropCurveData {
  domain: [number, number] | null;
  points: PropCurvePoint[];
}

/** Curves are limited to both the speed control's allowed bounds and the real series grid. */
export function propellerCurveData(series: PropSeries, allowedMin: number, allowedMax: number): PropCurveData {
  const rpms = series.rpm.filter(Number.isFinite);
  if (!rpms.length) return { domain: null, points: [] };
  const lo = Math.max(allowedMin, Math.min(...rpms));
  const hi = Math.min(allowedMax, Math.max(...rpms));
  if (!(hi > lo)) return { domain: null, points: [] };
  const tested = (rpm: number, i: number) => series.rpm_range_tested
    ? rpm >= series.rpm_range_tested[0] && rpm <= series.rpm_range_tested[1]
    : !series.extrapolated?.[i];
  const points: PropCurvePoint[] = [];
  for (let i = 0; i < series.rpm.length; i++) {
    const rpm = series.rpm[i];
    if (!Number.isFinite(rpm) || rpm < lo || rpm > hi) continue;
    const isTested = tested(rpm, i);
    const thrust = Number.isFinite(series.thrust_N?.[i]) ? series.thrust_N[i] : null;
    const power = Number.isFinite(series.shaft_power_W?.[i]) ? series.shaft_power_W[i] : null;
    points.push({ rpm,
      thrustTested: isTested ? thrust : null,
      thrustBeyondTested: isTested ? null : thrust,
      powerTested: isTested ? power : null,
      powerBeyondTested: isTested ? null : power,
    });
  }
  return { domain: points.length >= 2 ? [lo, hi] : null, points };
}

/** Pointer coordinate in the plot area -> RPM, constrained to the visible/allowed domain. */
export function rpmAtPlotPointer(x: number, plotWidth: number, domain: [number, number]): number {
  if (!(plotWidth > 0)) return domain[0];
  const f = Math.max(0, Math.min(1, x / plotWidth));
  return domain[0] + (domain[1] - domain[0]) * f;
}
