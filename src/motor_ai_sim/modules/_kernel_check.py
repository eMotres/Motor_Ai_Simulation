"""Light kernel verification (no gmsh / no FEM).

Run:  PYTHONPATH=src python -m motor_ai_sim.modules._kernel_check

Proves the kernel routes capabilities to modules and isolates faults:
geometry.2d + cost run through the kernel (real, light); a roadmap stub
(geometry.3d) and a missing capability return graceful {ok: False}, not a crash.
"""
from __future__ import annotations

import sys


def main() -> int:
    from motor_ai_sim.contracts import CostIR, GeometryIR
    from motor_ai_sim.modules.kernel import Kernel

    k = Kernel()
    ok = True

    g = k.run("geometry.2d")
    g_ok = g["ok"] and isinstance(g["result"], GeometryIR) and g["result"].n_regions > 0
    print(f"  [{'PASS' if g_ok else 'FAIL'}] kernel.run(geometry.2d)        "
          f"ok={g['ok']} regions={getattr(g.get('result'), 'n_regions', '-')}")
    ok = ok and g_ok

    c = k.run("cost", {"masses_kg": {"copper": 0.061, "magnet": 0.030, "steel": 0.050}})
    c_ok = c["ok"] and isinstance(c["result"], CostIR) and c["result"].total > 0
    print(f"  [{'PASS' if c_ok else 'FAIL'}] kernel.run(cost)               "
          f"ok={c['ok']} total=${getattr(c.get('result'), 'total', '-')}")
    ok = ok and c_ok

    f = k.run("geometry.3d")  # roadmap stub -> graceful failure
    f_ok = (not f["ok"]) and "not implemented" in (f.get("error") or "")
    print(f"  [{'PASS' if f_ok else 'FAIL'}] kernel.run(geometry.3d)        "
          f"graceful-fail={not f['ok']} err={(f.get('error') or '')[:40]!r}")
    ok = ok and f_ok

    nf = k.run("does.not.exist")  # missing capability -> graceful
    nf_ok = (not nf["ok"]) and "no provider" in (nf.get("error") or "")
    print(f"  [{'PASS' if nf_ok else 'FAIL'}] kernel.run(missing capability) graceful-fail={not nf['ok']}")
    ok = ok and nf_ok

    print("\n" + ("KERNEL OK" if ok else "KERNEL CHECK FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
