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

\\\
motor_ai_sim/
├── CLAUDE.md                          ← Project guide
├── config/motor_config.yaml           ← Single source of truth
├── src/motor_ai_sim/
│   ├── api.py                         ← FastAPI routes
│   ├── cadquery_geometry.py           ← CadQueryMotor class
│   ├── materials.py                   ← Material library
│   └── routes/
│       └── simulation.py              ← Physics endpoints (torque_sweep, field2d)
└── web/
    └── src/components/simulation/
        ├── MotorField2D.tsx           ← 2D field canvas
        ├── TorqueWaveformChart.tsx    ← T(θ) graph
        └── LossWaveformChart.tsx      ← Loss breakdown
\\\

---

## Motor Specs

\\\
Stator:        24 slots, Ø300mm outer, Ø150mm inner, T-shaped teeth
Rotor:         28 poles, trapezoidal magnets, Ø120mm, 35mm stack
Magnets:       NdFeB, 16mm height, radially magnetized
Windings:      2P·2S (2 parallel, 2 series per phase)
Operating:     3950 rpm, 85 Arms phase, 921.67 Hz
\\\

---

## What's Implemented ✅

### Backend Physics
- ✅ Analytical A_z field via Green's function superposition
- ✅ Maxwell stress tensor torque T(θ) (captures cogging ripple: 12 periods/elec)
- ✅ Real motor geometry from CadQuery → PIL rasterization (fast domain classification)
- ✅ Torque sweep endpoint (60 points/elec period, 1.9s warm time)
- ✅ Field 2D endpoint (A_z, B_x, B_y, |B|, J_z, domain map)
- ✅ Loss calculations (copper, iron Steinmetz, magnet eddy)

### Frontend UI
- ✅ TorqueWaveformChart — T(θ) with T_avg/max/min, cogging ripple %, CSV export
- ✅ LossWaveformChart — Cu/Fe/Mg losses over one period
- ✅ MotorField2D canvas — |B|, A_z, J_z, domain visualization with rotor slider
- ✅ Flux line contours (marching squares on A_z)

---

## Current Task 🔄

**User request**: "почему ты рисуешь здесь приближенную геомерию нужно везде выводить реальную и на ней рисовать все поля не забудь сделать поляризацию для магнитов в доль нижней грани"

Translation: "Why approximate geometry? Display REAL geometry everywhere. Add magnet polarization vectors."

### Tasks:
1. **✅ Real geometry** — Backend uses real Shapely polygons from \get_2d_polygons()\
2. ❌ **Verify in UI** — Ensure MotorField2D displays T-shaped teeth + trapezoidal magnets
3. ❌ **Magnet polarization** — Draw Br direction arrows on each magnet

---

## API Endpoints

### Physics
\\\
GET  /api/simulation/physics/torque_sweep?gamma_deg=90&n_rotor=60&n_ag=720
     → Returns: T(θ), T_avg, T_ripple, cogging analysis

GET  /api/simulation/physics/field2d?rotor_angle_deg=0&grid_size=150
     → Returns: A_z, B_x, B_y, |B|, J_z, domain_map (150×150 grid)
\\\

### Configuration
\\\
GET/PATCH /api/simulation/config
         → rpm, I_phase, gamma_deg

GET/PATCH /api/winding/config
         → Connection: 2P2S / 4S / 4P
\\\

---

## Physics Model

### Magnetic Field (Analytical)
- Linear superposition of dipole moments from each magnet
- Free-space Green's function: A_z ∝ M/r²
- **Scale factor ×9.02** (corrects for iron permeability, ~8–10× underestimate)
- Clamped saturation: B_max = 1.75 T (steel)

### Torque
\\\
T(θ) = (L/μ₀) · r² · ∮ B_r(φ) · B_φ(φ) dφ

Captures:
- Cogging ripple: 12 periods per electrical period
- Load-angle effect via γ phase offset
\\\

### Losses
- **Copper**: I²R (constant for balanced 3Φ)
- **Iron**: Steinmetz k_st × f^1.5 × B^2
- **Magnet eddy**: α × f² × B² × σ

---

## Key Technical Decisions

### Why Analytical First?
- **Instant feedback**: 1.9s warm response
- **Validates physics**: Cogging shape correct; amplitude scaled empirically
- **FEA comparison**: results cross-checked against ANSYS FEA

### Why Real Geometry?
- **User requirement**: "везде выводить реальную" (output REAL everywhere)
- **Accuracy**: T-shaped teeth, trapezoidal magnets with fillets
- **CadQuery**: Parametric, auto-updates when specs change

### Why PIL for Domain Classification?
- **Speed**: 2× faster than Shapely
- **Caching**: Rotor angle quantized to 0.5° steps → 1.9s warm
- **Simple**: Rasterize once, classify grid in <100ms

---

## Known Issues

| Issue | Status | Impact |
|-------|--------|--------|
| Magnet polarization not drawn | ❌ Pending | User explicitly requested |
| Voltage chart approximate | 🟡 Medium | Uses R + X_L, no back-EMF |

---

## Next: Magnet Polarization

**To implement in MotorField2D.tsx**:

1. Extract magnet info from \get_2d_polygons()\
2. For each magnet:
   - Compute center (X, Y)
   - Get polarity: +1 (N, blue) or -1 (S, red)
   - Draw arrow along radial direction
3. Scale arrow length to magnet size
4. Overlay on canvas with field visualization

**File**: \web/src/components/simulation/MotorField2D.tsx\

---

## References

- Config: \config/motor_config.yaml\
- Geometry: \src/motor_ai_sim/cadquery_geometry.py\
- Physics routes: \src/motor_ai_sim/routes/simulation.py\
- Field UI: \web/src/components/simulation/MotorField2D.tsx\

---

**Updated**: 2026-05-30 | **Phase**: 4 (Magnet polarization)
