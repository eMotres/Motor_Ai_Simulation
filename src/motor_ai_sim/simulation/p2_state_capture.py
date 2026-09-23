"""Opt-in, selected-frame capture of finalized P2 states for diagnostics."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Mapping

import numpy as np


_active_capture = ContextVar("p2_state_capture", default=None)


@contextmanager
def capture_p2_states(select: Callable[[Mapping], bool],
                      consume: Callable[[dict], None]):
    """Route selected finalized-frame snapshots to a diagnostic consumer."""
    token = _active_capture.set((select, consume))
    try:
        yield
    finally:
        _active_capture.reset(token)


def current_p2_state_capture():
    """Return the active capture target, or ``None`` on the ordinary path."""
    return _active_capture.get()


def _snapshot_copy(value):
    if isinstance(value, np.ndarray):
        copied = np.array(value, copy=True)
        copied.setflags(write=False)
        return copied
    if isinstance(value, dict):
        return {key: _snapshot_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_snapshot_copy(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_snapshot_copy(item) for item in value)
    return value


def emit_selected_p2_state(capture, metadata: Mapping,
                           snapshot_factory: Callable[[], dict]) -> bool:
    """Copy and deliver a frame only after the caller selects its metadata."""
    select, consume = capture
    frame_metadata = dict(metadata)
    if not select(frame_metadata):
        return False
    snapshot = _snapshot_copy(snapshot_factory())
    consume({"metadata": frame_metadata, "state": snapshot})
    return True
