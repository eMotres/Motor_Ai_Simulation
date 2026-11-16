/** Propellers > Designer: the pieces of the page (variables, planform, tiles, profile, curves, zones, Pareto view). */
import { useState } from 'react';
import { Box, Button, Chip, MenuItem, Paper, Select, Table, TableBody, TableCell, TableHead, TableRow, TextField, Tooltip, Typography } from '@mui/material';
import { CartesianGrid, Legend, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip as RcTooltip, XAxis, YAxis } from 'recharts';
import { useTranslation } from 'react-i18next';

import { fmt, fmt2, gPerW, round2, groupRows, keyFlags, outOfBounds, paretoFront, parseNumber, planformPolygon, profileRows, GROUP_KEYS } from '../../lib/propellerDesign';
import type { EvalRecord, RunRecord, VarRow, Vector } from '../../lib/propellerDesign';
import type { Curve, Geometry } from '../../lib/propellerDesignApi';

const LINE = 'var(--line)';
const TXT = { fontSize: 10, fill: 'var(--text-3)' };
const TITLE = { fontWeight: 600, fontSize: 13 } as const;

export function Panel({ title, tip, children }: { title: string; tip?: string; children: React.ReactNode }) {
  return (
    <Paper variant="outlined" sx={{ p: 1.25, borderColor: LINE, minWidth: 0 }}>
      <Tooltip title={tip ?? ''} disableHoverListener={!tip}><Typography sx={{ ...TITLE, mb: 0.75, display: 'inline-block' }}>{title}</Typography></Tooltip>
      {children}
    </Paper>
  );
}

/** A number field that shows the value rounded to two decimals; while it has focus it shows exactly what the user types,
 *  and every parsable text is passed on at full precision. */
function NumField({ label, name, value, error, onChange }: { label: string; name: string; value: unknown; error: boolean; onChange: (n: number) => void }) {
  const [text, setText] = useState<string | null>(null);
  const shown = text ?? (typeof value === 'number' ? String(round2(value)) : String(value));
  return (
    <TextField size="small" label={label} value={shown} error={error}
      onFocus={() => setText(typeof value === 'number' ? String(round2(value)) : String(value))}
      onBlur={() => setText(null)}
      onChange={(e) => { setText(e.target.value); const n = parseNumber(e.target.value); if (n !== null) onChange(n); }}
      inputProps={{ inputMode: 'decimal', 'aria-label': name }} InputLabelProps={{ sx: { fontSize: 12 } }} sx={{ '& input': { fontSize: 12 } }} />
  );
}

/** Variable inputs by group; a numeric field turns red outside its bounds, an option variable is a select. */
export function VariablesPanel({ rows, vector, onChange, onReset }: { rows: VarRow[]; vector: Vector; onChange: (name: string, v: number | string) => void; onReset: () => void }) {
  const { t } = useTranslation('simulation');
  return (
    <Panel title={t('propDesignGroupOuter')} tip={t('propDesignBoundsBasis')}>
      {groupRows(rows.filter((r) => r.name !== 'diameter_in' && r.name !== 'blades')).map(({ group, rows: rs }) => (
        <Box key={group} sx={{ mb: 1 }} data-testid={`prop-group-${group}`}>
          <Tooltip title={group === 'inner' ? t('propDesignGroupInnerTip') : ''} disableHoverListener={group !== 'inner'}>
            <Typography sx={{ fontSize: 12, color: 'var(--text-2)', mb: 0.5, display: 'inline-block' }}>{t(GROUP_KEYS[group])}</Typography>
          </Tooltip>
          <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 0.75 }}>
            {rs.map((r) => {
              const v = vector[r.name];
              if (r.options) {
                return (
                  <Select key={r.name} size="small" value={String(v)} onChange={(e) => onChange(r.name, e.target.value)} sx={{ fontSize: 12 }}
                    renderValue={(x) => `${r.name.replace('airfoil_', '')}: ${x}`}>
                    {r.options.map((o) => <MenuItem key={String(o)} value={String(o)} sx={{ fontSize: 12 }}>{String(o)}</MenuItem>)}
                  </Select>
                );
              }
              const bad = outOfBounds(r, v);
              const tip = r.lo != null && r.hi != null ? t('propDesignBounds', { lo: r.lo, hi: r.hi }) : '';
              return (
                <Tooltip key={r.name} title={tip}>
                  <NumField label={`${r.name} ${r.unit}`} name={r.name} value={v} error={bad} onChange={(n) => onChange(r.name, n)} />
                </Tooltip>
              );
            })}
          </Box>
        </Box>
      ))}
      <Button size="small" onClick={onReset}>{t('propDesignReset')}</Button>
    </Panel>
  );
}

