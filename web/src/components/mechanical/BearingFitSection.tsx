/** Bearing shrink fit & holder structural strength section.
 *
 * Placed in the Mechanical tab next to BearingsSection.
 * Shows:
 *  - Bearing fit vs temperature curve & fit-lost temperature dT_loss
 *  - Hub hoop stress, Lamé contact pressure, and safety factor against yield
 *  - Bearing outer-ring compression and raceway internal clearance loss
 *  - ISO 281 L10 life comparison: 1 vs 2 bearings carrying axial thrust
 *  - Front and back holder strength under 49 N thrust + 1 N*m torque + press fit
 *
 * Repo English & zh-CN localized. Compact one-line summary + expandable details.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Box, Button, CircularProgress, Collapse, MenuItem,
  Select, Table, TableBody, TableCell, TableHead, TableRow,
  TextField, Tooltip, Typography,
} from '@mui/material';
import { useTranslation } from 'react-i18next';

import HelpTip from '../common/HelpTip';
import { useMechanicalStore } from '../../stores/mechanicalStore';
import { loadMachineBearings } from '../../lib/machineBearings';
import {
  fetchBearingFit, fetchHolderStrength, fmt,
  type BearingFitResponse, type HolderStrengthResponse,
} from './api';

const lbl = { fontSize: 11, color: 'var(--text-3)', whiteSpace: 'nowrap' } as const;
const mono = { fontFamily: 'monospace', fontSize: 12 } as const;
const cellStyle = { fontSize: 11, py: 0.5, px: 1, fontFamily: 'monospace' } as const;

export interface BearingFitSectionProps {
  rpm: number;
  die?: string | null;
  config?: string | null;
}

export const BearingFitSection: React.FC<BearingFitSectionProps> = ({
  rpm, die, config,
}) => {
  const { t } = useTranslation('mechanical');
  const st = useMechanicalStore();
  const { brg } = st;

  const [machineCtx, setMachineCtx] = useState<{ die?: string | null; config?: string | null }>({
    die: die ?? null,
    config: config ?? null,
  });

  useEffect(() => {
    if (die != null || config != null) {
      setMachineCtx({ die, config });
      return;
    }
    void loadMachineBearings().then((r) => {
      setMachineCtx({ die: r.die, config: r.config });
    });
    const onContextChanged = () => {
      void loadMachineBearings().then((r) => {
        setMachineCtx({ die: r.die, config: r.config });
      });
    };
    window.addEventListener('family-context-changed', onContextChanged);
    window.addEventListener('family-changed', onContextChanged);
    return () => {
      window.removeEventListener('family-context-changed', onContextChanged);
      window.removeEventListener('family-changed', onContextChanged);
    };
  }, [die, config]);

  // Selected bearing card (from store or default 618/8-2Z for miniature)
  const bearingName = brg.A || '618/8-2Z';

  // Local calculation parameters
  const [offsetMm, setOffsetMm] = useState<string>('0.04');
  const [offsetMode, setOffsetMode] = useState<'diametral' | 'radial'>('diametral');
  const [thrustN, setThrustN] = useState<string>('49.05');
  const [torqueNm, setTorqueNm] = useState<string>('1.0');
  const [showDetails, setShowDetails] = useState<boolean>(false);

  // Results
  const [fitData, setFitData] = useState<BearingFitResponse | null>(null);
  const [holderData, setHolderData] = useState<HolderStrengthResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [err, setErr] = useState<string | null>(null);

  // Working temperature from Thermal tab or manual
  const bearingTemp = useMemo(() => {
    const tm = st.thermalTemps;
    if (!tm || tm.stale) return null;
    for (const p of [tm.shaft, tm.rotor]) {
      const v = p.max ?? p.avg;
      if (v != null && Number.isFinite(v)) return v;
    }
    return null;
  }, [st.thermalTemps]);

  const workingTempC = useMemo(() => {
    if (brg.tempSource === 'thermal' && bearingTemp != null) return bearingTemp;
    const v = Number(brg.tempC);
    return Number.isFinite(v) ? v : 70;
  }, [brg.tempSource, brg.tempC, bearingTemp]);

  const abortRef = useRef<AbortController | null>(null);

  const loadCalculations = useCallback(async () => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setLoading(true);
    setErr(null);

    const offVal = Number(offsetMm) || 0.04;
    const thVal = Number(thrustN) || 49.05;
    const tqVal = Number(torqueNm) || 1.0;
    const speed = rpm > 0 ? rpm : 5000;

    try {
      const [fitRes, holderRes] = await Promise.all([
        fetchBearingFit({
          die: machineCtx.die,
          config: machineCtx.config,
          bearing: bearingName,
          offset_bearing: offVal,
          offset_mode: offsetMode,
          t_assembly: 20.0,
          t_max: Math.max(workingTempC, 120.0),
          rpm: speed,
          thrust_n: thVal,
          signal: ac.signal,
        }),
        fetchHolderStrength({
          die: machineCtx.die,
          config: machineCtx.config,
          thrust_n: thVal,
          torque_nm: tqVal,
          offset_bearing: offVal,
          offset_mode: offsetMode,
          t_working: workingTempC,
          bearing: bearingName,
          signal: ac.signal,
        }),
      ]);

      if (!ac.signal.aborted) {
        setFitData(fitRes);
        setHolderData(holderRes);
      }
    } catch (e: any) {
      if (!ac.signal.aborted) {
        setErr(e?.message || 'Failed to calculate bearing fit');
      }
    } finally {
      if (!ac.signal.aborted) setLoading(false);
    }
  }, [machineCtx.die, machineCtx.config, bearingName, offsetMm, offsetMode, thrustN, torqueNm, rpm, workingTempC]);

  useEffect(() => {
    const t = window.setTimeout(() => {
      void loadCalculations();
    }, 200);
    return () => {
      window.clearTimeout(t);
      abortRef.current?.abort();
    };
  }, [loadCalculations]);

  const fit = fitData?.fit;
  const l10 = fitData?.l10_life;
  const front = holderData?.front_holder?.combined;
  const back = holderData?.back_holder?.combined;

  // Fit lost temperature badge
  const fitLostColor = fit
    ? (workingTempC < fit.t_loss_c ? '#4ade80' : '#f87171')
    : 'var(--text-3)';

  return (
    <Box sx={{ borderTop: '1px solid var(--app-bg)', pt: 1, mt: 1 }}>
      {/* Header controls line */}
      <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap', mb: 0.75 }}>
        <Tooltip title="Bearing hot/shrink fit in the aluminium holder (offset_bearing) and holder structural strength under propeller thrust (49 N) and torque (1 N·m). Closed-form Lamé thick-walled cylinder mechanics + ISO 281 L10 rating life.">
          <Typography sx={{ ...lbl, fontWeight: 700, cursor: 'help', borderBottom: '1px dotted var(--text-4)' }}>
            {t('bearingFit.title', 'Bearing fit & holder strength')}
          </Typography>
        </Tooltip>

        {/* Offset input */}
        <Tooltip title="Interference at assembly (20 °C) between holder seat and bearing OD (Fusion parameter offset_bearing, default 0.04 mm / 40 µm).">
          <TextField
            label={t('bearingFit.offsetBearing', 'Offset mm')}
            size="small"
            value={offsetMm}
            onChange={(e) => setOffsetMm(e.target.value)}
            sx={{ width: 88 }}
            inputProps={{ style: { fontSize: 11 } }}
            InputLabelProps={{ style: { fontSize: 11 } }}
          />
        </Tooltip>

        {/* Mode select */}
        <Tooltip title="Offset convention: diametral (default 0.04 mm on diameter) or radial (0.04 mm on radius = 0.08 mm on diameter).">
          <Select
            size="small"
            value={offsetMode}
            onChange={(e) => setOffsetMode(e.target.value as 'diametral' | 'radial')}
            sx={{ fontSize: 11, height: 30, minWidth: 96 }}
          >
            <MenuItem value="diametral" sx={{ fontSize: 11 }}>{t('bearingFit.diametral', 'diametral')}</MenuItem>
            <MenuItem value="radial" sx={{ fontSize: 11 }}>{t('bearingFit.radial', 'radial')}</MenuItem>
          </Select>
        </Tooltip>

        {/* Thrust input */}
        <Tooltip title="Propeller axial thrust [N] transmitted through the front bearing lip (5 kg = 49.05 N).">
          <TextField
            label={t('bearingFit.thrustLoad', 'Thrust N')}
            size="small"
            value={thrustN}
            onChange={(e) => setThrustN(e.target.value)}
            sx={{ width: 82 }}
            inputProps={{ style: { fontSize: 11 } }}
            InputLabelProps={{ style: { fontSize: 11 } }}
          />
        </Tooltip>

        {/* Torque input */}
        <Tooltip title="Stator reaction torque [N·m] resisted by the back holder mounting pocket.">
          <TextField
            label={t('bearingFit.reactionTorque', 'Torque N·m')}
            size="small"
            value={torqueNm}
            onChange={(e) => setTorqueNm(e.target.value)}
            sx={{ width: 88 }}
            inputProps={{ style: { fontSize: 11 } }}
            InputLabelProps={{ style: { fontSize: 11 } }}
          />
        </Tooltip>

        <Button
          size="small"
          variant="outlined"
          onClick={() => setShowDetails(!showDetails)}
          sx={{ textTransform: 'none', fontSize: 11, height: 30, ml: 'auto' }}
        >
          {showDetails ? t('bearingFit.hideCurve', 'Hide details') : t('bearingFit.showCurve', 'Show details')}
        </Button>
        <HelpTip title="Detailed interference vs temperature curve, Lamé contact pressure, hub hoop stress, clearance loss, and holder stress breakdown." />
      </Box>

      {err && <Alert severity="error" sx={{ mb: 1, fontSize: 12 }}>{err}</Alert>}

      {loading && !fit && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 0.5 }}>
          <CircularProgress size={14} />
          <Typography sx={{ ...lbl }}>Calculating bearing fit &amp; holder mechanics…</Typography>
        </Box>
      )}

      {/* Summary Headline Line 1: Bearing Fit & Life */}
      {fit && l10 && (
        <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', flexWrap: 'wrap', mb: 0.5 }}>
          <Tooltip title={`Fit lost at dT = +${fit.dT_loss_k} K above 20 °C (T = ${fit.t_loss_c} °C). At working temp ${fmt(workingTempC, 0)} °C: ${fit.is_fit_tight_at_t_max ? 'TIGHT FIT' : 'FIT LOST'}.`}>
            <Typography sx={{ ...mono, cursor: 'help' }}>
              <span style={{ color: 'var(--text-3)' }}>{t('bearingFit.fitLostAt', 'Fit lost at dT')}: </span>
              <span style={{ color: fitLostColor, fontWeight: 700 }}>+{fit.dT_loss_k} K ({fit.t_loss_c} °C)</span>
              {' · '}
              <span style={{ color: fitLostColor }}>{workingTempC < fit.t_loss_c ? t('bearingFit.tightFit', 'tight') : t('bearingFit.fitLost', 'lost')}</span>
            </Typography>
          </Tooltip>

          <Tooltip title={`Contact pressure p = ${fit.assembly.p_contact_mpa} MPa at 20 °C (Lamé thick-walled cylinder). Hub hoop stress = ${fit.assembly.sigma_hoop_mpa} MPa (SF ${fit.assembly.sf_yield ?? '—'} vs 6061-T6 yield 276 MPa). Bearing internal clearance reduction = ${fit.assembly.clearance_loss_um} µm.`}>
            <Typography sx={{ ...mono, cursor: 'help' }}>
              <span style={{ color: 'var(--text-3)' }}>p: </span>{fit.assembly.p_contact_mpa} MPa
              <span style={{ color: 'var(--text-3)' }}> · σ_hoop: </span>{fit.assembly.sigma_hoop_mpa} MPa
              <span style={{ color: 'var(--text-3)' }}> (SF </span><span style={{ color: '#4ade80' }}>{fit.assembly.sf_yield ?? '—'}</span><span style={{ color: 'var(--text-3)' }}>)</span>
              <span style={{ color: 'var(--text-3)' }}> · Δc: </span>-{fit.assembly.clearance_loss_um} µm
            </Typography>
          </Tooltip>

          <Tooltip title={`ISO 281 basic rating life L10h at ${Math.round(l10.rpm).toLocaleString()} rpm under ${fmt(l10.thrust_load_n, 1)} N thrust. When both bearings remain tight, thrust is shared across 2 bearings, increasing life by ${l10.life_ratio_shared_to_single}x.`}>
            <Typography sx={{ ...mono, cursor: 'help', ml: 'auto' }}>
              <span style={{ color: 'var(--text-3)' }}>L10: </span>
              {fmt(l10.single_bearing.l10h_hours, 0)} h (1 brg) → <span style={{ color: '#4ade80', fontWeight: 700 }}>{fmt(l10.two_bearings_shared.l10h_hours, 0)} h</span>
              <span style={{ color: '#4ade80' }}> ({l10.life_ratio_shared_to_single}x life)</span>
            </Typography>
          </Tooltip>
        </Box>
      )}

      {/* Summary Headline Line 2: Holder Strength */}
      {front && back && (
        <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', flexWrap: 'wrap', mb: 0.5 }}>
          <Tooltip title={`Front holder under ${fmt(front.thrust_n, 1)} N thrust + bearing fit: averaged stress ${front.sigma_avg_mpa} MPa, peak local ${front.sigma_peak_mpa} MPa. Project convention: SF = 276 / averaged stress = ${front.safety_factor}. Prototype benchmark: 28 MPa.`}>
            <Typography sx={{ ...mono, cursor: 'help' }}>
              <span style={{ color: 'var(--text-3)' }}>{t('bearingFit.frontHolder', 'Front holder')}: </span>
              {front.sigma_avg_mpa} MPa
              <span style={{ color: 'var(--text-3)' }}> · SF </span>
              <span style={{ color: (front.safety_factor ?? 0) > 2 ? '#4ade80' : '#f87171', fontWeight: 700 }}>
                {front.safety_factor ?? '—'}
              </span>
              <span style={{ color: 'var(--text-4)', fontSize: 11 }}> (peak {front.sigma_peak_mpa})</span>
            </Typography>
          </Tooltip>

          <Tooltip title={`Back holder under ${fmt(back.torque_nm, 2)} N·m torque + ${fmt(back.thrust_n, 1)} N thrust + bearing fit: averaged stress ${back.sigma_avg_mpa} MPa, peak local ${back.sigma_peak_mpa} MPa. Project convention: SF = 276 / averaged stress = ${back.safety_factor}. Prototype benchmark: 77 MPa.`}>
            <Typography sx={{ ...mono, cursor: 'help' }}>
              <span style={{ color: 'var(--text-3)' }}>{t('bearingFit.backHolder', 'Back holder')}: </span>
              {back.sigma_avg_mpa} MPa
              <span style={{ color: 'var(--text-3)' }}> · SF </span>
              <span style={{ color: (back.safety_factor ?? 0) > 2 ? '#4ade80' : '#f87171', fontWeight: 700 }}>
                {back.safety_factor ?? '—'}
              </span>
              <span style={{ color: 'var(--text-4)', fontSize: 11 }}> (peak {back.sigma_peak_mpa})</span>
            </Typography>
          </Tooltip>

          <Tooltip title={t('bearingFit.conventionTip', 'Project convention: every stress AVERAGED, SF = strength / averaged stress. Peak is singularity gauge only.')}>
            <Typography sx={{ ...lbl, cursor: 'help', ml: 'auto', borderBottom: '1px dotted var(--text-4)' }}>
              SF = Sy / σ_avg (averaged stress convention)
            </Typography>
          </Tooltip>
        </Box>
      )}

      {/* Expandable Details: Fit Curve & Holder Stresses Breakdown */}
      <Collapse in={showDetails}>
        <Box sx={{ mt: 1, p: 1, bgcolor: 'var(--panel-2, rgba(255,255,255,0.03))', borderRadius: 1 }}>
          <Typography sx={{ ...lbl, fontWeight: 700, mb: 0.5 }}>
            Interference vs Temperature Curve (Assembly to T_loss)
          </Typography>
          <Box sx={{ maxHeight: 200, overflowY: 'auto' }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell sx={cellStyle}>T (°C)</TableCell>
                  <TableCell sx={cellStyle}>dT (K)</TableCell>
                  <TableCell sx={cellStyle}>Interference (µm)</TableCell>
                  <TableCell sx={cellStyle}>p (MPa)</TableCell>
                  <TableCell sx={cellStyle}>σ_hoop (MPa)</TableCell>
                  <TableCell sx={cellStyle}>SF yield</TableCell>
                  <TableCell sx={cellStyle}>Δc loss (µm)</TableCell>
                  <TableCell sx={cellStyle}>State</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {(fit?.curve ?? []).map((pt) => (
                  <TableRow key={pt.temp_c}>
                    <TableCell sx={cellStyle}>{pt.temp_c}</TableCell>
                    <TableCell sx={cellStyle}>+{pt.dT_k}</TableCell>
                    <TableCell sx={cellStyle}>{pt.interference_um}</TableCell>
                    <TableCell sx={cellStyle}>{pt.p_contact_mpa}</TableCell>
                    <TableCell sx={cellStyle}>{pt.sigma_hoop_mpa}</TableCell>
                    <TableCell sx={{ ...cellStyle, color: (pt.sf_yield ?? 0) > 2 ? '#4ade80' : 'inherit' }}>
                      {pt.sf_yield ?? '—'}
                    </TableCell>
                    <TableCell sx={cellStyle}>-{pt.clearance_loss_um}</TableCell>
                    <TableCell sx={{ ...cellStyle, color: pt.is_tight ? '#4ade80' : '#f87171' }}>
                      {pt.is_tight ? 'Tight' : 'Fit lost'}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
        </Box>
      </Collapse>
    </Box>
  );
};

export default BearingFitSection;
