# Multi-user / personal workspace — migration plan

_Status as of 2026-06-18. Goal: each signed-in user gets an isolated, persistent
workspace; no cross-user clobbering; works on stateless, auto-scaling Cloud Run._

## Where we already are (DONE)

- **Auth / registration** — Firebase Google sign-in (`web/src/contexts/AuthContext.tsx`,
  `components/auth/AuthButton.tsx`). First sign-in = registration.
- **Token on every call** — fetch interceptor (`lib/apiAuth`) auto-attaches the
  Firebase ID token (`Bearer`) to all backend requests.
- **Backend verification** — `src/motor_ai_sim/auth.py` verifies the token
  (RS256 vs Google public certs, no secrets, Cloud-Run-friendly) → `{uid,email,tier}`.
- **Tier gate** — `TierGateMiddleware` gates expensive endpoints (FEM, optimize,
  CAD) by tier (anon→free→pro→team→admin). **OFF** until `AUTH_ENFORCE=1`.
- **Per-user library** — `MyDesigns.tsx` saves/loads/deletes the user's designs in
  Firestore `users/{uid}/designs`. Isolated by uid.

## The one real gap

The **active working state** is a single global `config/motor_config.yaml` on the
backend. Geometry edits + simulation read/write it. So:
- saved designs = per user ✅, but the **live sandbox = shared by everyone** ❌.
- Also wrong for Cloud Run: each instance has its own file → races on scale-out.

## Target architecture

Backend becomes **stateless w.r.t. the active design**. The active design lives in
the frontend store (session) + `users/{uid}/active` (Firestore, persistent). The
FEM/geometry/mesh endpoints receive the geometry **as input per request** (the
optimizer already does this via `geo_override`). No shared mutable file.

## Phases

- **P0 — auth + per-user library** — DONE.
- **P1 — stateless endpoints (backend, additive & back-compatible):** add an
  optional `geo` (full geometry dict) parameter to the geometry-/sim-deriving
  endpoints; use it when present, else fall back to the global config. Targets:
  `/api/geometry/mesh|mesh2d|mesh_extruded`, `/api/config` (read),
  `/api/simulation/physics/*`, `/api/simulation/mesh/*`, `/api/simulation/status`.
  Nothing breaks (default path unchanged).
- **P2 — frontend sends the active geometry** with each request (from the store),
  so a signed-in user no longer depends on the shared backend config.
- **P3 — persist the active workspace** to `users/{uid}/active` (debounced
  auto-save; load on sign-in). Reuses the `MyDesigns` Firestore layer.
- **P4 — enforce + tiers:** set `AUTH_ENFORCE=1` + `ADMIN_EMAILS` on Cloud Run.
  Decide the free-tier limits + the anonymous experience (read-only demo vs
  local-only). NB: confirm the owner's sign-in email first to avoid lock-out.
- **P5 — billing (later):** Stripe → `tier` custom claim (auth.py already honours it).

## Risks / decisions

- Many endpoints read the global config → migrate **incrementally** (additive `geo`
  param first across all, flip the default last).
- Anonymous UX: demo (read-only) or local-only (no backend writes)? — decide before P4.
- Payload: a geometry dict is small (a few dozen numbers) → sending it per request
  is cheap.
- Order matters: do the **isolation (P1–P3) before enforcing (P4)** — otherwise
  signed-in users still share one sandbox.
