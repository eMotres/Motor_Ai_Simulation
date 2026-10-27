"""TEST / VALIDATION duties (owner 2026-10-28).

Some saved duties are not operating points of the product but bench-test or validation
points: the free-run reproductions of the built test motor (``noload_3021`` ... at 0.1 A) that
``motor_tests`` reads as "the no-load duty" when it compares a bench record with the model.
They must stay in the configuration (the comparison reads them from the same yaml), but they
are not something a customer picks: no Configure preset or duty pill, no default-duty choice,
no public catalogue card, no public datasheet / assistant / MCP duty list.

The flag is ``kind: "test"`` on the duty entry (``DutySpec.kind`` of ``POST /api/family/duty``;
no duty field carried that meaning before - ``kind`` inside a duty's ``duty_cycle`` block is a
different thing, the S1/S2/S3 cycle type).  Absent, or any other value, is an ordinary duty.

LEGACY FALLBACK: duties saved before the flag existed carry no ``kind``.  The only such family is
the bench free-run reproduction named ``noload_<id>``; an UNFLAGGED duty with that prefix is read
as a test duty too, so a configuration is protected before its data is flagged.  An explicit
``kind`` (any value) always wins over the name.

Pure functions, no imports from the rest of the package: safe to use anywhere.
"""
from __future__ import annotations

from typing import Any, Iterable, List

#: the value of a duty's ``kind`` that marks a bench-test / validation point
TEST_KIND = "test"

#: legacy bench free-run reproductions were named ``noload_<id>`` before the flag existed
_LEGACY_TEST_PREFIX = "noload_"


def duty_kind(duty: Any) -> str:
    """``"test"`` for a test / validation duty, else ``"operating"`` (an ordinary duty)."""
    return TEST_KIND if is_test_duty(duty) else "operating"


def is_test_duty(duty: Any) -> bool:
    """Is this duty entry (a mapping with ``name`` / ``kind``) a test / validation point?"""
    if not isinstance(duty, dict):
        return False
    kind = duty.get("kind")
    if kind is not None and str(kind).strip():
        return str(kind).strip().lower() == TEST_KIND
    return str(duty.get("name") or "").strip().lower().startswith(_LEGACY_TEST_PREFIX)


def operating_duties(duties: Iterable[Any]) -> List[Any]:
    """``duties`` without the test / validation points (non-mappings are kept untouched)."""
    return [d for d in (duties or []) if not is_test_duty(d)]
