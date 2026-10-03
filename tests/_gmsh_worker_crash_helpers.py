"""Tiny functions the gmsh worker crash/timeout tests dispatch to.

Kept in their own module (not inline in the test file) because the worker
subprocess resolves them by dotted path (``importlib.import_module``) —
same mechanism used for the real mesh-building ``*_impl`` functions.
"""

from __future__ import annotations

import os
import time


def raise_value_error() -> None:
    raise ValueError("deliberate failure for the crash test")


def hard_crash() -> None:
    """Exit the interpreter immediately — no traceback, no cleanup.

    Stands in for a gmsh/OCC native crash (segfault), which a Python
    ``try/except`` in the worker cannot catch either way. The parent must
    detect the dead process, not a caught exception.
    """
    os._exit(1)


def sleep_forever(seconds: float) -> None:
    time.sleep(seconds)
