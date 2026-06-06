"""WSL: load the saved PINN net (pinn_net.pt) and dump its |B| / A_z on a Cartesian
grid + the air-gap B_r/B_φ profile to pinn_bgrid.npz, so a Windows script can put
it side-by-side with the FEM.  No retraining."""
import os, sys, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts"))
import numpy as np
import torch

from pinn_sector_train import make_net, eval_full
from motor_ai_sim.simulation.geometry_2d import params_from_config

ROOT = os.path.dirname(__file__)
gp = params_from_config()
dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
net = make_net(gp).to(dev)
net.load_state_dict(torch.load(os.path.join(ROOT, "pinn_net.pt"), map_location=dev))
net.eval()

R = gp.r_stator_out
N = 200
xs = np.linspace(-R, R, N)
X, Y = np.meshgrid(xs, xs)
Ar, _ = eval_full(net, X.ravel(), Y.ravel(), dev)
Ar = Ar.reshape(N, N)
dx = xs[1] - xs[0]
Bx = np.gradient(Ar, dx, axis=0)
By = -np.gradient(Ar, dx, axis=1)
B = np.sqrt(Bx ** 2 + By ** 2)

# air-gap B_r / B_phi vs angle
rg = 0.5 * (gp.r_rotor_out + gp.r_stator_in)
th = np.linspace(0, 2 * math.pi, 360, endpoint=False)
eps = 1e-4
ArA, _ = eval_full(net, (rg + eps) * np.cos(th), (rg + eps) * np.sin(th), dev)
ArB, _ = eval_full(net, (rg - eps) * np.cos(th), (rg - eps) * np.sin(th), dev)
Ar2, _ = eval_full(net, rg * np.cos(th + 1e-3), rg * np.sin(th + 1e-3), dev)
Ar0, _ = eval_full(net, rg * np.cos(th), rg * np.sin(th), dev)
B_r = (1.0 / rg) * (Ar2 - Ar0) / 1e-3
B_phi = -(ArA - ArB) / (2 * eps)

np.savez(os.path.join(ROOT, "pinn_bgrid.npz"),
         X=X, Y=Y, Ar=Ar, B=B, R=R, r_shaft=gp.r_shaft_in, rg=rg,
         th=th, B_r=B_r, B_phi=B_phi)
print(f"saved pinn_bgrid.npz  |B|max={B.max():.3f}T  Bphi_pp={B_phi.max()-B_phi.min():.3f}T")
