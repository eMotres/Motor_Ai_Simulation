"""Sector PINN trainer — 1/4 symmetry (90° wedge) with ANTI-PERIODIC BC.

Why a custom loop instead of physicsnemo's Solver:
  * physicsnemo has NO anti-periodic-BC constraint (only pointwise/integral),
    and the 90° sector of a 24-slot/28-pole machine has 7 poles → ODD → the
    field is anti-periodic across the cut:  A_z(φ+90°) = −A_z(φ).
  * the Solver black-box silently auto-resumed stale checkpoints (every "train"
    was a no-op) and trains opaquely/slowly.
A hand-written loop gives: exact anti-periodic BC (just eval the net at the two
matched cut points and penalise their SUM), a real mid-loop GPU thermal guard,
live web progress, and a 4× smaller domain (6 slots + 7 magnets) → ~4-8× faster
AND cooler.

Geometry is REUSED from MotorDomains2D (not re-implemented) — material μ_r /
σ / sources come straight from the existing per-region builders.

Run (WSL venv):
  LD_LIBRARY_PATH=/usr/lib/wsl/lib PYTHONPATH=src \
    python scripts/pinn_sector_train.py [STEPS]
"""
import sys, os, json, time, math, subprocess, logging
logging.basicConfig(level=logging.WARNING)
import numpy as np
import torch
import matplotlib
matplotlib.use("Agg")
from matplotlib.path import Path as _MplPath   # numpy point-in-polygon (no shapely)

from physicsnemo.sym.models.fourier_net import FourierNetArch
from physicsnemo.sym.key import Key
from physicsnemo.sym.models.activation import Activation

from motor_ai_sim.simulation.solver_2d import MagnetostaticsSolver2D, SimConfig
from motor_ai_sim.simulation.geometry_2d import params_from_config
from motor_ai_sim.simulation.pdes import MU_0

_trapz = getattr(np, "trapezoid", None) or np.trapz

ROOT     = "/mnt/c/Users/vadim/Projects/motor_ai_sim"
PROGRESS = os.path.join(ROOT, "pinn_progress.json")

# ── thermal guard ───────────────────────────────────────────────────────────
CHECK_EVERY   = 25
TEMP_PAUSE_C  = 84
TEMP_RESUME_C = 76
TORQUE_FEM    = 24.87
SECTOR        = math.pi / 2          # 90°  (GCD(24,28)=4)


def gpu_temp():
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=temperature.gpu",
             "--format=csv,noheader,nounits"], timeout=5).decode().strip().splitlines()[0]
        return int(out)
    except Exception:
        return -1


def write_progress(d):
    tmp = PROGRESS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.replace(tmp, PROGRESS)


# ─────────────────────────────────────────────────────────────────────────────
#  Sector collocation sampling  (reuses MotorDomains2D classification)
# ─────────────────────────────────────────────────────────────────────────────
def in_sector(x, y):
    phi = np.arctan2(y, x)
    return (phi >= -1e-9) & (phi <= SECTOR + 1e-9)


def sample_wedge_band(n, r_in, r_out):
    """Uniform-area points in the 90° wedge between r_in and r_out."""
    r   = np.sqrt(np.random.uniform(r_in**2, r_out**2, n))
    phi = np.random.uniform(0.0, SECTOR, n)
    return r * np.cos(phi), r * np.sin(phi)


# ── REAL geometry (exported by _export_geom.py → pinn_geom.json, metres) ──────
_GEOM = None
_POOL: dict = {}


def _load_geom():
    global _GEOM
    if _GEOM is None:
        with open(os.path.join(ROOT, "pinn_geom.json")) as f:
            _GEOM = json.load(f)
    return _GEOM


def _rings_paths(rings):
    return [(_MplPath(np.asarray(r["ext"], float)),
             [_MplPath(np.asarray(h, float)) for h in r.get("holes", [])])
            for r in rings]


def _inside(paths, P):
    ins = np.zeros(len(P), bool)
    for ext, holes in paths:
        m = ext.contains_points(P)
        for h in holes:
            m &= ~h.contains_points(P)
        ins |= m
    return ins


