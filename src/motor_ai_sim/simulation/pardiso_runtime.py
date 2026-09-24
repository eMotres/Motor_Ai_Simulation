"""Reuse one successful MKL discovery when launching fresh FEM interpreters.

PyPardiso's first import can recursively scan the Python installation. Let its
own loader choose the library once, then pass that exact absolute filename to
children. This module neither chooses another MKL build nor changes a solver.
"""
from __future__ import annotations

from importlib import import_module
import logging
import os
from threading import Lock
from typing import Mapping

_log = logging.getLogger(__name__)
_discovery_lock = Lock()
_discovery_attempted = False
_runtime_path: str | None = None


def pardiso_subprocess_env(env: Mapping[str, str]) -> dict[str, str]:
    """Return a child environment with a verified, process-local MKL hint.

    Explicit overrides (including an empty string) and disabled PARDISO are
    untouched. Failed discovery, unfamiliar package internals, relative loader
    names and removed files all retain the original child's discovery path.
    No process environment, installation or persistent cache file is modified.
    """
    result = dict(env)
    if "PYPARDISO_MKL_RT" in result or result.get("SB_NO_PARDISO") == "1":
        return result
    global _discovery_attempted, _runtime_path
    with _discovery_lock:
        if not _discovery_attempted:
            _discovery_attempted = True
            try:
                package = import_module("pypardiso")
                name = package.scipy_aliases.pypardiso_solver.libmkl._name
                path = os.fsdecode(os.fspath(name))
                if os.path.isabs(path) and os.path.isfile(path):
                    _runtime_path = path
            except Exception:
                # An optimization of startup must not make a previously
                # launchable job fail; the child keeps its usual fallback.
                _log.debug("MKL runtime hint unavailable; retaining child discovery", exc_info=True)
        if _runtime_path is not None and os.path.isfile(_runtime_path):
            result["PYPARDISO_MKL_RT"] = _runtime_path
    return result
