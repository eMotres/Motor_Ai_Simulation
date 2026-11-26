# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Shared math primitives, validity contracts, and serialization helpers for ``gears``.

Every check/rating result dataclass carries:
- ``method: str`` — standard/method identifier (e.g. ``"ISO6336-2:2006 Method B"``)
- ``state: str`` — one of ``"valid" | "estimated" | "unsupported" | "failed"``
- ``flags: List[str]`` — machine-readable diagnostics / simplifications
- ``applicability_evidence: Dict[str, Any]`` — domain checks and basis values
- ``missing_inputs: List[str]`` — inputs/references needed to upgrade from
  ``"unsupported"`` or ``"estimated"`` to ``"valid"``
- ``to_dict()`` — recursive conversion to plain JSON-safe primitives

An ``"unsupported"`` or ``"failed"`` rating MUST NEVER report ``passed=True``.
"""
from __future__ import annotations

import math
from dataclasses import fields, is_dataclass
from typing import Any, Dict, List, Literal

ValidityState = Literal["valid", "estimated", "unsupported", "failed"]


class FieldValidationError(ValueError):
    """Route-friendly validation error with an RFC 6901 JSON pointer."""

    def __init__(self, path, message, code='invalid_value'):
        self.path, self.message, self.code = path, message, code
        super().__init__(f'{path or "/"}: {message}')

    def to_dict(self):
        return dict(path=self.path, code=self.code, message=self.message)


def inv(alpha_rad: float) -> float:
    """Involute function inv(alpha) = tan(alpha) - alpha [rad]."""
    return math.tan(alpha_rad) - alpha_rad


def inv_solve(target_inv: float, *, tol: float = 1e-13, max_iter: int = 60) -> float:
    """Invert inv(alpha) = tan(alpha) - alpha for alpha in [0, pi/2).

    Uses cubic initial guess alpha ~ (3 * y)^(1/3) and safeguarded Newton-Raphson.
    """
    y = float(target_inv)
    if not math.isfinite(y):
        raise ValueError(f"inv_solve expects finite input, got {target_inv!r}")
    if y < 0.0:
        raise ValueError(f"inv(alpha) must be >= 0 for real pressure angle, got {y}")
    if y == 0.0:
        return 0.0
    alpha = min((3.0 * y) ** (1.0 / 3.0), 1.45)
    lo, hi = 0.0, 0.5 * math.pi - 1e-9
    for _ in range(max_iter):
        f = math.tan(alpha) - alpha - y
        if abs(f) < tol:
            return alpha
        if f < 0.0:
            lo = max(lo, alpha)
        else:
            hi = min(hi, alpha)
        df = math.tan(alpha) ** 2
        if df > 1e-18:
            cand = alpha - f / df
            if lo < cand < hi:
                alpha = cand
                continue
        alpha = 0.5 * (lo + hi)
    return alpha


def serialize_value(val: Any) -> Any:
    """Convert dataclasses, tuples, and numpy scalars/arrays into strict JSON-safe types."""
    if is_dataclass(val) and not isinstance(val, type):
        if hasattr(val, "to_dict") and callable(val.to_dict):
            return val.to_dict()
        return {f.name: serialize_value(getattr(val, f.name)) for f in fields(val)}
    if isinstance(val, dict):
        return {str(k): serialize_value(v) for k, v in val.items()}
    if isinstance(val, (list, tuple)):
        return [serialize_value(v) for v in val]
    if hasattr(val, "tolist") and callable(val.tolist):
        return serialize_value(val.tolist())
    if hasattr(val, "item") and callable(val.item) and type(val).__module__.startswith("numpy"):
        val = val.item()
    if isinstance(val, float) and not math.isfinite(val):
        return None
    return val


def worst_state(states: List[str]) -> ValidityState:
    """Return the most conservative validity state across a list of states."""
    order = {"failed": 3, "unsupported": 2, "estimated": 1, "valid": 0}
    best: ValidityState = "valid"
    best_rank = 0
    for st in states:
        r = order.get(st, 2)
        if r > best_rank:
            best_rank = r
            best = st  # type: ignore[assignment]
    return best


class DictResultMixin:
    """Mixin for plain result dataclasses providing ``to_dict()``."""

    method: str
    flags: List[str]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize all fields to a plain JSON-safe dictionary."""
        from .schema import encode_record
        return encode_record(self)

    @classmethod
    def from_dict(cls, data):
        """Decode a versioned strict recursive JSON record of this exact type."""
        from .schema import decode_record
        return decode_record(data, cls)


def require_number(name, value, *, minimum=None, positive=False, integer=False):
    """Strict numeric request boundary: no bools, strings, NaN or infinities."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise FieldValidationError('/'+name, f"{name} must be a finite number")
    if integer and not isinstance(value, int):
        raise FieldValidationError('/'+name, f"{name} must be an integer")
    if positive and value <= 0 or minimum is not None and value < minimum:
        raise FieldValidationError('/'+name, f"{name} outside supported range")
    return value


def validate_fields(obj, *, positive=(), nonnegative=(), signed=(), counts=()):
    for names, options in ((positive, dict(positive=True)), (nonnegative, dict(minimum=0)),
                           (signed, {}), (counts, dict(positive=True, integer=True))):
        for name in names:
            value = getattr(obj, name)
            if value is not None:
                require_number(name, value, **options)