def _sample_in_rings(rings, n, exclude_paths=None):
    """Rejection-sample n points INSIDE the polygon (ext minus holes), clipped to
    the 90° sector, optionally excluding other polygons (e.g. magnets ⊂ rotor)."""
    paths = _rings_paths(rings)
    allext = np.vstack([np.asarray(r["ext"], float) for r in rings])
    lo, hi = allext.min(0), allext.max(0)
    gx, gy = [], []
    have = 0; tries = 0
    while have < n and tries < 80:
        tries += 1
        bx = np.random.uniform(lo[0], hi[0], n * 4)
        by = np.random.uniform(lo[1], hi[1], n * 4)
        P = np.column_stack([bx, by])
        ins = _inside(paths, P)
        ph = np.arctan2(by, bx)
        ins &= (ph >= -1e-9) & (ph <= SECTOR + 1e-9)
        if exclude_paths:
            ins &= ~_inside(exclude_paths, P)
        gx.append(bx[ins]); gy.append(by[ins]); have += int(ins.sum())
    X = np.concatenate(gx)[:n] if gx else np.zeros(0)
    Y = np.concatenate(gy)[:n] if gy else np.zeros(0)
    return X, Y


def _in_q1(cx, cy):
    a = math.degrees(math.atan2(cy, cx))
    return -1e-6 <= a <= 90.0 + 1e-6


def _build_pool(solver):
    """Sample large per-region pools INSIDE the real FEM polygons (once)."""
    gp, cfg = solver.gp, solver.cfg
    G = _load_geom(); meta = G["meta"]
    omega = 2 * math.pi * cfg.frequency_hz
    pole_pairs = gp.num_poles // 2
    theta_elec = math.radians(cfg.rotor_angle_deg * pole_pairs + cfg.phase_offset_deg)
    I_phase = {'A': cfg.I_peak * math.cos(theta_elec),
               'B': cfg.I_peak * math.cos(theta_elec - 2 * math.pi / 3),
               'C': cfg.I_peak * math.cos(theta_elec + 2 * math.pi / 3)}
    mur_fe = min(float(meta["mu_r_fe"]), 500.0)

    Xs, Ys, IM, CS, SR = [], [], [], [], []

    def add(X, Y, mur, sigma, src):
        n = X.size
        if n == 0:
            return
        Xs.append(X); Ys.append(Y)
        IM.append(np.full(n, 1.0 / mur))
        CS.append(np.full(n, MU_0 * omega * sigma))
        SR.append(src if np.ndim(src) else np.full(n, MU_0 * src))

    # magnet exclusion paths (so rotor iron doesn't overlap the embedded magnets)
    mag_excl = []
    for m in G["magnets"]:
        mag_excl += _rings_paths(m["rings"])

    add(*_sample_in_rings(G["stator"], 4000), mur_fe, 0.0, 0.0)            # stator iron (slots excluded as holes)
    add(*_sample_in_rings(G["rotor"], 2500, exclude_paths=mag_excl), mur_fe, 0.0, 0.0)  # rotor iron between magnets
    add(*_sample_in_rings(G["shaft"], 1200), 1.0, 0.0, 0.0)               # shaft
    add(*_sample_in_rings(G["air_gap"], 1500), 1.0, 0.0, 0.0)             # air gap

    n_coil = 0
    for c in G["coils"]:
        if not _in_q1(c["cx"], c["cy"]):
            continue
        n_coil += 1
        X, Y = _sample_in_rings(c["rings"], 1500)
        J = c["direction"] * I_phase[c["phase"]] * meta["n_wires"] / max(c["area_m2"], 1e-12)
        add(X, Y, 1.0, gp.sigma_cu, J)

    n_mag = 0
    for m in G["magnets"]:
        if not _in_q1(m["cx"], m["cy"]):
            continue
        n_mag += 1
        X, Y = _sample_in_rings(m["rings"], 1500)
        r = np.sqrt(X**2 + Y**2)
        # curl(M)=M_φ/r for tangential M (= FEM's ±M_mag·tangent_CCW direction)
        Jmag = m["polarity"] * (meta["Br"] / MU_0) / np.maximum(r, 1e-6)
        add(X, Y, 1.05, gp.sigma_mag, MU_0 * Jmag)

    _POOL.update(X=np.concatenate(Xs), Y=np.concatenate(Ys),
                 IM=np.concatenate(IM), CS=np.concatenate(CS), SR=np.concatenate(SR),
                 n_coil=n_coil, n_mag=n_mag, r_so=meta["r_stator_out"])


