# Schemas graph (Simulations > Schemas)

A view-only map of how the simulations are chained: modules, the data that flows between them, and the
feedback loops. Engineers, external co-developers and their AI agents read it to see where a software module sits.
Stage 1 (this document) only draws; stage 2 and 3 are outlined at the end.

* Backend: `GET /api/system/graph` (`routes/system_graph.py`, builder in `src/motor_ai_sim/system_graph.py`).
* Frontend: Simulations > **Schemas** (`web/src/components/system/SystemPanel.tsx`), `@xyflow/react` for the canvas
  and `@dagrejs/dagre` for the layout.
* Access: engineer and admin only (`auth._GATED`: `("GET", "/api/system/graph"): "engineer"`; the tab is `fullUI`).
* It never starts a computation and never writes anything.

## Navigation and presentation

The page is **Schemas** in the Simulations menu. The canonical page route is `/schemas`; the legacy `/system` route redirects to `/schemas`. The legacy `?tab=system` selection redirects to `?tab=schemas`, preserving other query parameters.

Node cards use a dark border at least 2 px wide, a tinted module header or background, and dark readable text on white. Text and background tokens meet at least 4.5:1 contrast. Registered `StubModule` roadmap nodes remain visually distinct with a dashed treatment while retaining readable text and contrast. Clicking a node opens its module detail with the listed public API and documentation link.

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
| `context` | `true` for a parameter source that feeds many blocks (motor model, limits, ambient, drive, ...): the view draws it in a compact **Inputs** column and shows chips on its consumers instead of wires. |
| `condition` | Limit checks only: `{key, params}`, the one-line condition the block prints (`winding <= 200 C (class N)`, `I <= demag limit(T_magnet)`, ...). The numbers are read from the code (the project insulation class) or, with a motor selected, from that motor's model. |
| `layout` | Optional `{col, row}` grid hint (row 1 = one block pitch; the main chain is row 0). When every non-context node has one the view uses it, otherwise it falls back to an automatic layout (dagre). |
| module only | `capability`, `version`, `trust` (core / reference / community / ...), `roadmap` (a registered stub). |

Each node also carries `module_id` (`M01`–`M14`), the exact functional `module_name` from `docs/MODULE_MAP.md`, `public_api` and a repository-relative `documentation` link. The registry view maps registered providers into functional owners; its transmission plugin belongs to M11 as a component SDK registration and BasicCost belongs to M11 because its implementation is a registry module with a Component/Result contract. The S1 steps belong to M06: they describe the Configure passport surrogate workflow and use M03/M05 concepts through adapters, but they do not represent those solvers. Unknown registry nodes fail graph construction instead of receiving a catch-all owner.

### Edge

`{id, from: {node, port}, to: {node, port}, type, feedback, via, minor, route, label?, values?}`.

* `type` colours the wire: a contract type (`GeometryIR`, `MeshIR`, `ResultIR`, ...), a physical quantity in the S1
  view (`rpm`, `torque`, `current`, `temperature`, `loss_W`, ...) or `dependency` (a capability the target calls
  without a shared data type).
* `feedback: true` marks the wire that closes a loop (drawn dashed, left out of the layout ranking).
* `via` (modules view): `depends_on` (declared capability), `port_type` (an input no dependency covers, wired to the
  module that outputs that type; never creates a two-node cycle), `data` (S1 view).
* `values`: optional; shown when the wire is hovered. Today only the S1 view fills it, from the motor model when
  `die` and `config` are given (the slipstream factor on the calibration wire).
* `minor: true`: a parameter-like link (the search speed, the converged current, ...) shown as a chip on the target, like the links from a `context` node; "Show all wires" draws it as a wire. `label` names the chip.
* `route: "lane"`: routed under the loop group like a feedback wire, for a link that would otherwise run across the blocks (the S1 rating returned to the result block).
* `port: null` means the edge attaches to the node, not to a port.

### Loop

`{id, kind, title, nodes, edges, max_iterations, tolerance, tolerance_unit, detail, source, convergence}`.

* `kind`: `fixed_point` (iterate until the residual is below the tolerance) or `search` (scan + bisection).
* `max_iterations`, `tolerance` come from the code constants and defaults, read with `ast` (never typed twice):
  `FIXED_POINT_MAX_IT` / `FIXED_POINT_TOL_K` in `propeller_s1.py`, `solve_coupled(max_iter, tol_c)` in
  `routes/thermal.py`, `DEFAULT_MAX_ITER` / `DEFAULT_TOL_K` in `routes/coupled.py`. `null` = the code states none.
