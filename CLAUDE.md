# Motor AI Simulator — Project Guide

## Overview

This is a **2D electromagnetic motor design platform** combining:
- **scikit-fem finite-element solver** (magnetostatic field + torque, transient eddy/loss,
  steady-state thermal) — CPU FEM, **no PINN / NVIDIA Modulus / torch**
- **Real motor geometry from CadQuery** (configurable slots/poles)
- **Optimization (CMA-ES), DOE sweeps, and cost estimation**
- **Web UI** (React) for geometry, simulation, optimization and visualization

**Goal**: Fast, validated 2D FEM motor design (cross-checked against ANSYS FEA)

---

## Quick Start

\\\ash
# Terminal 1: Backend
cd C:\Users\vadim\Projects\motor_ai_sim
python -m uvicorn motor_ai_sim.api:app --port 8001 --reload

# Terminal 2: Frontend
cd web
npm run dev
\\\

Visit: **http://localhost:5173**

---

## Stack

| Layer | Tech | Port |
|-------|------|------|
| Backend | FastAPI (Python) | **8001** |
| Frontend | React + Vite + MUI | **5173** |
| Physics | scikit-fem FEM (magnetostatic / transient / thermal) + gmsh meshing | — |
| Geometry | CadQuery 3D → Shapely 2D polygons | — |

---

## Project Structure

```
motor_ai_sim/
├── CLAUDE.md                          ← Project guide
├── config/motor_config.yaml           ← Single source of truth
├── tests/physics_baseline.json        ← Pinned FEM numbers (the regression gate)
├── src/motor_ai_sim/
│   ├── api.py                         ← FastAPI app
│   ├── cadquery_geometry.py           ← CadQueryMotor class
│   ├── materials.py                   ← Material library (BH, Bertotti, magnets)
│   ├── simulation/
│   │   ├── fem_solver_2d.py           ← THE solver: P2 sliding-band transient
│   │   ├── eddy_solver_2d.py          ← coupled σ·∂A/∂t eddy solve
│   │   └── drive.py                   ← voltage-drive circuit coupling
│   ├── optimization/
│   │   ├── design_eval.py             ← analytic surrogate (anchored on the pins)
│   │   └── refine_proc.py             ← the ONE FEM eval path for every optimizer
│   └── routes/
│       └── simulation.py              ← FEM endpoints (fem_transient, fem_field2d)
└── web/
    └── src/components/simulation/
        ├── PhysicsDashboard.tsx       ← the Simulation tab's FEM view
        ├── FemFieldChart.tsx          ← A_z / |B| / J / loss / temp field inspector
        ├── FemAnimationViewer.tsx     ← frames across one electrical period
        └── TransientCharts.tsx        ← T(t), P(t), V(t) from the transient
```

---

## Motor Specs (active design)

```
Stator:        12 slots, Ø30 mm outer, T-shaped teeth, single-layer winding
Rotor:         14 poles, spoke magnets, 10 mm stack
Magnets:       F45SH NdFeB (Br 1.19 T @120 °C)
Windings:      2S·1P, 6 wires/slot
Operating:     read it off the Simulation tab — never from this file
```

The pinned regression machine (`tests/test_physics_regression.py GEO_30MM`) is a
30 mm 12s14p of the same family; it is what the surrogate calibration and every
pinned number are anchored to.

---

## What's Implemented ✅

### Backend Physics
- ✅ P2 (quadratic) sliding-band transient — the ONLY basis; P1 was deleted
  (radius-inconsistent Maxwell mean torque, ~35 % high under load)
- ✅ Energy / flux-linkage mean torque, with the raw Maxwell series kept beside
  it as the diagnostic
- ✅ Coupled σ·∂A/∂t eddy solve (copper AC, magnet, shaft) — losses from the
  solved field, not a model
- ✅ Voltage drive (currents are the ANSWER) solved as one bordered Newton
- ✅ Per-element irreversible demagnetisation
- ✅ Iron loss from the assigned steel's own Bertotti coefficients
- ✅ Steady-state thermal, coupled EM↔thermal
- ✅ Every optimizer eval goes through `refine_proc` and is REJECTED if any frame
  of its window failed its nonlinear convergence test

### Frontend UI
- ✅ PhysicsDashboard — the Simulation tab, FEM only
- ✅ FemFieldChart — A_z / |B| / J / J⟳ / loss density / temperature / demag maps
- ✅ FemAnimationViewer — one FEM solve per keyframe across the period
- ✅ TransientCharts — T(t), P(t), V(t), summary card

### Deleted, on purpose
The analytic Green's-function endpoints (`/physics`, `/physics/field2d`,
`/physics/torque_sweep`, `/physics/sweep`) and their UI (`MotorField2D`,
`TorqueWaveformChart`, `LossWaveformChart`, `ModelCompare`, `SimulationCharts`)
are gone. They were free-space superposition with μ_r = 1 steel, an amplitude
scaled ×8-10 to look plausible, and Steinmetz constants fitted to a lamination
this project does not use. Every one of their panels had been rendered into
`display: none` or never mounted at all. Do not bring them back as a "fast
screen" without a visible label saying it is not the FEM result.

---

## API Endpoints

### Physics (all FEM)
```
GET  /api/simulation/physics/fem_transient      ← T(t), losses, voltages, summary
GET  /api/simulation/physics/fem_field2d        ← solved field on the real mesh
GET  /api/simulation/physics/thermal_field2d    ← steady-state temperature
GET  /api/simulation/physics/harm_screening     ← honestly labelled fast screen
GET  /api/simulation/mesh/build2d[_sliding_band]
```

### Configuration
```
GET/PATCH /api/simulation/config    → rpm, I_phase, gamma_deg
GET/PATCH /api/winding/config       → connection (2S / 4S / 4P …)
```

---

## Physics Model

### Field
Nonlinear magnetostatics on a gmsh/CDT mesh, P2 elements, sliding band across the
air gap. Steel from the material library's measured BH curve; magnets carry Br
and μ_rec from the assigned material.

### Torque
Energy / flux-linkage mean torque (matches ANSYS). Maxwell stress on the sliding
band over-reads ~37 % under load and is kept only as `T_avg_maxwell_Nm`, the
diagnostic that distinguishes a hybrid regression from a field-solve regression.

### Losses
- **Copper**: ρ(T)·J²·V·k_end for DC, plus the coupled solve's σ∫E² for AC
- **Iron**: the assigned steel's Bertotti kh/kc/ke (fitted from its loss curves)
- **Magnet / shaft**: from the coupled σ·∂A/∂t solve when `rotor_eddy` is on

---

## Working Rules

- **Operating point comes from the Simulation tab.** Never from
  `config/motor_config.yaml`.
- **`tests/test_physics_regression.py` is the gate.** A diff there is the whole
  point of the file — regenerate deliberately and justify every moved line in the
  commit message.
- **No silent substitutions.** If a number is an estimate, the UI says so where
  the user reads it.
- Backend on **8001** (restart after backend changes), vite on **5173**.

---

## References

- Config: `config/motor_config.yaml`
- Solver: `src/motor_ai_sim/simulation/fem_solver_2d.py`
- FEM routes: `src/motor_ai_sim/routes/simulation.py`
- Simulation UI: `web/src/components/simulation/PhysicsDashboard.tsx`

---

**Updated**: 2026-07-29
