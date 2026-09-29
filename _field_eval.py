"""EVAL-ONLY: load the saved PINN checkpoint (no training) and dump the fields
(A_z real/imag, |B|) + torque, so we can SEE what the magnet-source + Fourier
net learned so far without paying for (slow, hot) re-training."""
import logging, numpy as np, math, glob, os
logging.basicConfig(level=logging.WARNING)
import torch
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from motor_ai_sim.simulation.solver_2d import MagnetostaticsSolver2D, SimConfig
from motor_ai_sim.simulation.geometry_2d import params_from_config

cfg = SimConfig.from_motor_config()
cfg.layer_size = 64; cfg.num_layers = 4
cfg.batch_size_interior = 1024; cfg.batch_size_boundary = 512
gp = params_from_config()

solver = MagnetostaticsSolver2D(cfg, gp)
solver.build()                       # builds the SAME Fourier arch
net = solver._net
dev = next(net.parameters()).device

ckpt = str(cfg.output_dir / "network" / "A_phasor_network.0.pth")
sd = torch.load(ckpt, map_location=dev)
if isinstance(sd, dict) and "model_state_dict" in sd:
    sd = sd["model_state_dict"]
missing, unexpected = net.load_state_dict(sd, strict=False)
print(f"loaded {ckpt}\n  missing={len(missing)} unexpected={len(unexpected)}", flush=True)
net.eval()

R = gp.r_stator_out
N = 240
xs = np.linspace(-R, R, N); ys = np.linspace(-R, R, N)
X, Y = np.meshgrid(xs, ys)
with torch.no_grad():
    out = net({"x": torch.tensor(X.ravel().astype("float32")).reshape(-1, 1).to(dev),
               "y": torch.tensor(Y.ravel().astype("float32")).reshape(-1, 1).to(dev)})
Ar = out["Ar"].cpu().numpy().reshape(N, N)
Ai = out["Ai"].cpu().numpy().reshape(N, N)
rr = np.sqrt(X**2 + Y**2)
mask = (rr > R) | (rr < gp.r_shaft_in)
dx = xs[1] - xs[0]
Bx = np.gradient(Ar, dx, axis=0)
By = -np.gradient(Ar, dx, axis=1)
Bmag = np.sqrt(Bx**2 + By**2)
mm = lambda a: np.ma.masked_where(mask, a)

fig, ax = plt.subplots(1, 3, figsize=(16, 5.2))
for a, data, title, vmax in (
    (ax[0], Ar, "A_z REAL (Ar) [Wb/m]", None),
    (ax[1], Ai, "A_z IMAG (Ai)", None),
    (ax[2], Bmag, "|B| [T]", None)):
    im = a.contourf(X*1000, Y*1000, mm(data), 40, cmap="jet", vmax=vmax)
    a.set_title(title); a.set_aspect("equal"); a.set_xlabel("x [mm]")
    plt.colorbar(im, ax=a, fraction=0.046)
for a in ax:
    for r in (gp.r_rotor_out*1000, gp.r_stator_in*1000):
        th = np.linspace(0, 2*np.pi, 200)
        a.plot(r*np.cos(th), r*np.sin(th), "k-", lw=0.4, alpha=0.5)
plt.suptitle("PINN fields — magnet source (curl M = M_phi/r) + Fourier net (partial train)")
plt.tight_layout()
plt.savefig("/mnt/c/Users/vadim/Projects/motor_ai_sim/pinn_field.png", dpi=85)
print("saved pinn_field.png", flush=True)
print(f"Ar range: [{mm(Ar).min():.4g}, {mm(Ar).max():.4g}]")
print(f"|B| max={mm(Bmag).max():.3f} T  mean={mm(Bmag).mean():.3f} T")
# angular |B| profile in the air gap → count poles
r_ag = 0.5*(gp.r_rotor_out + gp.r_stator_in)
th = np.linspace(0, 2*np.pi, 360, endpoint=False)
xg = r_ag*np.cos(th); yg = r_ag*np.sin(th)
with torch.no_grad():
    og = net({"x": torch.tensor(xg.astype("float32")).reshape(-1,1).to(dev),
              "y": torch.tensor(yg.astype("float32")).reshape(-1,1).to(dev)})
Arg = og["Ar"].cpu().numpy().reshape(-1)
# radial B in air gap ~ -dA/dtheta /r ... just FFT A_z(theta) to see dominant pole #
sp = np.abs(np.fft.rfft(Arg - Arg.mean()))
top = np.argsort(sp)[::-1][:6]
print("dominant angular harmonics of A_z(theta) in air gap:", sorted(top.tolist()))
print("  (28 poles → expect a strong harmonic at 14)")
