"""A grid geometry the validator refuses is stored with the FULL reason (every overlap pair, no
"... and N more") and with its structured violations, so the Sweep banner can group by class."""
from __future__ import annotations

from motor_ai_sim import geometry_validation as GV
from motor_ai_sim.routes import optimization as O


def _bad(n):
    vs = [GV.Violation(code="magnet_overlaps_magnet", severity="error", part_a="Magnet %d" % i,
                       part_b="Magnet %d" % (i + 1), message="Magnet %d and Magnet %d overlap by 0.1 mm2" % (i, i + 1),
                       overlap_area_mm2=0.1, likely_params=["magnet_fill_up"]) for i in range(n)]
    return GV.ValidationResult(violations=vs)


def test_the_text_keeps_every_overlap_pair_and_the_violations_are_structured(monkeypatch):
    monkeypatch.setattr(GV, "validate_geometry", lambda geo: _bad(9))
    monkeypatch.setattr("motor_ai_sim.geometry_constraints.violation_message", lambda geo: None)
    why, viol = O._geometry_reject_detail({"magnet_fill_up": 1.5})
    assert "and more" not in why and "… and" not in why and why.count("overlap by") == 9
    assert len(viol) == 9 and viol[0]["code"] == "magnet_overlaps_magnet" and viol[0]["likely_params"] == ["magnet_fill_up"]
    assert O._geometry_reject({"magnet_fill_up": 1.5}) == why        # the plain-text API is unchanged


def test_a_buildable_geometry_is_not_rejected(monkeypatch):
    monkeypatch.setattr(GV, "validate_geometry", lambda geo: GV.ValidationResult())
    monkeypatch.setattr("motor_ai_sim.geometry_constraints.violation_message", lambda geo: None)
    assert O._geometry_reject_detail({}) is None and O._geometry_reject({}) is None
