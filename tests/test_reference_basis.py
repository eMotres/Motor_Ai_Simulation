"""The audited reference basis rides the references answer, not the web bundle (review F4)."""
from motor_ai_sim import reference_basis as rb

CAND = "7d333cee34b8783a4b581d19f1ca7733659e0fb79e758ca0f2d53be10cf056df"


def test_pinned_candidate_gets_basis_and_required():
    e = {"id": "cat_x", "passport": {"passport": {}, "reference_provenance": {"candidate_sha256": CAND.upper()}}}
    rb.attach([e])
    assert e["reference_basis"]["required"] is True
    b = e["reference_basis"]["basis"]
    assert b["windingC"] == 180 and b["coldConstantsAvailable"] is False and b["stackLengthMm"] == 13


def test_required_card_without_provenance_fails_closed():
    e = {"id": "candidate_d85_ciano28_85_20sw1200_l13", "passport": {"passport": {}}}
    rb.attach([e])
    assert e["reference_basis"] == {"required": True, "basis": None}


def test_other_cards_are_untouched():
    e = {"id": "cat_ciano14_40_new", "passport": {"passport": {}, "reference_provenance": {"candidate_sha256": "0" * 64}}}
    before = dict(e)
    rb.attach([e])
    assert e == before and "reference_basis" not in e


def test_basis_is_a_copy():
    e = {"id": "a", "reference_provenance": {"candidate_sha256": CAND}}
    rb.attach([e])
    e["reference_basis"]["basis"]["windingC"] = 1
    f = {"id": "b", "reference_provenance": {"candidate_sha256": CAND}}
    rb.attach([f])
    assert f["reference_basis"]["basis"]["windingC"] == 180
