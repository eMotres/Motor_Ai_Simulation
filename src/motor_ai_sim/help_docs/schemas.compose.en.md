# Schemas: Compose (Edit)

An editor to assemble software modules into a graph and run it as a study. Engineers and admins.

Steps:
- "Select Graph": pick a saved graph or a Template (templates carry a Template chip).
- Palette: drag capability blocks onto the canvas. Wire an output to an input; a wire is refused ("Wire refused: reason") when the data types do not match or it would make a cycle.
- Open a block to inspect and change its parameters.
- Run: runs the graph in topological order and reports "Study completed in ..." with per-block timings, or "Study failed" with the error.
- Save overwrites your saved graph (on a Template, Save opens Save As); Save As asks for a graph name (the file name) and a title; Delete asks for confirmation; New starts an empty graph; the reload button re-reads the list.

Saved graphs live in your workspace next to the templates. This is not the motor optimiser and writes nothing into a motor or the catalogue.
