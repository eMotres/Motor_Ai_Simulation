"""Launcher for the CAD CLI where the FEM stack is not installed (the FreeCAD image).  Same as
``python -m motor_ai_sim.cad.build`` but does not run motor_ai_sim/__init__.py (which imports numpy/scipy/scikit-fem):
the package is registered as a bare namespace pointing at src/motor_ai_sim.

  python cad/run_build.py build --die "CIANO14 40 new" --config L12
"""
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pkg = types.ModuleType("motor_ai_sim")
pkg.__path__ = [os.path.join(ROOT, "src", "motor_ai_sim")]
sys.modules["motor_ai_sim"] = pkg
from motor_ai_sim.cad.build import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
