# Schemas: View

A read-only graph. Blocks are modules (or the steps of the propeller S1 rating), wires carry data, dashed groups are feedback loops with their stop condition and iteration limit. Nothing here runs or edits a model.

Controls:
- View tabs: "All modules" or "Propeller S1".
- "Show all wires": parameter sources (motor model, limits, ambient, drive) normally appear as chips on the blocks that use them; switch on to draw every wire.
- Die, Config and Duty pickers: choose a motor to see the status of each block from its stored results ("done", "running", "error", "stored result"); "no run yet" means there is no stored result, so run it in its Simulation tab. With no motor selected no status is shown.
- Reload button: re-reads the graph.

Hover a block to light its wires and dim the rest. Click a block to open its public API and a link to its documentation. Stub blocks marked "STUB" are planned and not implemented yet. If the graph cannot be loaded you see "Could not load the graph" with the reason.
