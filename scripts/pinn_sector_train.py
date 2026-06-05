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


def build_sector_points(solver: MagnetostaticsSolver2D, verbose: bool = True):
    """Return dicts of numpy arrays (x,y, inv_mur, c_sig, s_r) for interior PDE
    collocation, plus outer-arc BC points and the two anti-periodic cut point
    sets, all restricted to the 90° sector."""
    gp  = solver.gp
    cfg = solver.cfg
    geo = solver.geo
    omega = 2 * math.pi * cfg.frequency_hz

    # phase currents at this rotor angle (same model as solver_2d.build)
    pole_pairs = gp.num_poles // 2
    theta_elec = math.radians(cfg.rotor_angle_deg * pole_pairs + cfg.phase_offset_deg)
    I_phase = {
        'A': cfg.I_peak * math.cos(theta_elec),
        'B': cfg.I_peak * math.cos(theta_elec - 2 * math.pi / 3),
        'C': cfg.I_peak * math.cos(theta_elec + 2 * math.pi / 3),
    }
    slot_area = gp.slot_width_m * gp.slot_height_m * gp.fill_factor

    xs, ys, inv_mur, c_sig, s_r = [], [], [], [], []

    def add(x, y, mur, sigma, src):
        x = np.asarray(x).reshape(-1); y = np.asarray(y).reshape(-1)
        m = in_sector(x, y)
        x, y = x[m], y[m]
        n = x.size
        xs.append(x); ys.append(y)
        inv_mur.append(np.full(n, 1.0 / mur))
        c_sig.append(np.full(n, MU_0 * omega * sigma))
        s_r.append(np.full(n, MU_0 * src))      # MU_0·J  (constant per region)

    # ── bulk bands (μ_r by radius) ──
    # Cap iron μ_r for PINN conditioning.  In a strong-form collocation PINN a
    # huge μ_r (=5000) makes the iron PDE term (1/μ_r)ΔA ≈ 0 → iron is barely
    # constrained and the sharp iron/air interface can't be resolved → the field
    # stalls at low amplitude.  For an UNSATURATED SPM machine the air-gap flux
    # (hence torque) is set by the magnets + air-gap reluctance and is nearly
    # insensitive to iron μ_r once μ_r≫1, so μ_r≈500 keeps the physics while
    # making the problem far better-conditioned.
    mur_fe = min(float(cfg.mu_r_stator), 500.0)
    add(*sample_wedge_band(1500, 1e-4,           gp.r_shaft_in), 1.0,    0.0, 0.0)  # shaft bore
    add(*sample_wedge_band(700,  gp.r_shaft_in,  gp.r_rotor_in), mur_fe, 0.0, 0.0)  # rotor iron
    add(*sample_wedge_band(900,  gp.r_rotor_in,  gp.r_rotor_out),mur_fe, 0.0, 0.0)  # rotor body between magnets
    add(*sample_wedge_band(500,  gp.r_rotor_out, gp.r_stator_in),1.0,    0.0, 0.0)  # air gap
    add(*sample_wedge_band(1500, gp.r_stator_in, gp.r_stator_out),mur_fe,0.0, 0.0)  # stator iron

    # ── slots (6 in sector) → real current density J_z ──
    n_slot = 0
    for name, dom, (phase, direction) in geo.slot_domains():
        phic = float(name.split("_")[1]) * (2 * math.pi / gp.num_slots)
        if not (0.0 <= phic < SECTOR):
            continue
        n_slot += 1
        pts = dom.sample_interior(500)
        J_z = direction * I_phase[phase] * gp.num_wires_per_slot / slot_area
        add(pts["x"], pts["y"], 1.0, gp.sigma_cu, J_z)

    # ── magnets (7 in sector) → curl(M)=M_φ/r  (src current density, varies 1/r) ──
    n_mag = 0
    for name, dom, polarity in geo.magnet_domains():
        i = int(name.split("_")[1])
        cen = (i + 0.5) * (2 * math.pi / gp.num_poles)
        if not (0.0 <= cen < SECTOR):
            continue
        n_mag += 1
        pts = dom.sample_interior(500)
        x = np.asarray(pts["x"]).reshape(-1); y = np.asarray(pts["y"]).reshape(-1)
        m = in_sector(x, y); x, y = x[m], y[m]
        r = np.sqrt(x**2 + y**2)
        Jmag = polarity * (cfg.Br_magnet / MU_0) / r          # curl(M)_z [A/m²]
        n = x.size
        xs.append(x); ys.append(y)
        inv_mur.append(np.full(n, 1.0 / 1.05))
        c_sig.append(np.full(n, MU_0 * omega * gp.sigma_mag))
        s_r.append(MU_0 * Jmag)                                # spatially varying

    interior = {
        "x": np.concatenate(xs), "y": np.concatenate(ys),
        "inv_mur": np.concatenate(inv_mur), "c_sig": np.concatenate(c_sig),
        "s_r": np.concatenate(s_r),
    }

    # ── outer-arc Dirichlet BC  A_z = 0  (r = r_stator_out, φ∈[0,90°]) ──
    phb = np.random.uniform(0.0, SECTOR, 400)
    bc = {"x": gp.r_stator_out * np.cos(phb), "y": gp.r_stator_out * np.sin(phb)}

    # ── anti-periodic cut pairs:  p1 at φ=0 (+x axis),  p2 at φ=90° (+y axis) ──
    rc = np.sqrt(np.random.uniform((1e-4)**2, gp.r_stator_out**2, 400))
    cut = {"x1": rc, "y1": np.zeros_like(rc), "x2": np.zeros_like(rc), "y2": rc}

    if verbose:
        print(f"sector points: interior={interior['x'].size}  slots={n_slot}  "
              f"magnets={n_mag}  bc={bc['x'].size}  cut_pairs={rc.size}", flush=True)
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
