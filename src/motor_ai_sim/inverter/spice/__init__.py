"""SPICE harness for the Controller module — the vendor's own device models.

Owner, 2026-09-23: model the power switches THE SAME WAY the PCB/schematic
tools do (KiCad → ngspice; Altium/LTspice/PSpice → the manufacturer's SPICE
``.lib`` subcircuits), so that the board design and this simulation agree.

What lives here (pure Python, no new dependency):

* :mod:`.models`   — where a part's vendor library is, which subcircuit, which
  pins, whether ngspice can run it (the ``manifest.yaml`` next to it);
* :mod:`.netlist`  — the double-pulse half-bridge and the static tests as
  standard SPICE text (every netlist is also written as a standalone ``.cir``
  that ``.include``s the vendor lib, so the SAME circuit opens in KiCad or
  LTspice);
* :mod:`.runner`   — the subprocess runner (low priority, one process at a
  time, killed on timeout) and the ``wrdata`` parser;
* :mod:`.extract`  — E_on / E_off / E_fr / Q_fr, switching times, dv/dt, di/dt
  on the DATASHEET's own integration windows (Infineon Fig. A/B/C);
* :mod:`.table`    — the grid builder and the ``switching_table`` card block,
  with the interpolation the loss model reads when ``switching_source`` is
  ``"spice"``.

Nothing here replaces a datasheet number: the table is a SECOND source,
tagged ``basis: spice:<lib>@<sha256>``, and ``switching_source`` defaults to
``"datasheet"``.
"""
from __future__ import annotations

from .models import SpiceModel, SpiceModelError, model_for, spice_dir  # noqa: F401
