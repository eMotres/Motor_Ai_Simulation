"""Owner's 2026-10-26 specification decisions, backend side: Maximum speed is the one speed limit (proof speed = maximum
speed, a larger stored overspeed wins), Prototypes defaults to 1 and counts as filled, the ripple field is the only ripple
question, the retired fields stay readable.  All values are made up."""
import pytest

from motor_ai_sim.development import models


def _draft(**fields):
    return {"schema_version": 2, "fields": {k: {"status": "filled", "value": v} for k, v in fields.items()},
            "mission_profile": [], "mission_profile_status": "missing", "production": {}, "provenance": {}}


def test_proof_speed_is_the_maximum_speed_without_a_factor():
    assert not hasattr(models, "PROOF_SPEED_FACTOR")
    assert models.proof_speed_rpm(_draft(**{"1_6": {"maximum_rpm": 9000}})) == 9000.0
    assert models.proof_speed_rpm(_draft(**{"1_6": {"maximum_rpm": {"minimum": 8000, "maximum": 9500}}})) == 9500.0
    assert models.proof_speed_rpm(_draft()) is None
    unknown = {"schema_version": 2, "fields": {"1_6": {"status": "not_known_yet", "value": {"maximum_rpm": 9000}}}}
    assert models.proof_speed_rpm(unknown) is None


def test_a_larger_stored_overspeed_wins_a_smaller_one_does_not():
    legacy = _draft(**{"1_6": {"maximum_rpm": 9000, "overspeed_rpm": 10800, "runaway_rpm": 14000, "proof_or_runaway": "1.2 x max"}})
    assert models.proof_speed_rpm(legacy) == 10800.0                       # e.g. an engine limit stored by the customer
    smaller = _draft(**{"1_6": {"maximum_rpm": 9000, "overspeed_rpm": 8000}})
    assert models.proof_speed_rpm(smaller) == 9000.0
    # the old values stay readable: they normalise and are kept
    kept = models.normalize_draft(legacy)["fields"]["1_6"]["value"]
    assert kept["overspeed_rpm"] == 10800 and kept["runaway_rpm"] == 14000 and kept["proof_or_runaway"] == "1.2 x max"


def test_only_maximum_speed_is_required_of_the_speed_field():
    field = models._field_map(2)["1_6"][1]
    assert field["label"]["en"] == "Maximum speed"
    required = [p["id"] for p in field["properties"] if p.get("required")]
    assert required == ["maximum_rpm"]
    assert models._answer_complete({"status": "filled", "value": {"maximum_rpm": 9000}}, field)
    hidden = {p["id"] for p in field["properties"] if p.get("ui_hidden")}
    assert hidden == {"overspeed_rpm", "proof_or_runaway", "runaway_rpm"}


def test_prototypes_default_one_counts_as_filled_and_never_overrides():
    out = models.with_defaults(_draft())
    assert out["fields"]["6_1"] == {"status": "filled", "value": {"prototypes": 1}}
    assert models.with_defaults(out) == out                                   # idempotent
    typed = models.with_defaults(_draft(**{"6_1": {"prototypes": 4}}))
    assert typed["fields"]["6_1"]["value"]["prototypes"] == 4
    empty = models.with_defaults(_draft(**{"6_1": {"prototypes": None, "pre_series": 20}}))
    assert empty["fields"]["6_1"]["value"] == {"prototypes": 1, "pre_series": 20}      # the hidden extras stay
    unknown = {"schema_version": 2, "fields": {"6_1": {"status": "not_known_yet", "value": None}}}
    assert models.with_defaults(unknown)["fields"]["6_1"]["status"] == "not_known_yet"
    assert "6_1" not in models.with_defaults({"schema_version": 1, "fields": {}})["fields"]
    # the stored draft is not mutated
    stored = _draft()
    models.with_defaults(stored)
    assert "6_1" not in stored["fields"]
    # completeness and validation read it as filled; a saved draft carries it
    assert models.completeness(_draft())["sections"]["6"]["answered"] == 1
    assert not [e for e in models.validation_errors(_draft()) if e["field"].startswith("6_1")]
    assert models.normalize_draft(_draft())["fields"]["6_1"]["value"]["prototypes"] == 1


def test_only_prototypes_is_asked_of_the_quantities():
    field = models._field_map(2)["6_1"][1]
    assert field["label"]["en"] == "Prototypes"
    assert [p["id"] for p in field["properties"] if p.get("required")] == ["prototypes"]
    assert {p["id"] for p in field["properties"] if p.get("ui_hidden")} == {"pre_series", "series_per_year"}


def test_ripple_is_the_only_ripple_question_and_cogging_stays_readable():
    field = models._field_map(2)["1_8"][1]
    assert field["label"]["en"] == "Max torque ripple at continuous operation"
    assert not field.get("required")
    assert {p["id"] for p in field["properties"] if p.get("ui_hidden")} == {"cogging_pct_pkpk", "cogging_torque_Nm", "avoid_frequencies_Hz"}
    old = {"torque_ripple_pct_pkpk": 4, "cogging_pct_pkpk": 1.5, "cogging_torque_Nm": 0.02, "avoid_frequencies_Hz": [120, 240]}
    kept = models.normalize_draft(_draft(**{"1_8": old}))["fields"]["1_8"]["value"]
    assert kept == old


def test_retired_fields_stay_in_the_schema_and_stay_readable():
    fields = models._field_map(2)
    assert "2_3" in fields and "2_5" in fields
    old = _draft(**{"2_3": {"continuous_A": 80, "peak_A": 150, "charge_A": 40}, "2_5": {"device_type": "SiC MOSFET", "voltage_class_V": 1200}})
    kept = models.normalize_draft(old)["fields"]
    assert kept["2_3"]["value"]["charge_A"] == 40 and kept["2_5"]["value"]["voltage_class_V"] == 1200
    assert not fields["2_3"][1].get("required") and not fields["2_5"][1].get("required")
