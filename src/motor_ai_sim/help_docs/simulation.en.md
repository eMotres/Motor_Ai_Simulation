# Simulations > Electromagnetic

What it is for: the FEM transient of the loaded machine at one operating point: torque and ripple, back-EMF, losses, resistance and inductance, field maps and animation.

The header's last item is the mode of the loaded configuration: pick another to load its point and last stored results, or "+ new mode" to save the current point as one.

Controls, top to bottom:
- Winding Connection, and star or delta.
- Motor or Generator buttons. Generator drives the same current angle shifted 180 electrical degrees, so torque is braking.
- Drive: "Sine current" (you set current, speed and the current-vector angle) or "Target T / P" (you give a target torque in Nm or power in kW and the current is fitted). The PWM inverter itself is set in the Controller tab.
- Coil and magnet temperatures, end-winding factor, "Steps per electrical period", and the mesh preset (changed in the Mesh tab).
- "Coupled thermal - solve for the temperatures": switch on to iterate EM, thermal, EM until the winding and magnet temperatures settle; the cooling comes from the Thermal tab. When on, two menus appear: Drive (Sine current or inverter from Controller, the latter needs a device saved in Controller) and "Solve to" (Steady state, Time to the limits, Continuous rating S1).
- Press Run Simulation (Re-run Simulation after a previous run); in Target T / P press "Fit <torque> Nm & Run". During the fit it says "Fitting current...", then Stop Simulation while the simulation runs. After a Stop, the next run asks "Resume the stopped run?": Continue keeps the finished frames, Start fresh recomputes.

Results: torque, mechanical and input power, efficiency, the loss tiles (copper, stator and rotor iron, magnet eddy), phase resistance, turn inductance, coil current, B max and mean, steps, and the field viewer.

Usual order: load a motor and mode, set connection and operating point, set steps and mesh, run, read the tiles. For coupled runs set the cooling on Thermal first.

A short line under the button says why a run did not happen.
