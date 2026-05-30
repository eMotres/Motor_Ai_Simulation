# Motor AI Simulator — Project Summary

## Stack
- **Backend**: FastAPI + uvicorn (Python), port **8001** (new code), port 8000 (stale — cannot kill from bash)
- **Frontend**: React + Vite + MUI + Recharts, port **5173**
- **Env**: `web/.env.local` → `VITE_API_URL=http://localhost:8001`
- **Simulation**: NVIDIA Modulus PINN (dry-run mode, Modulus not installed)

## Motor Config (current design)
```
Stator Ø150mm · 24 slots · core 4.2mm · tooth 9.2/5.5mm · slot 14×5mm×0.6mm wire
Rotor: 28 poles (14 pairs) · magnet 16mm · motor length 35mm
Materials: 20SW1200 steel · F45SH_120C magnets · Cu windings · Al6061 shaft
```

## Operating Point
```
rpm = 3950 · I_phase = 85 Arms · f = 921.67 Hz (= 3950 × 14 / 60)
Connection: 2P·2S (2 parallel × 2 series, 4 coils/phase, 14 wires/slot)
I_coil = 42.5 Arms · I_coil_peak = 60.1 A (sent to solver)
A·turns/slot = 595 At
phase_offset γ = 0° (d-axis; use 90° for q-axis / max torque SPMSM)
Electrical period = 25.71° · Cogging period = 2.143° · 12 cogging/elec
```

## Architecture

### Backend routes (`src/motor_ai_sim/`)
| Endpoint | Description |
|---|---|
| `GET/PUT /api/geometry` | Motor geometry params (YAML-backed) |
| `GET /api/geometry/summary` | num_poles, num_slots, radii |
| `GET/PATCH /api/materials` | Material assignments |
| `GET /api/materials/library` | Full material DB (steel/magnet/conductor) |
| `GET/PATCH /api/mesh/config` | PINN collocation grid (n_radial, n_angular) |
| `GET/PATCH /api/winding/config` | Winding connection (2P2S, 4S, 4P) |
| `GET /api/simulation/status` | Modulus availability + operating point |
| `POST /api/simulation/run` | Start async PINN job → job_id |
| `GET /api/simulation/result/{id}` | Poll job result |
| `GET/PATCH /api/simulation/config` | rpm, current, frequency, phase_offset |

### Simulation module (`src/motor_ai_sim/simulation/`)
- **`pdes.py`**: `Magnetostatics2D`, `PermanentMagnet2D`, nonlinear Froelich PDE
- **`geometry_2d.py`**: `MotorDomainParams`, `MotorDomains2D` (CSG: stator, air-gap, rotor, magnets, slots, shaft)
- **`solver_2d.py`**: `SimConfig` (loads from YAML), `MagnetostaticsSolver2D`
  - `build()`: assembles Modulus domain + 3-phase slot currents from θ_rotor + γ
  - `_dry_run()`: returns copper losses analytically (no Modulus needed)
  - `_postprocess()`: torque (Maxwell), Fe losses (Bertotti), Mg eddy, efficiency
- **`postprocess.py`**: `compute_copper_losses`, `compute_iron_losses_by_domain`, `compute_magnet_losses`, `compute_efficiency`

### Frontend tabs (`web/src/`)
| Tab | Component | Status |
|---|---|---|
| Geometry | `ParameterVariationTable` | Manual RECALCULATE button, yellow dirty highlight |
| Materials | `MaterialsLibraryTree` + `MaterialDetailView` + `MotorScene` (click-to-assign) | ✅ |
| Mesh | `MeshPanel` | Sliders + live polar SVG collocation preview |
| Simulation | `SimulationPanel` + `SimulationCharts` | Full waveform charts (recharts) |
| Sweep | `SweepConfigPanel` | ✅ |

### Simulation UI (`SimulationPanel.tsx`)
Left panel:
- Solver badge (Dry-run / NVIDIA Modulus)
- Winding connection: **4S | 2P·2S | 4P** buttons + I_coil / A·turns display
- Operating point: I_phase, frequency, rpm, rotor angle (clamped to elec. period), **phase offset γ**
- Slot currents A/B/C live bar chart (updates with θ + γ)
- Rotor Periodicity card: pole pairs, electrical period, cogging period, samples needed
- PINN training: steps, CPU/CUDA toggle
- **RUN SIMULATION** button

Right panel:
- Governing equation + domain chips
- Job progress + status
- **Results**: Power balance (T, P_mech, P_input), Losses (Cu/Fe-stat/Fe-rot/Mag), Winding params, B field
- **Waveforms — One Electrical Period** (SimulationCharts):
  - Phase Currents i_A/i_B/i_C vs θ_mech (analytical)
  - Phase Voltages V_A/V_B/V_C vs θ (R·i + X_L·i approx)
  - Copper losses P_cu vs θ (constant for balanced 3-phase)
  - Torque T(θ) placeholder (needs Modulus) or live data

## Current Issues / TODOs
1. **Old uvicorn on :8000 stuck** — PIDs 26080/45180 not killable from bash. New backend on **:8001**. User must restart manually: `python -m uvicorn motor_ai_sim.api:app --port 8000 --reload`
2. **NVIDIA Modulus not installed** — all torque/iron/magnet results are `null`. Install: `pip install nvidia-physicsnemo`
3. **SimulationCharts nParallel fix pending** — chart uses I_phase_peak = I_coil_peak × nParallel; `nParallel` prop added but not yet wired from SimulationPanel
4. **Voltage chart is approximate** — uses R + X_L only, no back-EMF (needs PINN flux linkage)

## Key Files
```
config/motor_config.yaml          — single source of truth for geometry + materials + simulation
web/.env.local                    — VITE_API_URL=http://localhost:8001
src/motor_ai_sim/api.py           — FastAPI app, all route registrations
src/motor_ai_sim/config.py        — YAML loader with cache
src/motor_ai_sim/materials.py     — material library (steel/magnet/conductor)
src/motor_ai_sim/simulation/      — PINN solver module
web/src/stores/motorStore.ts      — Zustand store, all geometry/sweep state
web/src/components/simulation/    — SimulationPanel + SimulationCharts
```

## Git
- Remote: `https://github.com/eMotres/Motor_Ai_Simulation.git`
- Branch: `main`
- Last commit: `f5c9553` — Add loss calculations and efficiency to simulation results
