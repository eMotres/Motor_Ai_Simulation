/** ONE width for the Simulation output block (owner 2026-11-26): the summary tile table and the charts under it (T(t) / P(t) / V(t),
 *  torque harmonics, ...) share this cap, so the charts are exactly as wide as the table above them on a wide screen, and on a
 *  narrow one (below the cap) both are the full width of the screen and stack in one column.  Used by SummaryTable, TransientCharts
 *  and the read-only project view, i.e. identical on the Simulation tab, the project pages and Configure. */
export const SIM_BLOCK_MAX_WIDTH = 1280;

export const SIM_BLOCK_SX = { width: '100%', maxWidth: SIM_BLOCK_MAX_WIDTH, boxSizing: 'border-box' } as const;
