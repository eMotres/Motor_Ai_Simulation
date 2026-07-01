"""Test preamble: point the config singleton at a STABLE 40 mm config in the
scratchpad, so tests are deterministic and NEVER touch the user's live on-disk
config/motor_config.yaml (which the user is actively editing).

Import this FIRST in any test script:  import _use40  # noqa
"""
import sys
from pathlib import Path

sys.path.insert(0, "src")
import motor_ai_sim.config as _C

_CFG40 = Path(
    r"C:/Users/vadim/AppData/Local/Temp/claude/"
    r"C--Users-vadim-Projects-Motor-Optimization-AI/"
    r"4baf3446-3813-46b8-803e-79b9c27f2cf3/scratchpad/cfg40.yaml"
)
_C.DEFAULT_CONFIG_PATH = _CFG40
_C.load_config(_CFG40, reload=True)     # prime the singleton so get_config() hits it

# MotorGeometryParams.from_yaml has its OWN DEFAULT_CONFIG_PATH (the one actually
# used by CadQueryMotor) — patch it too, else geometry reads the live on-disk file.
import motor_ai_sim.geometry.motor_geometry as _MG
_MG.DEFAULT_CONFIG_PATH = _CFG40

# geometry_2d.params_from_config has its OWN _CFG_PATH (used by the transient for
# the slip radius `mid`) — patch it too so `mid` agrees with the polygon mid_r_mm.
import motor_ai_sim.simulation.geometry_2d as _G2
_G2._CFG_PATH = _CFG40
# params_from_config's default arg was bound at def time to the old _CFG_PATH;
# rebind the default so callers that omit cfg_path use the 40mm file.
try:
    _G2.params_from_config.__defaults__ = (_CFG40, None)
except Exception:
    pass
