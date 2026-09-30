"""Refuse Intel MKL's INTEL threading layer when intel-openmp is not shipped.

WHY
===
The deploy image (2026-09-30) no longer installs ``intel-openmp`` /
``intel-cmplr-lib-ur`` — see ``deploy/THIRD_PARTY_NOTICES.md`` and
``C:\\Users\\vadim\\Downloads\\mkl_without_intel_openmp_2026-09-30.md`` for the
licensing reason (Intel's End User License Agreement for those two packages
bars reciprocal-open-source linking, §3.1(x), and SaaS/service-bureau use,
§3.1(xi) — both a problem for a hosted FEM API). MKL's ``libmkl_rt.so`` picks
its threading backend at RUNTIME, by ``dlopen()``, based on
``MKL_THREADING_LAYER`` (default: ``INTEL``). Only the INTEL backend
(``libmkl_intel_thread.so``) ever touches ``libiomp5.so``; ``SEQUENTIAL``,
``TBB`` and ``GNU`` never do (confirmed with ``ldd`` against every one of
MKL's own ``.so`` files — none of them, including
``libmkl_intel_thread.so.3`` itself, has an ELF ``DT_NEEDED`` entry for
``libiomp5.so``; it is loaded via ``dlopen()`` from inside
``libmkl_intel_thread.so`` only when that backend actually runs).

Left unset or set to ``INTEL`` on an image that never installs
``intel-openmp``, the *first* real PARDISO call ``dlopen()``s
``libmkl_intel_thread.so``, which then needs ``libiomp5.so``'s
``omp_in_parallel`` symbol. That failure is **not a catchable Python
exception** — glibc's dynamic linker prints ``symbol lookup error`` straight
to stderr and aborts the whole interpreter (measured directly: a bare
``python3 -c "..."`` exits like this, with nothing for a ``try/except`` to
catch, because the unresolved symbol is hit by the dynamic linker's own lazy
PLT binding, not by Python bytecode). Every call site that might import
``pypardiso`` MUST ask this module first and take its SciPy fallback
(SuperLU / ``splu``) itself when unsafe — wrapping the import in
``try/except`` is necessary but not sufficient, because the crash this
module exists to prevent happens below the level Python exceptions reach.

Memoized per process: the loud refusal (or the quiet all-clear) is logged
once, the first time anything asks, and the decision does not change
mid-process — nothing may usefully rewrite ``MKL_THREADING_LAYER`` after
boot, since ``libmkl_rt.so``'s dispatcher reads it once too.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

#: Threading layers whose backend ``.so`` has no dependency on the EULA'd
#: ``libiomp5.so``. ``SEQUENTIAL`` and ``GNU`` verified with ``ldd`` (no
#: libiomp5 in either's DT_NEEDED); ``TBB`` verified the same way — it only
#: needs the ISSL-licensed ``tbb`` package's ``libtbb.so.12``.
SAFE_LAYERS = frozenset({"SEQUENTIAL", "TBB", "GNU"})

_checked = False
_safe = False
_layer: str | None = None


def pardiso_threading_status() -> dict:
    """``{"layer": <MKL_THREADING_LAYER as set, or None>, "safe": bool}``.

    Computed fresh on every call and never logs — for cheap, read-only
    reporting (health/diagnostics endpoints). Use
    :func:`pardiso_threading_is_safe` to actually gate a PARDISO call site.
    """
    raw = os.environ.get("MKL_THREADING_LAYER")
    layer = (raw or "").strip().upper()
    return {"layer": raw, "safe": layer in SAFE_LAYERS}


def pardiso_threading_is_safe() -> bool:
    """True once ``MKL_THREADING_LAYER`` never dlopen()s ``libiomp5.so``.

    Every call site about to ``import pypardiso`` (or any of its submodules)
    must check this FIRST and fall back to SciPy itself when it is False —
    never attempt the import, since the crash this guards against is not a
    Python exception a surrounding ``try/except`` can catch.

    Logs its refusal at CRITICAL (or its all-clear at INFO) exactly once per
    process; cheap and memoized after that, safe to call from every hot solve
    path.
    """
    global _checked, _safe, _layer
    if _checked:
        return _safe
    status = pardiso_threading_status()
    _layer, _safe = status["layer"], status["safe"]
    _checked = True
    if not _safe:
        # Belt and suspenders: also flip the pre-existing kill switch, so a
        # call site that only checks SB_NO_PARDISO (fem_solver_2d.py's P2
        # transient path predates this module) is covered without editing it.
        os.environ["SB_NO_PARDISO"] = "1"
        log.critical(
            "PARDISO REFUSED: MKL_THREADING_LAYER=%r is not one of %s. This "
            "image does not ship intel-openmp (deploy/THIRD_PARTY_NOTICES.md) "
            "-- the INTEL threading layer would dlopen a missing libiomp5.so "
            "and abort the interpreter outright, not raise a catchable "
            "exception. Every solver falls back to SciPy SuperLU for this "
            "process. Set MKL_THREADING_LAYER=SEQUENTIAL (or TBB/GNU) in "
            "/etc/motres/api.env to use PARDISO.",
            _layer, sorted(SAFE_LAYERS),
        )
    else:
        log.info("PARDISO threading layer OK: MKL_THREADING_LAYER=%s", _layer)
    return _safe


def libiomp5_mapped() -> bool:
    """True if a ``libiomp5*.so`` is currently mapped into this process.

    Reads ``/proc/self/maps`` (Linux only — returns False anywhere that file
    does not exist, e.g. the Windows development box, rather than raising).
    A single small file read; cheap enough to call on every health check.
    """
    try:
        with open("/proc/self/maps", "r", encoding="utf-8", errors="replace") as fh:
            return any("libiomp5" in line for line in fh)
    except OSError:
        return False


def reset_for_tests() -> None:
    """Undo the memoization so a test can exercise both branches."""
    global _checked, _safe, _layer
    _checked = False
    _safe = False
    _layer = None