export function PlanformChart({ geo, motorRadiusMm }: { geo: Geometry | null; motorRadiusMm: number }) {
  const { t } = useTranslation('simulation');
  if (!geo) return null;
  const poly = planformPolygon(geo.outline);
  const xs = poly.map((p) => p[0]), ys = poly.map((p) => p[1]);
  const x0 = 0, x1 = Math.max(...xs) * 1.02, y0 = Math.min(...ys) * 1.1, y1 = Math.max(...ys) * 1.1;
  const W = 520, H = 130, sx = (x: number) => ((x - x0) / (x1 - x0)) * W, sy = (y: number) => H - ((y - y0) / (y1 - y0)) * H;
  const d = poly.map((p, i) => `${i ? 'L' : 'M'}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join(' ') + ' Z';
  const twist = geo.outline.map((o) => ({ r_mm: o.r_mm, twist: o.twist_deg }));
  return (
    <Panel title={t('propChartPlanform')} tip={t('propChartPlanformTip')}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', maxHeight: 150 }} role="img" aria-label={t('propChartPlanform')} data-testid="prop-planform">
        <rect x={0} y={0} width={sx(motorRadiusMm)} height={H} fill="var(--brand)" opacity={0.12} />
        <line x1={sx(motorRadiusMm)} x2={sx(motorRadiusMm)} y1={0} y2={H} stroke="var(--brand)" strokeDasharray="3 3" />
        <text x={sx(motorRadiusMm) + 3} y={11} style={TXT}>{t('propMotorBand')}</text>
        <path d={d} fill="var(--text-3)" fillOpacity={0.25} stroke="var(--text-1)" strokeWidth={1} />
      </svg>
      <ResponsiveContainer width="100%" height={120}>
        <LineChart data={twist} margin={{ top: 4, right: 12, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="var(--line-soft)" strokeDasharray="3 3" />
          <XAxis dataKey="r_mm" type="number" domain={[0, 'dataMax']} tick={TXT} unit=" mm" />
          <YAxis tick={TXT} width={38} />
          <RcTooltip formatter={(v) => [`${fmt2(Number(v))}°`, t('propChartTwist')]} labelFormatter={(r) => `${fmt2(Number(r))} mm`} contentStyle={{ background: 'var(--panel)', fontSize: 12 }} />
          <ReferenceLine x={motorRadiusMm} stroke="var(--brand)" strokeDasharray="3 3" />
          <Line type="monotone" dataKey="twist" stroke="var(--text-1)" dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </Panel>
  );
}

export function ZoneTable({ geo }: { geo: Geometry | null }) {
  const { t } = useTranslation('simulation');
  if (!geo) return null;
  return (
    <Panel title={t('propZones')} tip={t('propZonesTip')}>
      <Table size="small" data-testid="prop-zones">
        <TableHead><TableRow>
          {['propZoneCol', 'propZoneAirfoil', 'propZoneTcIdeal', 'propZoneTcMade', 'propZoneTe', 'propZoneLimit'].map((k) => <TableCell key={k} sx={{ fontSize: 12 }}>{t(k)}</TableCell>)}
        </TableRow></TableHead>
        <TableBody>
          {geo.zones.map((z) => (
            <TableRow key={z.zone}>
              <TableCell sx={{ fontSize: 12 }}>{t(`propZone_${z.zone}`)}</TableCell>
              <TableCell sx={{ fontSize: 12 }}>{z.airfoil}</TableCell>
              <TableCell sx={{ fontSize: 12 }}>{fmt2(z.t_c_ideal * 100)} %</TableCell>
              <TableCell sx={{ fontSize: 12 }}>{fmt2(z.t_c_manufactured * 100)} %</TableCell>
              <TableCell sx={{ fontSize: 12 }}>{fmt2(z.te_thickness_mm)}</TableCell>
              <TableCell sx={{ fontSize: 12 }}>{z.active_limits.length ? z.active_limits.map((l) => t(`propLimit_${l}`)).join(', ') : t('propZoneNone')}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Panel>
  );
}

export function ResultTiles({ r }: { r: EvalRecord | null }) {
  const { t } = useTranslation('simulation');
  if (!r) return null;
  const tiles: Array<{ k: string; tip?: string; v: string; hot?: boolean }> = [
    { k: 'propTileEff', tip: 'propTileEffTip', v: fmt2(gPerW(r.objectives.thrust_per_shaft_W)), hot: true },
    { k: 'propTileAir', tip: 'propTileAirTip', v: fmt2(r.objectives.cooling_air_axial_ms), hot: true },
    { k: 'propTileThrust', v: fmt2(r.thrust_N) },
    { k: 'propTileTorque', tip: 'propTileTorqueTip', v: fmt2(r.torque_Nm) },
    { k: 'propTilePower', v: fmt(r.shaft_power_W, 0) },
    { k: 'propTileRpm', v: fmt(r.operating.rpm, 0) },
    { k: 'propTileMach', tip: 'propTileMachTip', v: fmt2(r.tip_mach) },
  ];
  return (
    <Box data-testid="prop-tiles">
      <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))', gap: 0.75 }}>
        {tiles.map((x) => (
          <Tooltip key={x.k} title={x.tip ? t(x.tip) : ''} disableHoverListener={!x.tip}>
            <Paper variant="outlined" sx={{ p: 0.75, borderColor: x.hot ? 'var(--brand)' : LINE }}>
              <Typography sx={{ fontSize: 11, color: 'var(--text-3)' }}>{t(x.k)}</Typography>
              <Typography sx={{ fontSize: 18, fontWeight: 600 }}>{x.v}</Typography>
            </Paper>
          </Tooltip>
        ))}
      </Box>
      <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', mt: 0.75 }}>
        {Object.entries(r.constraints).map(([k, ok]) => (
          <Chip key={k} size="small" color={ok ? 'success' : 'error'} variant="outlined" label={t(`propConstraint_${k}`)} />
        ))}
        {keyFlags(r.flags).map((f) => (
          <Tooltip key={f} title={t(`propFlagTip_${f}`)}><Chip size="small" color="warning" variant="outlined" label={t(`propFlag_${f}`)} /></Tooltip>
        ))}
      </Box>
    </Box>
  );
}

export function ProfileChart({ r, motorRadiusMm }: { r: EvalRecord | null; motorRadiusMm: number }) {
  const { t } = useTranslation('simulation');
  if (!r) return null;
  const rows = profileRows(r.profile);
  return (
    <Panel title={t('propChartProfile')} tip={t('propChartProfileTip')}>
      <ResponsiveContainer width="100%" height={170}>
        <LineChart data={rows} margin={{ top: 6, right: 12, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="var(--line-soft)" strokeDasharray="3 3" />
          <XAxis dataKey="r_mm" type="number" domain={[0, 'dataMax']} tick={TXT} unit=" mm" />
          <YAxis tick={TXT} width={38} />
          <ReferenceArea x1={0} x2={motorRadiusMm} fill="var(--brand)" fillOpacity={0.12} label={{ value: t('propMotorBand'), fontSize: 10, fill: 'var(--text-3)' }} />
          <RcTooltip formatter={(v) => `${fmt2(Number(v))} m/s`} labelFormatter={(x) => `${fmt2(Number(x))} mm`} contentStyle={{ background: 'var(--panel)', fontSize: 12 }} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Line name={t('propChartAxial')} type="monotone" dataKey="axial" stroke="var(--brand)" dot={false} connectNulls={false} isAnimationActive={false} />
          <Line name={t('propChartSwirl')} type="monotone" dataKey="swirl" stroke="#f59e0b" dot={false} connectNulls={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </Panel>
  );
}

export function CurveChart({ c }: { c: Curve | null }) {
  const { t } = useTranslation('simulation');
  if (!c) return null;
  return (
    <Panel title={t('propChartCurves')} tip={t('propChartCurvesTip')}>
      <ResponsiveContainer width="100%" height={170}>
        <LineChart data={c.rows} margin={{ top: 6, right: 12, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="var(--line-soft)" strokeDasharray="3 3" />
          <XAxis dataKey="rpm" type="number" domain={['dataMin', 'dataMax']} tick={TXT} tickFormatter={(v) => String(Math.round(Number(v)))} />
          <YAxis yAxisId="q" tick={TXT} width={42} />
          <YAxis yAxisId="t" orientation="right" tick={TXT} width={36} />
          <RcTooltip contentStyle={{ background: 'var(--panel)', fontSize: 12 }} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <ReferenceLine yAxisId="q" y={c.torque_limit_Nm} stroke="#d32f2f" strokeDasharray="4 3" label={{ value: t('propChartLimit'), fontSize: 10, fill: '#d32f2f' }} />
          <Line yAxisId="q" name={t('propChartTorque')} type="monotone" dataKey="torque_Nm" stroke="var(--brand)" dot={false} isAnimationActive={false} />
          <Line yAxisId="t" name={t('propTileThrust')} type="monotone" dataKey="thrust_N" stroke="var(--text-2)" dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </Panel>
  );
}

export function ParetoView({ runs, selectedId, onPick, onClear, cost }: { runs: RunRecord[]; selectedId: number | null; onPick: (r: RunRecord) => void; onClear: () => void; cost: number | null }) {
  const { t } = useTranslation('simulation');
  const front = paretoFront(runs);
  const frontIds = new Set(front.map((i) => runs[i].id));
  const pts = runs.filter((r) => r.thrust_per_shaft_W != null && r.air_ms != null).map((r) => ({ air: r.air_ms as number, eff: gPerW(r.thrust_per_shaft_W) as number, id: r.id }));
  const line = front.map((i) => ({ air: runs[i].air_ms as number, eff: gPerW(runs[i].thrust_per_shaft_W) as number })).sort((a, b) => a.air - b.air);
  return (
    <Panel title={t('propPareto')} tip={t('propParetoTip')}>
      {runs.length === 0 ? <Typography sx={{ fontSize: 12, color: 'var(--text-3)' }}>{t('propParetoNone')}</Typography> : (
        <>
          <ResponsiveContainer width="100%" height={220}>
            <ScatterChart margin={{ top: 8, right: 16, bottom: 18, left: 4 }}>
              <CartesianGrid stroke="var(--line-soft)" strokeDasharray="3 3" />
              <XAxis dataKey="air" type="number" name={t('propTileAir')} tick={TXT} domain={['auto', 'auto']} label={{ value: t('propTileAir'), position: 'insideBottom', offset: -8, fontSize: 10, fill: 'var(--text-3)' }} />
              <YAxis dataKey="eff" type="number" name={t('propTileEff')} tick={TXT} width={48} domain={['auto', 'auto']} tickFormatter={(v) => fmt2(Number(v))} />
              <RcTooltip cursor={{ strokeDasharray: '3 3' }} contentStyle={{ background: 'var(--panel)', fontSize: 12 }} />
              <Scatter name={t('propRuns')} data={pts} fill="var(--text-3)" isAnimationActive={false} />
              <Scatter name={t('propRunFront')} data={line} fill="var(--brand)" line={{ stroke: 'var(--brand)' }} isAnimationActive={false} />
            </ScatterChart>
          </ResponsiveContainer>
          {cost !== null && <Typography sx={{ fontSize: 12 }} data-testid="prop-front-cost">{t('propParetoCost', { value: fmt2(gPerW(cost)) })}</Typography>}
          <Box sx={{ maxHeight: 220, overflow: 'auto', mt: 0.5 }}>
            <Table size="small" data-testid="prop-runs">
              <TableHead><TableRow>
                {['propRunCol', 'propRunChange', 'propRunEff', 'propRunAir', 'propRunOk', 'propRunFront', ''].map((k, i) => <TableCell key={i} sx={{ fontSize: 11, py: 0.25 }}>{k ? t(k) : ''}</TableCell>)}
              </TableRow></TableHead>
              <TableBody>
                {runs.map((r) => (
                  <TableRow key={r.id} selected={r.id === selectedId} hover>
                    <TableCell sx={{ fontSize: 11, py: 0.25 }}>{r.id}</TableCell>
                    <TableCell sx={{ fontSize: 11, py: 0.25, maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.label}</TableCell>
                    <TableCell sx={{ fontSize: 11, py: 0.25 }}>{fmt2(gPerW(r.thrust_per_shaft_W))}</TableCell>
                    <TableCell sx={{ fontSize: 11, py: 0.25 }}>{fmt2(r.air_ms)}</TableCell>
                    <TableCell sx={{ fontSize: 11, py: 0.25 }}>{r.constraintsMet ? '✓' : '✗'}</TableCell>
                    <TableCell sx={{ fontSize: 11, py: 0.25 }}>{frontIds.has(r.id) ? '●' : ''}</TableCell>
                    <TableCell sx={{ py: 0.25 }}><Button size="small" onClick={() => onPick(r)}>{t('propParetoPick')}</Button></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
          <Button size="small" onClick={onClear}>{t('propParetoClear')}</Button>
        </>
      )}
    </Panel>
  );
}
