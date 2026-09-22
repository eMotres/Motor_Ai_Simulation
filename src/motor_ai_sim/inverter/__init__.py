"""The CONTROLLER — power devices, bridges, their losses and their cooling.

Owner, 2026-09-22: *«давай начнём делать модуль инвертора и его моделирование …
чтобы была возможность его подключить к мотору и выдавать уже реальный сигнал,
ну и конечно считать потери в инверторе с учётом системы охлаждения MOSFET»*,
and, the same morning: *«чтобы была возможность комбинировать мосты так, как нам
надо: один контроллер на один мотор, два контроллера на один мотор и т.д., один
мост на каждую катушку отдельно»*.

So this package is NOT a three-phase inverter with a loss formula bolted on.
It is, in order:

``devices``    the device LIBRARY — one YAML card per part in
               ``config/devices/``, transcribed from the manufacturer's
               datasheet, every number tagged with where it came from and a
               missing number left ``None`` rather than guessed.
``topology``   the COIL -> BRIDGE map.  The motor's coils come from the winding
               builder; a controller is a statement about which coil is driven
               by which bridge leg, and the presets ("one 3-phase inverter",
               "two 3-phase inverters", "an H-bridge per coil") are just named
               maps.  Everything downstream reads the map, not a hard-wired
               phase count.
``losses``     conduction, third-quadrant (dead-time), switching and E_oss per
               DEVICE, summed per switch, per bridge and per controller, with
               the junction temperature iterated against the coldplate the
               devices are bolted to.
``waveforms``  what the motor actually sees: the PWM phase/coil voltage WITH
               dead-time distortion and device drops, exported as a time series
               the coupled loop can be fed with (Stage 2).
``schematic``  the power schematic — DC link, bridges, coil terminals — drawn
               from the same map, so the picture cannot disagree with the
               numbers.

STAGES (docs/CONTROLLER_MODULE_2026-09-22.md)
  1. this — the module, the cards, the route, the tab, the report rows.
  2. coupling: ``drive: "inverter"`` feeds the non-ideal waveform into the EM
     transient and takes the solved current back, iterating to a fixed point.
  3. the six-coil study on the H-bridge-per-coil topology.
"""
from __future__ import annotations

from motor_ai_sim.inverter.devices import (          # noqa: F401
    DeviceCard, CardError, get_device, list_devices, library, devices_dir,
    write_card, validate_card,
)
from motor_ai_sim.inverter.topology import (         # noqa: F401
    TOPOLOGY_PRESETS, Bridge, Topology, TopologyError, build_topology,
    coils_from_winding,
)
from motor_ai_sim.inverter.losses import (           # noqa: F401
    ControllerRefusal, solve_controller,
)

__all__ = [
    "DeviceCard", "CardError", "get_device", "list_devices", "library",
    "devices_dir", "write_card", "validate_card",
    "TOPOLOGY_PRESETS", "Bridge", "Topology", "TopologyError",
    "build_topology", "coils_from_winding",
    "ControllerRefusal", "solve_controller",
]
