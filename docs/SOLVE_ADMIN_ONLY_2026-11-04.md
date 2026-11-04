# Who may start a calculation (SOLVE_ADMIN_ONLY, 2026-11-04)

Owner 2026-11-04: nobody except the admin starts a simulation for now; it must be easy to reopen.

## The setting

`MOTRES_SOLVE_ADMIN_ONLY` (env of the API and the worker; `auth.solve_admin_only()`, read per call).

* unset / `1` / `true`: closed. Only an admin account starts a calculation.
* `0` / `false` / `no` / `off`: open. Every route is back at the tier it had before this rule.

To reopen: set `MOTRES_SOLVE_ADMIN_ONLY=0` in `api.env`, restart the API colours and the worker.

## How it is enforced (three locks, one rule)

1. **Role gate** (`auth.RoleGateMiddleware`, tier tables in `auth.py`). A solve route carries the tier `solve:<old tier>`
   (`open` = it had no tier). `required_role` resolves it to `admin` while the setting is on, to the old tier when it is off.
   A signed-in non-admin gets `403 {"detail": "Simulations are run by MOTRES for now.", "code": "solve.admin_only"}`, an anonymous
   caller 401. Path-parameter routes use `_GATED_PATTERN` (the Tests "Compute it", the catalog passport).
2. **Job queue** (`jobs.require_solve_allowed`, called by `jobs.queued`, `jobs.admit` and the Tests compute). Same refusal for a
   caller that is not an admin, for a path that never meets the middleware. A call already inside an admitted job runs on:
   nothing running or queued is cancelled when the rule is switched on.
3. **MCP** (`agent_designs.simulate`). `/mcp` is the outermost ASGI layer and never meets the role gate, so the tool answers
   `{"status": "simulations_closed", "user_message": "Simulations are run by MOTRES for now.", "design_id": ...}` as data.
   `start_design` (analytic draft) and every read tool stay open. The answer carries no price word (`mcp_tools.no_commercial`).

The web asks `/api/me` (`canSolve`) and disables Run / Solve / Recompute / Animation behind one component, `SolveGate`
(tooltip `common:solveClosed`). A refused call that slips through shows the same sentence (`refused.solveClosed`, `errors.json`
`solve.admin_only`).

## The solve class

See `auth._GATED` (`solve:` entries), `auth._GATED_PREFIX` and `auth._GATED_PATTERN`; the full list with the tier before and
after is pinned in `tests/test_solve_admin_only.py` (`SOLVE_ROUTES`).

## A new route

`tests/test_solve_admin_only.py::test_every_route_that_can_start_a_solve_is_admin_only_or_reviewed` walks every route. A route
the compute worker serves (`roles.WORKER_PREFIXES`) or whose endpoint source submits a job must be an admin route while the
setting is on, or be named in `REVIEWED_NOT_A_SOLVE` (a read, a preview, progress, cancel, a store write). The test fails
otherwise: give the route a `solve:<old tier>` entry or review it into the list.

## Deliberately left open (reviewed)

Stored-result reads (`/last`, `em_run`), progress and cancel routes, closed-form calculators that Configure and the Mechanical
panel call (bearing fit, holder strength, propeller S1, bearing losses), the controller schematic, the CAD rebuild request,
the design agent, MCP `start_design`, the specification assistant, reports and datasheets.

## View-only Simulations workbench (owner 2026-11-04, follow-up)

«это касается только simulation; для Configure ничего не меняется».  Under the same setting:

* the preview meshes of the workbench tabs (`GET /api/simulation/mesh/build2d`, `build2d_sliding_band`, `/api/thermal/mesh`,
  `/api/mechanical/mesh`) and the Simulation tab's stored-answer lookup (`fem_transient/ledger_match`) are solve routes (admin
  while the setting is on); no Configure, 3-D or project view calls them;
* the tab gate `workbench` (`web/src/lib/accessUi.ts`) shows Electromagnetic, Thermal, Mechanical and Schemas only to an account
  that `/api/me` says `canSolve`; the other tabs (Geometry, Materials, Mesh, Controller, Compare ...) keep their old gates;
  stored points stay readable in the project views (#578).

Reopening is the same single switch.  `tests/test_solve_admin_only.py` also walks every route of the compute modules
(`COMPUTE_MODULES`): admin-only or reviewed with a reason; an unclassified router module fails the walk.
