# Electric Motor AI Simulator

A full-stack **2-D FEM design platform for BLDC/PMSM motors** — parametric geometry,
real finite-element electromagnetics, thermal, optimization and cost estimation, with a
browser workbench.

> **Note:** the electromagnetics are solved with a classical **finite-element method
> (scikit-fem + gmsh, on CPU)** — there is **no** PINN / neural-network / NVIDIA Modulus /
> PhysicsNeMo / PyTorch dependency. (An earlier prototype targeted Modulus PINNs; that path
> was removed — only inert, guarded references may remain in a few legacy modules.)

## Features

- 🧲 Parametric 2-D geometry of BLDC/PMSM motors (CadQuery + gmsh)
- 🧮 **FEM electromagnetics** — magnetostatic field, time-stepped transient with a
  sliding air-gap band (torque, back-EMF, iron/copper/magnet losses, demagnetisation)
- 🌡️ Steady-state **thermal** map (scikit-fem conduction; air / liquid cooling models)
- 📈 **Optimization** — two searches on one button: *Explore* (CMA-ES) and
  *Refine* (screening descent: deviate every variable, descend the influential
  few, polish with the rest — see `docs/SCREENING_DESCENT.md`) + DOE sweeps over
  geometry and operating point
- 💶 **Cost estimation** — material masses × prices + labour, by line item
- 🧊 3-D viewer, field/loss/temperature colour maps, saved-motor catalog

## Architecture

- **Backend** — FastAPI (Python). FEM via scikit-fem + gmsh; geometry via CadQuery;
  optimization via CMA-ES. Containerised (`Dockerfile` → `requirements.txt`), runs on
  Cloud Run.
- **Frontend** — React + Vite + TypeScript + MUI + Three.js (`web/`), deployed to
  Firebase Hosting.

## Run locally

```bash
# Backend (FastAPI on :8001)
pip install -r requirements.txt
PYTHONPATH=src uvicorn motor_ai_sim.api:app --port 8001

# Frontend (Vite dev server on :5173)
cd web && npm install && npm run dev
```

## Physics

Magnetostatics from Maxwell's equations, in terms of the magnetic vector potential **A**
(**B = ∇ × A**):

- **∇ × H = J** (Ampère), **∇ · B = 0**, **B = μH**
- ⇒ **∇·(ν ∇A_z) = −J_z** — solved per time step on the FEM mesh; torque from the
  Maxwell stress tensor on the air-gap circle, losses from the cycle-averaged field.

## License

MIT License

## References

- [scikit-fem](https://github.com/kinnala/scikit-fem) · [gmsh](https://gmsh.info/)
- [Maxwell's Equations](https://en.wikipedia.org/wiki/Maxwell%27s_equations)
- [BLDC Motor Design](https://en.wikipedia.org/wiki/Brushless_DC_electric_motor)
