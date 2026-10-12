"""Parametric CAD models of the motors (FreeCAD): store -> profile -> .FCStd + STEP + datums + manifest.

Light by design: nothing here imports the FEM stack, so the modules also run in the FreeCAD image
(deploy/Dockerfile.cad).  See docs/CAD_GENERATOR.md.
"""
