"""A sweep chart belongs to ONE machine, and says which.

User 2026-09-10: *"опять косяк, я запускал sweep одних параметров, а в
результате получил старый sweep от другого мотора"*.

The backend reloads `config/.last_scan.json` into its scan state every time it
starts, so after any restart the Sweep panel's first poll answered with
yesterday's chart — variable cards and all — and nothing in the payload said it
had been computed on a different motor.  The server already marked it
`restored_from_disk`; no client ever read that flag, which is why the identity
is now a NUMBER the client can compare rather than a flag it can ignore.

The fingerprint is the same one the eval cache is keyed by, so "the cache would
miss on this machine" and "this chart is foreign" can never disagree.
"""
from __future__ import annotations

import json

import pytest

import motor_ai_sim.routes.optimization as opt


def test_a_the_stamp_names_the_machine():
    st = opt._machine_stamp()                       # noqa: SLF001
    assert isinstance(st, dict)
    fp = st.get("fingerprint")
    assert isinstance(fp, str) and fp and fp != "nofp", st
    # …and it IS the eval cache's own fingerprint, not a second opinion
    assert fp == opt._config_fingerprint()          # noqa: SLF001


def test_b_a_saved_scan_carries_it(tmp_path, monkeypatch):
    dst = tmp_path / "last_scan.json"
    monkeypatch.setattr(opt, "_scan_store_path", lambda: str(dst))
    opt._save_last_scan({"points": [{"T_em_Nm": 1.0}], "run_id": "sweep_test"})  # noqa: SLF001
    saved = json.loads(dst.read_text(encoding="utf-8"))
    assert saved["run_id"] == "sweep_test"
    assert saved["machine"]["fingerprint"] == opt._config_fingerprint()  # noqa: SLF001


def test_c_a_scan_that_already_named_its_machine_is_left_alone(tmp_path, monkeypatch):
    """`setdefault`, not overwrite: a worker that stamped the machine it ACTUALLY
    solved must not have it replaced by whatever is loaded at save time."""
    dst = tmp_path / "last_scan.json"
    monkeypatch.setattr(opt, "_scan_store_path", lambda: str(dst))
    opt._save_last_scan({"points": [], "machine": {"fingerprint": "deadbeef",  # noqa: SLF001
                                                   "die": "OTHER"}})
    saved = json.loads(dst.read_text(encoding="utf-8"))
    assert saved["machine"] == {"fingerprint": "deadbeef", "die": "OTHER"}


def test_d_the_progress_payload_says_what_is_loaded_now():
    out = opt.scan_progress()
    assert out["machine_now"]["fingerprint"] == opt._config_fingerprint()  # noqa: SLF001


def test_e_a_restored_scan_is_still_flagged(tmp_path, monkeypatch):
    """The flag stays — it is the human-readable half of the same fact."""
    dst = tmp_path / "last_scan.json"
    dst.write_text(json.dumps({"points": [], "run_id": "old",
                               "machine": {"fingerprint": "deadbeef"}}),
                   encoding="utf-8")
    monkeypatch.setattr(opt, "_scan_store_path", lambda: str(dst))
    keep = opt._scan_state.get("result")             # noqa: SLF001
    try:
        opt._load_last_scan()                        # noqa: SLF001
        got = opt._scan_state["result"]              # noqa: SLF001
        assert got["restored_from_disk"] is True
        assert got["machine"]["fingerprint"] == "deadbeef"
        # …and it does NOT claim to be this machine's
        assert got["machine"]["fingerprint"] != opt._config_fingerprint()  # noqa: SLF001
    finally:
        opt._scan_state["result"] = keep             # noqa: SLF001


@pytest.mark.parametrize("res, now, want", [
    ({"machine": {"fingerprint": "a"}}, {"fingerprint": "a"}, True),
    ({"machine": {"fingerprint": "a"}}, {"fingerprint": "b"}, False),
    ({}, {"fingerprint": "a"}, False),               # unstamped is foreign
    ({"machine": {"fingerprint": "a"}}, {}, False),
])
def test_f_the_rule_the_panel_applies(res, now, want):
    """The same comparison `SweepStudyPanel.sameMachine` makes, pinned here so
    the two halves cannot drift: both stamps must exist AND be equal."""
    a = (res.get("machine") or {}).get("fingerprint")
    b = (now or {}).get("fingerprint")
    assert bool(a and b and str(a) == str(b)) is want
