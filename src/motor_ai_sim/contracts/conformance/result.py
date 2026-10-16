"""Conformance: any ResultIR a solver returns must satisfy these.

Keeps every solver's output honest: a failure must carry a reason; time-series
must be equal-length; a field-free result must JSON round-trip.
"""
from __future__ import annotations

from .. import ResultIR


def assert_result_ir(rir: ResultIR, *, physics: str | None = None) -> None:
    assert isinstance(rir, ResultIR), "must return a ResultIR"
    assert isinstance(rir.physics, str) and rir.physics, "physics tag required"
    if physics is not None:
        assert rir.physics == physics, f"expected physics={physics!r}, got {rir.physics!r}"
    assert isinstance(rir.ok, bool)
    if not rir.ok:
        assert rir.error, "a failed ResultIR must carry an error reason (fault isolation)"
    if rir.series and rir.series.time_s:
        n = len(rir.series.time_s)
        if rir.series.torque_Nm:
            assert len(rir.series.torque_Nm) == n, "torque series length != time series"
        for name, vals in (rir.series.extra or {}).items():
            assert len(vals) == n, f"series {name!r} length != time series"
    # field-free results are pure data -> must JSON round-trip (the wire format)
    if rir.fields is None:
        ResultIR.model_validate_json(rir.model_dump_json())