def build_sector_points(solver: MagnetostaticsSolver2D, verbose: bool = True):
    """Minibatch from the REAL-polygon pools (interior) + fresh BC & anti-periodic
    cut points.  The pool is built once from pinn_geom.json so the PINN samples the
    SAME geometry the FEM solves (trapezoid magnets + real coils + T-tooth iron)."""
    if not _POOL:
        _build_pool(solver)
    P = _POOL
    N = min(9000, P["X"].size)
    idx = np.random.choice(P["X"].size, N, replace=False)
    interior = {"x": P["X"][idx], "y": P["Y"][idx], "inv_mur": P["IM"][idx],
                "c_sig": P["CS"][idx], "s_r": P["SR"][idx]}

    r_so = P["r_so"]
    phb = np.random.uniform(0.0, SECTOR, 400)
    bc = {"x": r_so * np.cos(phb), "y": r_so * np.sin(phb)}
    rc = np.sqrt(np.random.uniform((1e-4)**2, r_so**2, 400))
    cut = {"x1": rc, "y1": np.zeros_like(rc), "x2": np.zeros_like(rc), "y2": rc}

    if verbose:
        print(f"REAL-geom pool: interior={P['X'].size} (batch {N})  "
              f"coils={P['n_coil']}  magnets={P['n_mag']}  bc={bc['x'].size}", flush=True)
    return interior, bc, cut


# ─────────────────────────────────────────────────────────────────────────────
#  Net + autograd PDE residual
# ─────────────────────────────────────────────────────────────────────────────
def make_net(gp):
    R = gp.r_stator_out
    A0 = 0.05
    freqs = list(range(0, 17))
    return FourierNetArch(
        input_keys=[Key("x", scale=(0.0, R)), Key("y", scale=(0.0, R))],
        output_keys=[Key("Ar", scale=(0.0, A0)), Key("Ai", scale=(0.0, A0))],
        frequencies=("axis", freqs), frequencies_params=("axis", freqs),
        layer_size=96, nr_layers=5, activation_fn=Activation.SILU,
    )


def lap(field, x, y):
    g = torch.autograd.grad(field, (x, y), grad_outputs=torch.ones_like(field),
                            create_graph=True)
    fx, fy = g
    fxx = torch.autograd.grad(fx, x, grad_outputs=torch.ones_like(fx),
                              create_graph=True)[0]
    fyy = torch.autograd.grad(fy, y, grad_outputs=torch.ones_like(fy),
                              create_graph=True)[0]
    return fxx + fyy