* `rule`: what the loop does, every number read from the code with `ast` (never typed twice): `iterates` (id of the
  explanation text), `stop` (`max_delta` with the tolerance, `all_pass`, or `budget`), `limit.max`, `damping`
  (`start`, halving `factor` and `min`), `fallback` (`damping`, `max`: the retry the code makes when the first run does
  not settle), `steps` (the speed search: scan points, refinement passes, bisection tolerance) and `exits`
  (`continue` / `warn` / `fail`, with the runaway temperature). The sources: `FIXED_POINT_*`, `RUNAWAY_C` and the
  `_fixed_point(...)` calls in `solve_point`, the `damping = max(MIN, damping * F)` line and the returned `state`
  values in `propeller_s1._fixed_point`, the `n_points` / `tol_rpm` defaults and the refinement `range(N)` in
  `s1_rating`; `solve_coupled` and `routes/coupled.py` for the EM <-> thermal loops.
* `convergence`: `{iterations, max_iterations, converged, residual, unit}` of the newest stored run of the selected
  motor (read from the run history, which is not touched), else `null`. Only the `/api/coupled` loop has one today;
  the S1 rating is computed on demand and leaves no record.
* Loops that nest cleanly are drawn as nested boxes; two loops that overlap only partly (the modules view's two
  EM <-> thermal loops share the thermal solver) are drawn as a coloured ring on their members.

## How the view draws it

* **One wire per block pair.** Parallel edges between the same two blocks are merged into one wire with a count badge;
  hovering the wire lists the data items. A wire has one colour when its items share a type, grey otherwise.
* **Context inputs are chips.** Edges from a `context` node (and `minor` edges) are not wires: the consuming block shows
  a small chip per source (coloured by the data type, the hover lists the items). The switch **Show all wires** draws every
  link as a wire (the Inputs column stays).
* **Right-angle routing that never crosses what is drawn.** Outputs leave the right side and inputs enter the left side at
  handles ordered by the other end's height, so wires do not cross at the ports. Wires are routed by A* over the grid lines
  of the obstacle edges, 8 px away from every obstacle; a bend, a run on another net's line and a crossing of another wire
  cost extra, so wires keep to their own lines. Obstacles are everything that is drawn: every block (a wire touches only its
  own source and target), every loop group's **header band** (title, rule lines, exit tags), a loop box for a wire with
  neither end inside it (a wire may cross a box only to reach a member, through the border, never through the header),
  the Inputs label, the rings of ring-drawn loops, and the legend card. Feedback wires (and `route: lane`) leave the bottom
  of the block, run in a lane at the bottom of the enclosing loop group (longest span lowest) and enter the bottom of the
  target; wires between stacked blocks (the Inputs column) are brackets on their right side. When a wire finds no clean
  route the layout is widened (columns and rows) and everything is routed again.
* **The check.** `validateWiring(layout, loops, wires, routes)` (web/src/lib/systemGraphRoute.ts) returns every wire
  segment that touches an obstacle it must not touch (`block`, `header`, `loop`, `label`, `extra`), every wire that found no
  route (`unrouted`) and every collinear overlap of two wires of different nets (`overlap`; wires from one source share an
  output bus). The node tests require an empty list on both real views (fixtures of the live graph, with and without a motor
  selected, "Show all wires" off); with "Show all wires" on the count is only reported. In a dev build the app prints a
  console warning when the check finds anything, or when the legend card covers part of the graph. A module without a
  `layout` hint is placed in a row under the hinted ones (the rest of the grid is kept); a backend test fails when a registered
  module has no hint, so a new module gets one.
* **Hover focus.** Hovering a block lights its wires and its chip sources and dims the rest.
* **View.** The graph is fitted to the width of the pane (and to the height when the width alone would push it out of view).
* **Status wording.** Pick a motor (die / config / duty, the lists of the Motors catalogue) at the top of the view: with no
  motor selected no status badge is shown at all. With a motor, a block shows `done` / `running` / `error` / `stored
  result` with its time, or a grey **no run yet** (hover: no stored result for this motor, run it in its Simulation tab)
  when it has no record. The API still answers `state: "unknown"` for such a node; the word is not shown.

## How a module appears in the graph

Every registered module in `modules/bootstrap.default_registry()` becomes a node automatically. Each registry entry must map to a functional owner in M01-M14; unmapped nodes fail graph construction:

1. `manifest().name` is the id, `ui.title` (else the name) the title, `inputs` / `outputs` the ports,
   `capability` the group, `status` the trust level, a `StubModule` is `roadmap: true`.
2. Each `depends_on` capability becomes edges from every provider of that capability (an extension such as
   `geometry.2d.ring_spm` is an alternative provider of `geometry.2d`). The edge carries the contract types the two
   manifests share; with none shared it is a `dependency` edge.
3. A declared input that no dependency covers is wired to the module that outputs that type (`ResultIR` is too
   generic and is never auto-wired).
4. Plugins dropped into `modules/geometry_plugins/` or `modules/component_plugins/` appear once registered and must map to their owning M01-M14 module. Declare accurate `inputs`, `outputs` and `depends_on` in the manifest; the functional owner map is explicit in `system_graph.py`.
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
* Agents propose a composed graph as JSON; the engineer reviews it in the Schemas view before it is run.
