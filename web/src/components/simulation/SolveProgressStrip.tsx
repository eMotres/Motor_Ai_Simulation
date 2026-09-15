/**
 * The Simulation tab's solve-progress strip — the transient endpoint's preset.
 *
 * The bar itself moved to `common/SolveProgressStrip` on 2026-09-07, when
 * Mechanical and Thermal asked for the same one ("нужно сделать прогресс бар в
 * Mechanical и Thermal так же, как в Simulation").  This file stays so
 * SimulationPanel's import keeps working, and so the two things only this tab
 * knows stay with this tab: the transient progress URL, and the PWM composition
 * fallback (it describes the frame schedule of a voltage run and nothing else).
 */
import React from 'react';

import Strip from '../common/SolveProgressStrip';

const SolveProgressStrip: React.FC = () => (
  <Strip endpoint="/api/simulation/physics/fem_transient/progress"
    unit="points" pwmFallback />
);

export default SolveProgressStrip;