def main():
    total = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
    cfg = SimConfig.from_motor_config()
    gp  = params_from_config()
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    solver = MagnetostaticsSolver2D(cfg, gp)   # only for geometry/material reuse
    interior, bc, cut = build_sector_points(solver)

    net = make_net(gp).to(dev)
    opt = torch.optim.Adam(net.parameters(), lr=1e-3)
    # total-aware decay → lr falls to ~10% of initial over the WHOLE run (a fixed
    # gamma would zero the lr long before a long run finishes, stalling it).
    sched = torch.optim.lr_scheduler.ExponentialLR(opt, gamma=0.1 ** (1.0 / total))

    def T(a): return torch.tensor(np.asarray(a, dtype="float32").reshape(-1, 1), device=dev)

    def make_tensors(interior, bc, cut):
        return (T(interior["x"]), T(interior["y"]), T(interior["inv_mur"]),
                T(interior["c_sig"]), T(interior["s_r"]),
                T(bc["x"]), T(bc["y"]),
                T(cut["x1"]), T(cut["y1"]), T(cut["x2"]), T(cut["y2"]))

    (xi, yi, inv_mur, c_sig, s_r, xb, yb, x1, y1, x2, y2) = make_tensors(interior, bc, cut)
    # RESAMPLE collocation points periodically.  Sampling ONCE lets a Fourier net
    # satisfy ΔA=−s at those exact points with local wiggles that DON'T form the
    # correct global field (low train loss, wrong evaluated |B|/torque).  Fresh
    # points each refresh expose & penalise the overfit → forces the true field.
    RESAMPLE_EVERY = 5
    A0 = 0.05
    W_BC, W_CUT = 1.0, 1.0     # losses already normalised by A0² below
    # Normalise the PDE residual by the characteristic source magnitude so
    # loss_pde is O(1).  Without this the residual is ~hundreds (sources μ₀J~40),
    # the raw gradients are huge, and grad-clip(1.0) throws away their magnitude
    # → Adam stalls/oscillates at a low-amplitude field.  Scaling makes the clip
    # meaningful and lets the field actually grow to its physical value.
    S_PDE = max(float(np.max(np.abs(interior["s_r"]))), 1.0)
    print(f"PDE residual scale S_PDE={S_PDE:.1f}", flush=True)

    hist = []; t0 = time.time()
    write_progress({"running": True, "step": 0, "max_steps": total, "torque_pinn": 0.0,
                    "torque_fem": TORQUE_FEM, "b_max": 0.0, "gpu_temp": gpu_temp(),
                    "sector": True, "history": hist})

    for step in range(1, total + 1):
        # resample collocation points (anti-overfitting)
        if step % RESAMPLE_EVERY == 0:
            interior, bc, cut = build_sector_points(solver, verbose=False)
            (xi, yi, inv_mur, c_sig, s_r, xb, yb, x1, y1, x2, y2) = make_tensors(interior, bc, cut)

        # thermal guard
        if step % CHECK_EVERY == 0:
            t = gpu_temp()
            if t >= TEMP_PAUSE_C:
                while gpu_temp() >= TEMP_RESUME_C:
                    write_progress({"running": True, "step": step, "max_steps": total,
                                    "torque_pinn": hist[-1]["torque"] if hist else 0.0,
                                    "torque_fem": TORQUE_FEM, "gpu_temp": gpu_temp(),
                                    "cooling": True, "sector": True, "history": hist})
                    time.sleep(4)

        opt.zero_grad()
        # interior PDE residual (non-dim: (1/μr)ΔA + μ0ωσ·A_imag + μ0·J = 0)
        xi.requires_grad_(True); yi.requires_grad_(True)
        o = net({"x": xi, "y": yi}); Ar, Ai = o["Ar"], o["Ai"]
        res_r = (inv_mur * lap(Ar, xi, yi) + c_sig * Ai + s_r) / S_PDE
        res_i = (inv_mur * lap(Ai, xi, yi) - c_sig * Ar) / S_PDE
        loss_pde = (res_r**2).mean() + (res_i**2).mean()

        # outer Dirichlet  A=0
        ob = net({"x": xb, "y": yb})
        loss_bc = ((ob["Ar"] / A0)**2 + (ob["Ai"] / A0)**2).mean()

        # anti-periodic  A(p1) + A(p2) = 0
        o1 = net({"x": x1, "y": y1}); o2 = net({"x": x2, "y": y2})
        loss_cut = (((o1["Ar"] + o2["Ar"]) / A0)**2 + ((o1["Ai"] + o2["Ai"]) / A0)**2).mean()

        loss = loss_pde + W_BC * loss_bc + W_CUT * loss_cut
        loss.backward()
        torch.nn.utils.clip_grad_norm_(net.parameters(), 10.0)  # loose safety net
        opt.step(); sched.step()

        if step % 100 == 0 or step == total:
            with torch.no_grad():
                bmax = field_bmax(net, gp, dev)
            tq = sector_torque(net, gp, cfg, dev)
            hist.append({"step": step, "torque": round(float(tq), 3),
                         "b_max": round(float(bmax), 3)})
            print(f"step {step:5d}  loss={loss.item():.3e} "
                  f"(pde={loss_pde.item():.2e} bc={loss_bc.item():.2e} cut={loss_cut.item():.2e})  "
                  f"|B|max={bmax:.3f}T  T={tq:.2f}Nm  {gpu_temp()}C", flush=True)
            write_progress({"running": True, "step": step, "max_steps": total,
                            "torque_pinn": round(float(tq), 3), "torque_fem": TORQUE_FEM,
                            "b_max": round(float(bmax), 3), "gpu_temp": gpu_temp(),
                            "sector": True, "history": hist})

    # final dump
    dump_field(net, gp, dev)
    tq = sector_torque(net, gp, cfg, dev)
    write_progress({"running": False, "done": True, "step": total, "max_steps": total,
                    "torque_pinn": round(float(tq), 3), "torque_fem": TORQUE_FEM,
                    "b_max": round(float(field_bmax(net, gp, dev)), 3),
                    "gpu_temp": gpu_temp(), "sec": round(time.time() - t0),
                    "sector": True, "history": hist})
    print(f"DONE  torque={tq:.3f} Nm (FEM {TORQUE_FEM})  sec={round(time.time()-t0)}", flush=True)


