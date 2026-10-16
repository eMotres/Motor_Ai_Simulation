"""Optional private data directory.

Some data cannot be published with the open repository: customer-supplied
device cards, reference cross-check numbers from independent tools, and case
notes covered by agreements.  They live in a separate private repository with
the SAME relative paths (``config/devices/<PART>.yaml`` and so on).

Point ``MOTOR_AI_SIM_PRIVATE_DATA`` at a checkout of it to use them.  When the
variable is unset or the folder is missing, everything here returns ``None``
or an empty list and the application runs on the public data alone.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

ENV_VAR = "MOTOR_AI_SIM_PRIVATE_DATA"


def private_root() -> Optional[Path]:
    """The private data folder, or ``None`` when it is not configured."""
    raw = os.environ.get(ENV_VAR, "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.is_dir() else None


def private_path(*parts: str) -> Optional[Path]:
    """``<private root>/<parts...>`` when it exists, else ``None``."""
    root = private_root()
    if root is None:
        return None
    p = root.joinpath(*parts)
    return p if p.exists() else None
