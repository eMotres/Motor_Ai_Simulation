"""The audited reference basis rides the references answer, not the web bundle or the public source.

Re-audit R4 (2026-10-20): the real values live in the private data store; this module only loads them, so the
tests use SYNTHETIC ids, hashes and numbers.
"""
import json

import pytest

from motor_ai_sim import reference_basis as rb

CAND = "ab" * 32                       # synthetic pin
OTHER = "cd" * 32
CARD = "synthetic_card_x1"
BASIS = {"windingC": 111, "magnetC": 99, "steelC": None, "stackLengthMm": 7, "rpm": 123, "currentA": 4.5,
         "sourceRecord": "synthetic_record", "sourceSha256": "ef" * 32, "coldConstantsAvailable": False}


def _write(d, name, **rec):
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps({"schema": "reference-basis-1", **rec}), encoding="utf-8")


@pytest.fixture
def store(tmp_path, monkeypatch):
    d = tmp_path / "references" / "basis"
    monkeypatch.setattr(rb, "_DIR", d)
    _write(d, "synthetic.json", card_id=CARD, candidate_sha256=CAND, basis=BASIS)
    return d


def test_pinned_candidate_gets_basis_and_required(store):
    e = {"id": "cat_x", "passport": {"passport": {}, "reference_provenance": {"candidate_sha256": CAND.upper()}}}
    rb.attach([e])
    assert e["reference_basis"]["required"] is True
    b = e["reference_basis"]["basis"]
    assert b["windingC"] == 111 and b["coldConstantsAvailable"] is False and b["stackLengthMm"] == 7


def test_required_card_without_provenance_fails_closed(store):
    e = {"id": CARD, "passport": {"passport": {}}}
    rb.attach([e])
    assert e["reference_basis"] == {"required": True, "basis": None}


def test_other_cards_are_untouched(store):
    e = {"id": "cat_other", "passport": {"passport": {}, "reference_provenance": {"candidate_sha256": OTHER}}}
    before = dict(e)
    rb.attach([e])
    assert e == before and "reference_basis" not in e


def test_basis_is_a_copy(store):
    e = {"id": "a", "reference_provenance": {"candidate_sha256": CAND}}
    rb.attach([e])
    e["reference_basis"]["basis"]["windingC"] = 1
    f = {"id": "b", "reference_provenance": {"candidate_sha256": CAND}}
    rb.attach([f])
    assert f["reference_basis"]["basis"]["windingC"] == 111


def test_missing_store_fails_pinned_cards_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(rb, "_DIR", tmp_path / "nowhere")
    pinned = {"id": "p", "reference_provenance": {"candidate_sha256": CAND}}
    plain = {"id": "q", "passport": {"passport": {}}}
    rb.attach([pinned, plain])
    assert pinned["reference_basis"] == {"required": True, "basis": None}
    assert "reference_basis" not in plain


def test_empty_store_fails_pinned_cards_closed(tmp_path, monkeypatch):
    d = tmp_path / "empty"
    d.mkdir()
    monkeypatch.setattr(rb, "_DIR", d)
    e = {"id": "p", "reference_provenance": {"candidate_sha256": CAND}}
    rb.attach([e])
    assert e["reference_basis"] == {"required": True, "basis": None}


def test_unreadable_record_fails_pinned_cards_closed(store):
    (store / "broken.json").write_text("{not json", encoding="utf-8")
    e = {"id": "p", "reference_provenance": {"candidate_sha256": OTHER}}
    rb.attach([e])
    assert e["reference_basis"] == {"required": True, "basis": None}


def test_record_with_null_basis_is_required_without_basis(tmp_path, monkeypatch):
    d = tmp_path / "b"
    monkeypatch.setattr(rb, "_DIR", d)
    _write(d, "n.json", card_id=CARD, candidate_sha256=CAND, basis=None)
    e = {"id": "x", "reference_provenance": {"candidate_sha256": CAND}}
    rb.attach([e])
    assert e["reference_basis"] == {"required": True, "basis": None}


def test_edit_of_a_record_is_picked_up(store):
    e = {"id": "a", "reference_provenance": {"candidate_sha256": CAND}}
    rb.attach([e])
    assert e["reference_basis"]["basis"]["rpm"] == 123
    _write(store, "synthetic.json", card_id=CARD, candidate_sha256=CAND, basis={**BASIS, "rpm": 456, "pad": "x" * 9})
    f = {"id": "a", "reference_provenance": {"candidate_sha256": CAND}}
    rb.attach([f])
    assert f["reference_basis"]["basis"]["rpm"] == 456


def test_private_checkout_is_the_workstation_store(tmp_path, monkeypatch):
    """No shared folder: <MOTOR_AI_SIM_PRIVATE_DATA>/config/references/basis is read."""
    from motor_ai_sim import workspace
    d = tmp_path / "priv" / "config" / "references" / "basis"
    _write(d, "s.json", card_id=CARD, candidate_sha256=CAND, basis=BASIS)
    monkeypatch.setenv("MOTOR_AI_SIM_PRIVATE_DATA", str(tmp_path / "priv"))
    monkeypatch.setattr(workspace, "shared_root", lambda: tmp_path / "no_shared")
    e = {"id": "a", "reference_provenance": {"candidate_sha256": CAND}}
    rb.attach([e])
    assert e["reference_basis"]["basis"]["stackLengthMm"] == 7


def test_public_source_holds_no_basis_data():
    """The module is a loader: no pin, card id or record hash literal."""
    import re
    from pathlib import Path
    src = Path(rb.__file__).read_text(encoding="utf-8")
    assert not re.search(r"[0-9a-f]{40,}", src, re.I)