# ── symmetry-replicated full-disk evaluation ────────────────────────────────
def eval_full(net, x, y, dev):
    """Evaluate the anti-periodic field on the FULL disk by folding every point
    into the 90° sector:  A(r,φ) = (−1)^k · A_net(r, φ−k·90°)."""
    x = np.asarray(x).reshape(-1); y = np.asarray(y).reshape(-1)
    r = np.sqrt(x**2 + y**2); phi = np.mod(np.arctan2(y, x), 2 * math.pi)
    k = np.floor(phi / SECTOR).astype(int)
    ph0 = phi - k * SECTOR
    sign = np.where(k % 2 == 0, 1.0, -1.0)
    x0 = r * np.cos(ph0); y0 = r * np.sin(ph0)
    xb = torch.tensor(x0.astype("float32").reshape(-1, 1), device=dev)
    yb = torch.tensor(y0.astype("float32").reshape(-1, 1), device=dev)
    with torch.no_grad():
        o = net({"x": xb, "y": yb})
    Ar = o["Ar"].cpu().numpy().reshape(-1) * sign
    Ai = o["Ai"].cpu().numpy().reshape(-1) * sign
    return Ar, Ai


def field_bmax(net, gp, dev, N=160):
    R = gp.r_stator_out
    xs = np.linspace(-R, R, N); X, Y = np.meshgrid(xs, xs)
    Ar, _ = eval_full(net, X.ravel(), Y.ravel(), dev)
    Ar = Ar.reshape(N, N)
    dx = xs[1] - xs[0]
    Bx = np.gradient(Ar, dx, axis=0); By = -np.gradient(Ar, dx, axis=1)
    rr = np.sqrt(X**2 + Y**2)
    B = np.sqrt(Bx**2 + By**2)
    B[(rr > R) | (rr < gp.r_shaft_in)] = 0.0
    return float(B.max())


def sector_torque(net, gp, cfg, dev, n=720):
    """Maxwell-stress torque on a mid-gap circle (full 360° via symmetry)."""
    r = 0.5 * (gp.r_rotor_out + gp.r_stator_in)
    th = np.linspace(0, 2 * math.pi, n, endpoint=False)
    eps = 1e-4
    # B_r, B_phi from finite-difference of A_z on the circle
    ArA, _ = eval_full(net, (r + eps) * np.cos(th), (r + eps) * np.sin(th), dev)
    ArB, _ = eval_full(net, (r - eps) * np.cos(th), (r - eps) * np.sin(th), dev)
    dA_dr = (ArA - ArB) / (2 * eps)                 # B_phi = -dA/dr (sign conv.)
    th2 = th + 1e-3
    Ar2, _ = eval_full(net, r * np.cos(th2), r * np.sin(th2), dev)
    Ar0, _ = eval_full(net, r * np.cos(th),  r * np.sin(th),  dev)
    dA_dth = (Ar2 - Ar0) / 1e-3
    B_r   = (1.0 / r) * dA_dth
    B_phi = -dA_dr
    L = gp.stack_length
    T = (L / MU_0) * r * r * _trapz(B_r * B_phi, th)
    return T


def dump_field(net, gp, dev, N=240):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    R = gp.r_stator_out
    xs = np.linspace(-R, R, N); X, Y = np.meshgrid(xs, xs)
    Ar, Ai = eval_full(net, X.ravel(), Y.ravel(), dev)
    Ar = Ar.reshape(N, N); Ai = Ai.reshape(N, N)
    rr = np.sqrt(X**2 + Y**2); mask = (rr > R) | (rr < gp.r_shaft_in)
    dx = xs[1] - xs[0]
    Bx = np.gradient(Ar, dx, axis=0); By = -np.gradient(Ar, dx, axis=1)
    B = np.sqrt(Bx**2 + By**2)
    mm = lambda a: np.ma.masked_where(mask, a)
    fig, ax = plt.subplots(1, 3, figsize=(16, 5.2))
    for a, d, t in ((ax[0], Ar, "A_z REAL [Wb/m]"), (ax[1], Ai, "A_z IMAG"),
                    (ax[2], B, "|B| [T]")):
        im = a.contourf(X * 1000, Y * 1000, mm(d), 40, cmap="jet")
        a.set_title(t); a.set_aspect("equal"); plt.colorbar(im, ax=a, fraction=0.046)
    for a in ax:
        for rr_ in (gp.r_rotor_out * 1000, gp.r_stator_in * 1000):
            th = np.linspace(0, 2 * np.pi, 200)
            a.plot(rr_ * np.cos(th), rr_ * np.sin(th), "k-", lw=0.4, alpha=0.5)
    plt.suptitle("Sector PINN (90°, anti-periodic) — full disk via symmetry")
    plt.tight_layout(); plt.savefig(os.path.join(ROOT, "pinn_field.png"), dpi=85)
    print(f"saved pinn_field.png  |B|max={mm(B).max():.3f}T mean={mm(B).mean():.3f}T", flush=True)


if __name__ == "__main__":
    main()
