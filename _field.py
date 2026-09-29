"""Debug: train the PINN briefly, then DUMP its fields (A_z real/imag, |B|) over
the motor cross-section to a PNG so we can SEE what the network actually learned
(and understand the weird torque)."""
import logging, numpy as np, math
logging.basicConfig(level=logging.WARNING)
import torch
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from motor_ai_sim.simulation.solver_2d import MagnetostaticsSolver2D, SimConfig
from motor_ai_sim.simulation.geometry_2d import params_from_config

STEPS = 1500
cfg = SimConfig.from_motor_config()
cfg.layer_size = 64; cfg.num_layers = 4; cfg.max_steps = STEPS
cfg.batch_size_interior = 1024; cfg.batch_size_boundary = 512
gp = params_from_config()

# CRITICAL: physicsnemo auto-resumes from any checkpoint in network_dir.  A stale
# step-800 checkpoint makes every "training" run a no-op (bit-identical output),
# silently masking code changes.  Wipe it so we ALWAYS train fresh.
import shutil
_netdir = cfg.output_dir / "network"
if _netdir.exists():
    shutil.rmtree(_netdir)
    print(f"cleared stale checkpoint dir {_netdir}", flush=True)

solver = MagnetostaticsSolver2D(cfg, gp)
solver.build()
from physicsnemo.sym.solver import Solver
s = Solver(cfg=solver._make_modulus_cfg(), domain=solver._domain)
print(f"training {STEPS} steps...", flush=True)
s.solve()

net = solver._net
dev = next(net.parameters()).device
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
    (ax[2], Bmag, "|B| [T]", 2.5)):
    im = a.contourf(X*1000, Y*1000, mm(data), 30, cmap="jet", vmax=vmax)
    a.set_title(title); a.set_aspect("equal"); a.set_xlabel("x [mm]")
    plt.colorbar(im, ax=a, fraction=0.046)
# draw r_rotor_out and r_stator_in circles for reference
for a in ax:
    for r in (gp.r_rotor_out*1000, gp.r_stator_in*1000):
        th = np.linspace(0, 2*np.pi, 200)
        a.plot(r*np.cos(th), r*np.sin(th), "k-", lw=0.4, alpha=0.5)
plt.suptitle(f"PINN fields after {STEPS} steps")
plt.tight_layout()
plt.savefig("/mnt/c/Users/vadim/Projects/motor_ai_sim/pinn_field.png", dpi=85)
print("saved pinn_field.png", flush=True)
print(f"Ar range: [{mm(Ar).min():.4g}, {mm(Ar).max():.4g}]")
print(f"Ai range: [{mm(Ai).min():.4g}, {mm(Ai).max():.4g}]")
print(f"|B| max={mm(Bmag).max():.3f} T  mean={mm(Bmag).mean():.3f} T")
res = solver._postprocess(s)
print(f"torque={res.get('torque_Nm'):.3f} N*m  (FEM ~24.9)  B_max(postproc)={res.get('B_max_T'):.3f}")
