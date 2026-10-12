"""Active part of a segmented flat-wire PM motor (template `segmented_flatwire`, first instance CIANO14 40/12).

Bodies (PartDesign; every sketch dimension, feature length and pattern count is an expression of `Params`):
  Core   - core.py   : sector sketch -> Pad (symmetric motor_length) -> PolarPattern (num_seg*3)
  Rotor  - rotor.py  : pole-pitch sketch with magnet pocket -> Pad (symmetric rotor_w) -> PolarPattern
                       (num_seg*num_poles_per_segment)
  Magnet - magnet.py : magnet sketch -> Pad (mag_step); instances by a Draft link polar array
  Coil   - coil.py   : flat-wire coil around one tooth (legs, end turns, layer transitions, leads);
                       instances by a Draft link polar array (num_seg*3)
Frame = Fusion root frame: Z = motor axis, first tooth / coil / magnet centred on +Y.
"""
from .core import build_core
from .rotor import build_rotor
from .magnet import build_magnet

__all__ = ["build_core", "build_rotor", "build_magnet"]
