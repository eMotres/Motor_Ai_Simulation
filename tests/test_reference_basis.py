"""The audited reference basis rides the references answer, not the web bundle or the public source.

Re-audit R4 (2026-10-20): the real values live in the private data store; this module only loads them, so the
tests use SYNTHETIC ids, hashes and numbers.
"""
import json

NL = chr(10)

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
    # only the marked SHA-256 digests of the required cards may be 64-hex strings
    unmarked = NL.join(ln for ln in src.splitlines() if "private-data-ok" not in ln)
    assert not re.search(r"[0-9a-f]{40,}", unmarked, re.I)
    assert len(rb.REQUIRED_ID_DIGESTS) >= 1 and len(rb.REQUIRED_PIN_DIGESTS) >= 1


# --- re-audit #2, R7: the requirement survives a missing record -------------------------------------------------------
import hashlib

PIN_A, PIN_B = "a1" * 32, "b2" * 32
ID_A, ID_B, ID_ONLY = "synthetic_card_a", "synthetic_card_b", "synthetic_id_only"
BASIS_A = {**BASIS, "rpm": 100}
BASIS_B = {**BASIS, "rpm": 200}


def _sha(x):
    return hashlib.sha256(x.encode()).hexdigest()


@pytest.fixture
def two(tmp_path, monkeypatch):
    """Cards A (pinned) and B (pinned) and an id-only card; the built-in required list names all three."""
    d = tmp_path / "basis"
    monkeypatch.setattr(rb, "_DIR", d)
    monkeypatch.setattr(rb, "REQUIRED_PIN_DIGESTS", frozenset({_sha(PIN_A), _sha(PIN_B)}))
    monkeypatch.setattr(rb, "REQUIRED_ID_DIGESTS", frozenset({_sha(ID_A), _sha(ID_B), _sha(ID_ONLY)}))
    _write(d, "a.json", card_id=ID_A, candidate_sha256=PIN_A, basis=BASIS_A)
    _write(d, "b.json", card_id=ID_B, candidate_sha256=PIN_B, basis=BASIS_B)
    _write(d, "c.json", card_id=ID_ONLY, basis=BASIS)
    return d


def _card(cid, pin=None):
    e = {"id": cid}
    if pin:
        e["reference_provenance"] = {"candidate_sha256": pin}
    return e


FAIL_CLOSED = {"required": True, "basis": None}


def test_all_records_present(two):
    a, b, c = _card(ID_A, PIN_A), _card(ID_B, PIN_B), _card(ID_ONLY)
    rb.attach([a, b, c])
    assert a["reference_basis"]["basis"]["rpm"] == 100 and b["reference_basis"]["basis"]["rpm"] == 200
    assert c["reference_basis"] == FAIL_CLOSED          # id-only: required, no pin to match a basis to
    assert rb.store_problems() == []


def test_a_present_b_missing(two):
    (two / "b.json").unlink()
    a, b = _card(ID_A, PIN_A), _card(ID_B, PIN_B)
    rb.attach([a, b])
    assert a["reference_basis"]["basis"]["rpm"] == 100
    assert b["reference_basis"] == FAIL_CLOSED
    assert any("required pin" in p for p in rb.store_problems())


def test_b_present_a_missing(two):
    (two / "a.json").unlink()
    a, b = _card(ID_A, PIN_A), _card(ID_B, PIN_B)
    rb.attach([a, b])
    assert a["reference_basis"] == FAIL_CLOSED          # the R7 case: A's file is gone, B keeps the store healthy
    assert b["reference_basis"]["basis"]["rpm"] == 200


def test_id_only_card_stays_required_when_its_file_disappears(two):
    (two / "c.json").unlink()
    c = _card(ID_ONLY)
    rb.attach([c])
    assert c["reference_basis"] == FAIL_CLOSED
    assert any("required card id" in p for p in rb.store_problems())


def test_required_card_with_no_pin_and_no_records_at_all(two):
    for f in two.glob("*.json"):
        f.unlink()
    a, c, other = _card(ID_A), _card(ID_ONLY), _card("cat_unrelated")
    rb.attach([a, c, other])
    assert a["reference_basis"] == FAIL_CLOSED and c["reference_basis"] == FAIL_CLOSED
    assert "reference_basis" not in other


def test_empty_store(two):
    for f in two.glob("*.json"):
        f.unlink()
    a, b, other = _card(ID_A, PIN_A), _card("x", PIN_B), _card("cat_unrelated", "0f" * 32)
    rb.attach([a, b, other])
    assert a["reference_basis"] == FAIL_CLOSED and b["reference_basis"] == FAIL_CLOSED
    assert other["reference_basis"] == FAIL_CLOSED       # unusable store: any pinned card fails closed
    assert rb.store_problems()


def test_cat_prefix_and_case_do_not_hide_a_required_card(two):
    (two / "a.json").unlink()
    e = _card("cat:Synthetic_Card_A")
    rb.attach([e])
    assert e["reference_basis"] == FAIL_CLOSED


def test_duplicate_pin_is_ambiguous_and_fails_closed(two):
    _write(two, "z_copy.json", card_id="synthetic_copy", candidate_sha256=PIN_A, basis={**BASIS, "rpm": 999})
    a, b = _card(ID_A, PIN_A), _card(ID_B, PIN_B)
    rb.attach([a, b])
    assert a["reference_basis"] == FAIL_CLOSED          # never "the last file wins"
    assert b["reference_basis"] == FAIL_CLOSED          # the store is ambiguous: every pinned card fails closed
    assert any("pin claimed by two files" in p for p in rb.store_problems())


def test_duplicate_card_id_is_ambiguous_and_fails_closed(two):
    _write(two, "z_copy.json", card_id=ID_B.upper(), candidate_sha256="c3" * 32, basis=BASIS_B)
    b = _card(ID_B, PIN_B)
    rb.attach([b])
    assert b["reference_basis"] == FAIL_CLOSED
    assert any("card id claimed by two files" in p for p in rb.store_problems())


def test_identical_duplicate_is_still_refused(two):
    _write(two, "a_again.json", card_id=ID_A, candidate_sha256=PIN_A, basis=BASIS_A)
    a = _card(ID_A, PIN_A)
    rb.attach([a])
    assert a["reference_basis"] == FAIL_CLOSED


def test_unsupported_schema_is_invalid_and_fails_closed(two):
    (two / "b.json").write_text(json.dumps({"schema": "reference-basis-9", "card_id": ID_B,
                                            "candidate_sha256": PIN_B, "basis": BASIS_B}), encoding="utf-8")
    a, b = _card(ID_A, PIN_A), _card(ID_B, PIN_B)
    rb.attach([a, b])
    assert a["reference_basis"] == FAIL_CLOSED and b["reference_basis"] == FAIL_CLOSED
    assert any("b.json" in p for p in rb.store_problems())


def test_unrelated_cards_stay_untouched_in_a_healthy_store(two):
    e = _card("cat_unrelated")
    rb.attach([e])
    assert "reference_basis" not in e


def test_built_in_list_covers_the_real_card_without_any_file(tmp_path, monkeypatch):
    """The shipped digests make the real audited card required even with no store (digests only, no names here)."""
    monkeypatch.setattr(rb, "_DIR", tmp_path / "nowhere")
    assert rb.REQUIRED_ID_DIGESTS and rb.REQUIRED_PIN_DIGESTS
    some_id = next(iter(rb.REQUIRED_ID_DIGESTS))
    assert len(some_id) == 64 and rb.store_problems()
