# System graph (Simulations > System)

A view-only map of how the simulations are chained: modules, the data that flows between them, and the
feedback loops. Engineers, external co-developers and their AI agents read it to see where a module sits.
Stage 1 (this document) only draws; stage 2 and 3 are outlined at the end.

* Backend: `GET /api/system/graph` (`routes/system_graph.py`, builder in `src/motor_ai_sim/system_graph.py`).
* Frontend: Simulations > **System** (`web/src/components/system/SystemPanel.tsx`), `@xyflow/react` for the canvas
  and `@dagrejs/dagre` for the layout.
* Access: engineer and admin only (`auth._GATED`: `("GET", "/api/system/graph"): "engineer"`; the tab is `fullUI`).
* It never starts a computation and never writes anything.

## Request

| Query | Meaning |
|---|---|
| `view` | `modules` (default) or `propeller_s1`. Anything else answers 400. |
| `die`, `config`, `duty` | Optional. Adds the last-run `status` per node from records that already exist. |

## Response

```json
{
  "version": "system-graph-1",
  "view": "propeller_s1",
  "title": "Propeller S1 rating",
  "nodes": [ ... ],
  "edges": [ ... ],
  "loops": [ ... ],
  "notes": ["..."],
  "status_context": null
}
```

### Node

| Field | Meaning |
|---|---|
| `id` | Stable id (the manifest `name` for a module). |
| `kind` | `compute`, `ui`, `input`, `data`, `limit`, `search`, `output`. |
| `group` | Capability family (`solver`, `geometry`, ...) or the step group in the S1 view. |
| `title`, `summary` | One short line each. The frontend translates `title` through `system.nodes.<id>`. |
| `inputs`, `outputs` | `[{port, type}]`. For a module the port name is the contract/IR type from its manifest. |
| `source` | `{function, file, line}` of the code the node stands for (the hover). Resolved with `ast` on every request, so the line follows the code; a function that is gone makes the route answer 500 instead of showing a stale hover. |
| `status` | `{state, time_s, finished_at, summary, source, values}`; `state` is `done`, `running`, `error`, `cached` (a stored result exists) or `unknown`. |
| module only | `capability`, `version`, `trust` (core / reference / community / ...), `roadmap` (a registered stub). |

### Edge

`{id, from: {node, port}, to: {node, port}, type, feedback, via, label?, values?}`.

* `type` colours the wire: a contract type (`GeometryIR`, `MeshIR`, `ResultIR`, ...), a physical quantity in the S1
  view (`rpm`, `torque`, `current`, `temperature`, `loss_W`, ...) or `dependency` (a capability the target calls
  without a shared data type).
* `feedback: true` marks the wire that closes a loop (drawn dashed, left out of the layout ranking).
* `via` (modules view): `depends_on` (declared capability), `port_type` (an input no dependency covers, wired to the
  module that outputs that type; never creates a two-node cycle), `data` (S1 view).
* `values`: optional; shown when the wire is hovered. Today only the S1 view fills it, from the motor model when
  `die` and `config` are given (the slipstream factor on the calibration wire).
* `port: null` means the edge attaches to the node, not to a port.

### Loop

`{id, kind, title, nodes, edges, max_iterations, tolerance, tolerance_unit, detail, source, convergence}`.

* `kind`: `fixed_point` (iterate until the residual is below the tolerance) or `search` (scan + bisection).
* `max_iterations`, `tolerance` come from the code constants and defaults, read with `ast` (never typed twice):
  `FIXED_POINT_MAX_IT` / `FIXED_POINT_TOL_K` in `propeller_s1.py`, `solve_coupled(max_iter, tol_c)` in
  `routes/thermal.py`, `DEFAULT_MAX_ITER` / `DEFAULT_TOL_K` in `routes/coupled.py`. `null` = the code states none.
* `convergence`: reserved for stage 2 (`{iterations, residual}` of the last run, when a record carries it).
* Loops that nest cleanly are drawn as nested boxes; two loops that overlap only partly (the modules view's two
  EM <-> thermal loops share the thermal solver) are drawn as a coloured ring on their members.

## How a module appears in the graph

Nothing to register. A module that is in `modules/bootstrap.default_registry()` is a node:

1. `manifest().name` is the id, `ui.title` (else the name) the title, `inputs` / `outputs` the ports,
   `capability` the group, `status` the trust level, a `StubModule` is `roadmap: true`.
2. Each `depends_on` capability becomes edges from every provider of that capability (an extension such as
   `geometry.2d.ring_spm` is an alternative provider of `geometry.2d`). The edge carries the contract types the two
   manifests share; with none shared it is a `dependency` edge.
3. A declared input that no dependency covers is wired to the module that outputs that type (`ResultIR` is too
   generic and is never auto-wired).
4. Plugins dropped into `modules/geometry_plugins/` or `modules/component_plugins/` appear the same way once they
   are registered. Declare accurate `inputs`, `outputs` and `depends_on` in the manifest: that is all the graph reads.
5. A new feedback loop is a few lines in `system_graph.build_modules_graph` (nodes, and the constants through
   `const()` / `default_arg()`); the status layer maps run records to nodes in `_JOB_NODE` / `_HISTORY_NODE`.

## Status layer

With `die`, `config`, `duty` the route reads, for the calling account only: its job queue (`jobs.queue()`) and the
run history (`run_history`). A job that is queued or running is `running`; a finished one is `done`, a failed,
cancelled or interrupted one is `error`; with no job but a stored result the node is `cached`; otherwise `unknown`.
A record is used when every given filter value appears in its stored request. The `propeller_s1` view marks the
`model` node `cached` with the model's key values when `config/propeller_s1/<die>/<config>.json` (or the server's
copy) loads. The frontend takes `die` / `config` / `duty` from the duty selected in Motors (the client-side
marker), and asks for no status when none is selected.

## Views in stage 1

* `modules`: every registered module, the manifest links, three loops (EM <-> thermal in the module,
  EM <-> thermal in `/api/coupled`, the design search).
* `propeller_s1`: the chain `propeller_s1.solve_point` and `s1_rating` run; see
  `docs/PROPELLER_S1_2026-10-11.md` for the physics. The graph is checked against the code by
  `tests/test_system_graph.py` (every node's function exists and its line lies in it, the functions each loop
  calls are the ones in `_fixed_point` / `solve_point`, loop constants equal the module's).

## Stage 2 (outline): user-composed graphs

* The same `nodes` / `edges` JSON, but editable: a user picks modules, wires ports whose `type` matches, and saves
  the graph with the duty.
* Running it goes through `/api/kernel/study` (`Kernel.run_study`): the graph is topologically ordered into
  `(capability, payload)` steps; an edge `type` decides which upstream result lands in `payload["upstream"]`.
  Loops become repeated sub-studies with the loop's `max_iterations` / `tolerance`.
* `ModuleRegistry.pipeline_compatible` is the port check the editor reuses before a run.
* `convergence` and the per-node `status` fill from the study's own records.

## Stage 3 (outline): graph JSON for agents

* The same document through the MCP connector (`describe_service` lists the module graph; a tool returns a view),
  so an external agent sees which modules exist, what each consumes and produces, and where to hook in a new one
  (module contribution rules: `docs/MODULE_CONTRIBUTION_RULES.md`, component SDK: `docs/COMPONENT_SDK.md`).
* Agents propose a composed graph as JSON; the engineer reviews it in the System view before it is run.
