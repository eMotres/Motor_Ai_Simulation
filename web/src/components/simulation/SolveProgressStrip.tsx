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

/** `runId` is the panel's `runNonce` — the SAME token the Run and the Stop
 *  button already send as `run_id` (TransientCharts, FemAnimationViewer).
 *  Passing it here makes the bar follow this run rather than "whatever the
 *  server's transient route is doing", which with several accounts is somebody
 *  else's solve.  Omitted, the poll keeps its old no-argument form. */
const SolveProgressStrip: React.FC<{ runId?: string }> = ({ runId }) => (
  <Strip endpoint="/api/simulation/physics/fem_transient/progress"
    unit="points" pwmFallback runId={runId} />
);

export default SolveProgressStrip;
