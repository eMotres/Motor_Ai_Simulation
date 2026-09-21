"""``solve_to: "continuous"`` — the THIRD answer, beside ``steady`` / ``limits``.

Owner, 2026-09-21 (screenshot of the coupled panel's "Solve to" selector):
*«давай сделаем кнопку, или лучше добавим ещё один элемент в меню»* — a third
option, "continuous rating": the largest current the machine may hold FOR EVER
at THIS duty's saved cooling (S1), from the pass the loop already made.

What this file pins is the WIRING, not the physics (that is
``tests/test_continuous_rating.py``):

  (a) ``continuous`` runs the loop with EXACTLY the ``limits`` stop rule — same
      electromagnetic passes, same crossing behaviour — so a machine that never
      reaches a limit is still a ``steady`` record and one that does still gets
      the one extra pass AT the limit.  No extra electromagnetic pass is bought
      by asking for the rating.
  (b) The rating step is called once, with this run's own cooling / em / field,
      and its block rides the record as ``continuous_rating``.
  (c) The progress phases name the search ("continuous rating N/M").
  (d) ``limits`` on its own grows no ``continuous_rating`` key — behaviour is
      unchanged unless the third option is what was asked for.
  (e) The block survives ``duty_results.compact_coupled`` into the stored
      record.

Both halves of the loop are faked, exactly as ``test_coupled_limited_state``
fakes them; the rating step itself is faked too, because its own arithmetic is
``coupled_continuous_rating``'s to prove.

── THE PRODUCTION DEFECT (2026-09-21) ────────────────────────────────────────
Seen on `/srv/motres/workspaces/.../​.last_coupled.json` (Ø50 L15): a point 41 s
from its winding class (`time_to_limit.within_limits: false`) came back rated
at 1.02 x its OWN duty current — physically impossible, a point that reaches
its limit cannot hold MORE current for ever.  Two causes, both fixed in
`_continuous_rating_for_loop`, both pinned below (the section marked "THE FIX,
PINNED"):

  1. the reference map was the LOOP's own ``field`` — on a ``limits``-mode pass
     that is the machine translated onto the INSTANT it crosses its limit
     (`rescale_map_to_nodes`), so the network the search fits sits ON the
     limit at s ≈ 1 by construction.  Fixed: a fresh STEADY 2-D thermal solve,
     looked up by the pass's own identity (no `_em_map` override), exactly as
     the standalone `/continuous_rating` route already does.
  2. the re-solve crashed with a TypeError, because the loop's own `em` does
     not carry the per-element loss mesh at all (it lives in a separate
     snapshot store `field_snapshot` keys into) — fixed the same way, by
     letting the lookup capture its own mesh-level map (`capture["em"]`)
     instead of handing back the loop's bare transient dict.

A belt-and-braces consistency guard (`_cr_consistency_guard`) also catches ANY
other way a rating could contradict this run's own `time_to_limit` verdict,
and a re-solve that never converges is marked `trustworthy: false` rather than
kept as a network-only pass.
"""
from __future__ import annotations

import pytest

from tests.test_coupled import COOLING as PANEL_COOLING, EM_BODY
from tests.test_coupled_limited_state import _fake, _ttl_block

LOOP_BODY = {**EM_BODY, "thermal_settings": PANEL_COOLING,
             "magnet_temp_c": 90.0, "mechanical": False}


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


def _run(client, **body):
    r = client.post("/api/coupled/run",
                    json={**LOOP_BODY, "max_iter": 3, "tol_k": 1.0,
                          "cold_constants": False, **body})
    assert r.status_code == 200, r.text[:800]
    return r.json()["coupling"]


_RATING_BLOCK = {
    "ok": True, "feasible": True, "trustworthy": True,
    "s": 0.54, "I_cont_A_rms": 34.36, "I_cont_A_peak": 48.59,
    "limiting_part": "magnet",
    "temperatures_c": {"winding": 103.0, "magnet": 149.7},
    "limits_c": {"winding": 200.0, "magnet": 149.7},
    "power": {"T_em_Nm": 1.175, "P_shaft_W": 1230.0, "eta_shaft": 0.933},
    "reference": {"I_phase_rms_A": 63.64, "rpm": 10000.0},
    "cooling_label": "forced air 40 m/s + bore air 10 m/s, 30 °C",
    "headline": "34.4 A rms continuously, 1230 W at the shaft — the magnet "
                "sits on 149.7 °C",
}

#: The same block, as it looks AFTER a real S1 verification pass confirmed it
#: (owner 2026-09-21, second addendum) — for the report/datasheet tests, which
#: read a static record and never run the loop's own verification logic.
_RATING_BLOCK_VERIFIED = {**_RATING_BLOCK, "verified": True,
                          "verification_passes": 1, "miss_K": -0.5}


def _fake_rating(monkeypatch, *, block=None):
    from motor_ai_sim.routes import coupled as cp

    calls = []

    def _rate(body, *, cooling, rpm, coil_temp_c, magnet_temp_c, duty, mode,
              time_to_limit=None):
        calls.append({"cooling": dict(cooling or {}), "duty": duty,
                      "mode": mode, "rpm": rpm, "coil_temp_c": coil_temp_c,
                      "magnet_temp_c": magnet_temp_c,
                      "time_to_limit": dict(time_to_limit or {})})
        return None if block is None else dict(block)

    monkeypatch.setattr(cp, "_continuous_rating_for_loop", _rate, raising=True)
    return calls


# ---------------------------------------------------------------------------
# (d) limits alone is untouched
# ---------------------------------------------------------------------------

def test_limits_alone_grows_no_continuous_rating_key(client, monkeypatch):
    calls = _fake_rating(monkeypatch)
    seen = _fake(monkeypatch, ttl=_ttl_block())
    c = _run(client, solve_to="limits")
    assert c["mode"] == "limited"
    assert "continuous_rating" not in c
    assert calls == []
    # …and the stop rule is exactly what it was: one pass at the body's own
    # temperature, one AT the limit.
    assert seen["coil_in"] == [120.0, 200.0]


def test_the_default_grows_no_continuous_rating_key(client, monkeypatch):
    calls = _fake_rating(monkeypatch)
    _fake(monkeypatch, ttl=_ttl_block())
    c = _run(client)
    assert c["solve_to"] == "steady"
    assert "continuous_rating" not in c
    assert calls == []


# ---------------------------------------------------------------------------
# (a) the loop's own stop rule is unchanged by "continuous"
# ---------------------------------------------------------------------------

def test_continuous_stops_the_loop_exactly_as_limits_does(client, monkeypatch):
    """Same crossing, same one extra pass AT the limit — the LOOP's own stop
    rule buys no extra electromagnetic solve; the S1 VERIFICATION passes are
    a separate, later thing (owner 2026-09-21: the network's answer must be
    confirmed with a real EM pass), counted on their own."""
    _fake_rating(monkeypatch, block=dict(_RATING_BLOCK))
    seen = _fake(monkeypatch, ttl=_ttl_block())
    c = _run(client, solve_to="continuous")
    assert c["solve_to"] == "continuous"
    # The loop's own two passes (body's 120 °C, then AT the limit 200 °C) —
    # unchanged by asking for the rating.
    assert seen["coil_in"][:2] == [120.0, 200.0], seen["coil_in"]
    assert c["limited"]["part"] == "winding"
    # …and the S1 verification ran on top, at the rating's OWN estimated
    # winding temperature (103.0 °C from _RATING_BLOCK) — the fake thermal
    # solve never settles near the magnet's 149.7 °C card, so it used its
    # full two-pass budget and reported the miss rather than a false "verified".
    assert seen["coil_in"][2:] == [103.0, 103.0]
    cr = c["continuous_rating"]
    assert cr["verified"] is False
    assert cr["verification_passes"] == 2
    assert cr["I_estimated_A_rms"] == 34.36
    # THE RECORD'S OWN MACHINE MOVED TO S1 (owner's rule): the coupled
    # block's temperatures are no longer the setpoint's 200 °C AT-THE-LIMIT
    # pass, they are the S1 verification's own — even unverified, it is the
    # real pass the record now describes, not the setpoint's.
    assert c["coil_temp_c"] == 103.0
    # …and the setpoint's own story survives, unabbreviated.
    assert cr["duty_point"]["I_phase_rms_A"] == LOOP_BODY["I_phase_rms"]
    assert "winding reaches 200" in cr["duty_point"]["verdict"]


def test_continuous_on_a_point_inside_every_limit_is_still_a_steady_record(
        client, monkeypatch):
    _fake_rating(monkeypatch, block=dict(_RATING_BLOCK))
    seen = _fake(monkeypatch, ttl=_ttl_block(within=True))
    c = _run(client, solve_to="continuous")
    assert c["mode"] == "steady"
    assert "limited" not in c
    assert 172.5 not in seen["coil_in"]
    # The rating step still runs on the steady pass — a machine inside every
    # limit still has a continuous rating worth finding.
    assert "continuous_rating" in c


# ---------------------------------------------------------------------------
# (b) the rating step is called once, with the pass's own state
# ---------------------------------------------------------------------------

def test_the_rating_step_is_called_once_with_this_runs_own_state(
        client, monkeypatch):
    calls = _fake_rating(monkeypatch, block=dict(_RATING_BLOCK))
    _fake(monkeypatch, ttl=_ttl_block())
    c = _run(client, solve_to="continuous")
    assert len(calls) == 1
    call = calls[0]
    assert call["mode"] == "motor"
    # The temperatures handed to the rating step are THIS PASS's own — the
    # limited pass's coil (200 °C) and magnet (48.2 °C), never the body's
    # original 120 °C / 90 °C.
    assert call["coil_temp_c"] == 200.0
    assert call["magnet_temp_c"] == 48.2
    assert call["rpm"]
    assert call["time_to_limit"]
    # The cooling handed to the rating step is this run's own — the panel
    # cooling body carries, not a re-read of a stale duty save.
    assert call["cooling"]


def test_the_block_rides_the_record_as_continuous_rating(client, monkeypatch):
    _fake_rating(monkeypatch, block=dict(_RATING_BLOCK))
    _fake(monkeypatch, ttl=_ttl_block())
    c = _run(client, solve_to="continuous")
    cr = c["continuous_rating"]
    # I_cont_A_rms is the VERIFIED reading (owner 2026-09-21) — the network's
    # own first answer survives separately, as I_estimated_A_rms.
    assert cr["I_estimated_A_rms"] == 34.36
    assert cr["I_cont_A_rms"] == 20.0          # the fake EM pass's own current
    assert cr["limiting_part"] == "magnet"
    assert cr["cooling_label"].startswith("forced air 40 m/s")


def test_a_refused_rating_is_reported_not_raised(client, monkeypatch):
    """A condition the rating step cannot answer (no capacities, a
    non-monotone map, …) must not cost the loop's own converged answer."""
    _fake_rating(monkeypatch,
                 block={"ok": False, "refusal": {"error": "no part limits",
                                                 "error_code": "no_part_limits"}})
    _fake(monkeypatch, ttl=_ttl_block())
    c = _run(client, solve_to="continuous")
    assert c["mode"] == "limited"          # the loop's own answer stands
    assert c["continuous_rating"]["ok"] is False
    assert c["continuous_rating"]["refusal"]["error_code"] == "no_part_limits"


# ---------------------------------------------------------------------------
# THE S1 VERIFICATION PASS (owner 2026-09-21) — confirm the network's
# estimate with a real EM + thermal pass; adopt it as the record's machine.
# ---------------------------------------------------------------------------

def _fake_verify(monkeypatch, *, winding_max_at, magnet_max_at):
    """A CURRENT-AWARE EM/thermal fake, so a verification pass/correction/
    convergence scenario can be built exactly: ``winding_max_at(I)`` /
    ``magnet_max_at(I)`` are this map's own hot-spot reading at that current."""
    from motor_ai_sim.routes import coupled as cp

    seen = {"I": [], "coil_in": []}

    def _em(body, *, coil_temp_c, magnet_temp_c, inverter=None, **_k):
        i = float(body.get("I_phase_rms") or 0.0)
        seen["I"].append(round(i, 4))
        seen["coil_in"].append(round(float(coil_temp_c), 4))
        return {"summary": {"P_loss_total_W": 100.0, "T_em_avg_Nm": 5.0,
                            "rpm": 1000.0, "coil_temp_C": float(coil_temp_c),
                            "I_phase_rms_A": i},
                "I_phase_rms_solved_A": i}

    def _th(body, cooling, *, coil_temp_c, magnet_temp_c, rpm, **_k):
        i = float(body.get("I_phase_rms") or 0.0)
        wmax, mmax = winding_max_at(i), magnet_max_at(i)
        return {"ok": True,
                "components": {"winding": {"avg": wmax - 5.0, "max": wmax},
                               "magnet": {"avg": mmax - 2.0, "max": mmax}}}

    monkeypatch.setattr(cp, "_em_run", _em, raising=True)
    monkeypatch.setattr(cp, "_thermal_solve", _th, raising=True)
    return seen


def test_a_verification_within_3k_is_accepted_in_one_pass(client, monkeypatch):
    _fake_rating(monkeypatch, block=dict(_RATING_BLOCK))    # magnet, 149.7 °C
    seen = _fake_verify(monkeypatch, winding_max_at=lambda i: 100.0,
                        magnet_max_at=lambda i: 148.0)       # 1.7 K under
    c = _run(client, solve_to="continuous")
    cr = c["continuous_rating"]
    assert cr["verified"] is True
    assert cr["verification_passes"] == 1
    assert abs(cr["miss_K"]) <= 3.0
    # The loop makes its own two passes first (unmocked here, both at this
    # fixture's fixed I=20.0); the verification's OWN call is the one after.
    verify_calls = seen["I"][2:]
    assert len(verify_calls) == 1
    # THE VERIFIED CURRENT is what the (fake) EM pass actually drew.
    assert cr["I_cont_A_rms"] == pytest.approx(_RATING_BLOCK["I_cont_A_rms"])
    assert cr["I_estimated_A_rms"] == _RATING_BLOCK["I_cont_A_rms"]
    # …and the RECORD becomes this pass: the block's own temperatures are the
    # verification's, not the setpoint's AT-THE-LIMIT 200 °C.
    assert c["coil_temp_c"] == 103.0                        # RATING's own guess
    # THE FEM TORQUE/POWER are real now, not the linear estimate.
    assert cr["power"]["T_em_Nm"] == 5.0
    assert "real electromagnetic pass" in cr["power"]["basis"]


def test_a_miss_over_3k_triggers_one_correction_pass_and_can_converge(
        client, monkeypatch):
    _fake_rating(monkeypatch, block=dict(_RATING_BLOCK))
    # First pass (at the estimate, 34.36 A): 50.3 K over the magnet card.
    # The correction must pull the current DOWN; the second pass then lands
    # under 30 A, where this fixture settles comfortably inside the card.
    def magnet_fn(i):
        return 200.0 if i > 30.0 else 148.0
    seen = _fake_verify(monkeypatch, winding_max_at=lambda i: 50.0,
                        magnet_max_at=magnet_fn)
    c = _run(client, solve_to="continuous")
    cr = c["continuous_rating"]
    verify_calls = seen["I"][2:]
    assert len(verify_calls) == 2
    assert verify_calls[1] < verify_calls[0]    # corrected DOWN, over the card
    assert cr["verification_passes"] == 2
    assert cr["verified"] is True
    assert cr["I_cont_A_rms"] == pytest.approx(verify_calls[1], abs=1e-3)


def test_a_verification_still_off_after_two_passes_states_the_miss(
        client, monkeypatch):
    """The cap is real: a map that never settles near the card is reported
    with the miss stated, not chased for ever."""
    _fake_rating(monkeypatch, block=dict(_RATING_BLOCK))
    seen = _fake_verify(monkeypatch, winding_max_at=lambda i: 50.0,
                        magnet_max_at=lambda i: 300.0)       # never settles
    c = _run(client, solve_to="continuous")
    cr = c["continuous_rating"]
    verify_calls = seen["I"][2:]
    assert len(verify_calls) == 2                # the cap, never chased further
    assert cr["verification_passes"] == 2
    assert cr["verified"] is False
    assert cr["miss_K"] is not None and cr["miss_K"] > 3.0
    assert "still" in cr["note"] and "verification pass" in cr["note"]


# ---------------------------------------------------------------------------
# (c) the progress phases name the search
# ---------------------------------------------------------------------------

def test_progress_phases_name_the_continuous_rating_search(client, monkeypatch):
    # `RouteProgress` is a `__slots__` proxy (no per-instance patching), so the
    # CLASS method is what is recorded around.
    from motor_ai_sim.progress import RouteProgress

    phases = []
    _orig = RouteProgress.update

    def _rec(self, *a, **k):
        if k.get("phase"):
            phases.append(k["phase"])
        return _orig(self, *a, **k)

    monkeypatch.setattr(RouteProgress, "update", _rec, raising=True)
    _fake_rating(monkeypatch, block=dict(_RATING_BLOCK))
    _fake(monkeypatch, ttl=_ttl_block())
    _run(client, solve_to="continuous")
    assert any("continuous rating" in p for p in phases), phases


# ---------------------------------------------------------------------------
# solve_to aliases and the refusal message
# ---------------------------------------------------------------------------

def test_solve_to_aliases_resolve_to_continuous():
    from motor_ai_sim.routes.coupled import _solve_to

    for raw in ("continuous", "continuous_rating", "rating", "s1", "S1",
               " Continuous "):
        assert _solve_to({"solve_to": raw}) == "continuous"


def test_unknown_solve_to_mentions_all_three_questions(client, monkeypatch):
    _fake(monkeypatch, ttl=_ttl_block())
    r = client.post("/api/coupled/run", json={**LOOP_BODY, "solve_to": "asap"})
    assert r.status_code == 422
    d = r.json()["detail"]
    assert d["error_code"] == "unknown_solve_to"
    assert "continuous" in d["error"]


# ---------------------------------------------------------------------------
# (e) the block survives the store
# ---------------------------------------------------------------------------

def test_continuous_rating_survives_compact_coupled_into_the_duty_record():
    from motor_ai_sim.duty_results import compact_coupled

    rec = compact_coupled({"coupling": {"coil_temp_c": 200.0,
                                        "solve_to": "continuous",
                                        "mode": "limited",
                                        "continuous_rating": dict(_RATING_BLOCK)},
                           "computed_at": "2026-09-21T00:00:00"})
    assert rec["continuous_rating"]["I_cont_A_rms"] == 34.36
    assert rec["continuous_rating"]["limiting_part"] == "magnet"
    # …and a record that never asked grows no key at all, never a null one.
    plain = compact_coupled({"coupling": {"coil_temp_c": 120.0}})
    assert "continuous_rating" not in plain


# ---------------------------------------------------------------------------
# the report's row group — present only when a duty asked, never computed here
# ---------------------------------------------------------------------------

_REC_WITH_RATING = {"coil_temp_c": 103.0, "continuous_rating": dict(_RATING_BLOCK)}
_REC_NO_RATING = {"coil_temp_c": 120.0}


def test_the_report_functions_read_the_stored_block_only():
    from motor_ai_sim import report as rp

    assert rp.continuous_rating_of(_REC_WITH_RATING) == _RATING_BLOCK
    assert rp.continuous_rating_of(_REC_NO_RATING) is None
    assert rp.continuous_rating_of(None) is None

    words = rp.continuous_rating_words(_REC_WITH_RATING)
    assert words.startswith("34.4 A rms")
    assert "magnet" in words and "149.7" in words
    assert rp.continuous_rating_words(_REC_NO_RATING) == ""

    limited = rp.continuous_rating_limit_words(_REC_WITH_RATING)
    assert limited.startswith("magnet, 149.7 / 150")
    assert "torque linear in current" in limited
    assert "forced air 40 m/s" in limited
    assert rp.continuous_rating_limit_words(_REC_NO_RATING) == ""

    refused = {"continuous_rating": {"ok": False, "feasible": False,
                                     "refusal": {"error": "no capacities"}}}
    assert rp.continuous_rating_words(refused) == "no capacities"
    assert rp.continuous_rating_limit_words(refused) == "no capacities"

    not_trustworthy = {"continuous_rating": {"ok": True, "feasible": True,
                                             "trustworthy": False}}
    assert "NOT" not in rp.continuous_rating_words(not_trustworthy).upper() \
        or "not a rating" in rp.continuous_rating_words(not_trustworthy)


def test_the_row_group_appears_only_when_a_duty_asked_for_it():
    from motor_ai_sim import report as rp

    cols_with = [{"duty": "rated", "em": {}, "d": {},
                  "res": {"coupled": dict(_REC_WITH_RATING)}},
                 {"duty": "peak", "em": {}, "d": {},
                  "res": {"coupled": dict(_REC_NO_RATING)}}]
    _, rows_with = rp.coupled_compare_rows(cols_with)
    by_label = {r[0]: r for r in rows_with}
    assert by_label["Continuous rating (S1), current [A rms]"][1] == "34.4"
    assert by_label["Continuous rating (S1), current [A rms]"][2] == "—"
    lim = by_label["Continuous rating (S1), limited by"][1]
    assert lim.startswith("magnet, 149.7 / 150")
    assert "torque linear in current" in lim     # not verified — an estimate
    # Owner's first addendum, 2026-09-21: torque and shaft power printed as a
    # linear ESTIMATE were dropped.  Neither duty here was ever VERIFIED
    # (owner's second addendum, same day, is what re-admits them), so the
    # row group has nothing to print and `_drop_empty` takes it out whole —
    # the same "silent unless it applies" rule as the group itself.
    assert not any(l.startswith("Continuous rating (S1), torque")
                  or l.startswith("Continuous rating (S1), shaft power")
                  for l in by_label)

    # …and once VERIFIED, the real FEM torque/power print, and the clause
    # says so instead of naming the linear-estimate approximation.
    rec_verified = {"coil_temp_c": 103.0,
                    "continuous_rating": dict(_RATING_BLOCK_VERIFIED)}
    _, rows_v = rp.coupled_compare_rows(
        [{"duty": "rated", "em": {}, "d": {}, "res": {"coupled": rec_verified}}])
    by_label_v = {r[0]: r for r in rows_v}
    assert by_label_v["Continuous rating (S1), torque [N·m]"][1] == "1.175"
    assert by_label_v["Continuous rating (S1), shaft power [W]"][1] == "1,230"
    lim_v = by_label_v["Continuous rating (S1), limited by"][1]
    assert "confirmed with a real electromagnetic pass" in lim_v
    assert "torque linear in current" not in lim_v

    # …and when NO duty in the report ever asked, the whole group is silent —
    # `_drop_empty` takes it out, exactly as the "Warning" row is taken out of
    # a report where the loop never warned.
    cols_without = [{"duty": "rated", "em": {}, "d": {},
                     "res": {"coupled": dict(_REC_NO_RATING)}},
                    {"duty": "peak", "em": {}, "d": {},
                     "res": {"coupled": dict(_REC_NO_RATING)}}]
    _, rows_without = rp.coupled_compare_rows(cols_without)
    labels_without = {r[0] for r in rows_without}
    assert not any(l.startswith("Continuous rating") for l in labels_without)


# ---------------------------------------------------------------------------
# the datasheet — one row per duty, present only when a duty has the block
# ---------------------------------------------------------------------------

def test_the_datasheet_carries_one_row_per_duty_when_a_duty_has_it(tmp_path):
    from openpyxl import load_workbook

    from motor_ai_sim.datasheet import build_datasheet

    die_doc = {"geometry": {"num_poles": 28, "num_slots": 24,
                            "stator_diameter": 85.0, "motor_length": 13.0}}
    cfg_doc = {"winding": {"star_delta": "star"},
               "duties": [{"name": "peak", "current_arms": 45.96, "rpm": 1000.0,
                           "gamma_deg": 2.0,
                           "summary": {"T_em_avg_Nm": 7.57,
                                       "I_phase_rms_A": 45.96,
                                       "R_phase_ohm": 0.05}},
                          {"name": "rated", "current_arms": 26.09, "rpm": 1000.0,
                           "gamma_deg": 2.0,
                           "summary": {"T_em_avg_Nm": 6.12,
                                       "I_phase_rms_A": 26.09,
                                       "R_phase_ohm": 0.05}}]}

    def _row(blob, label):
        p = tmp_path / "card.xlsx"
        p.write_bytes(blob)
        ws = load_workbook(p).active
        for r in range(1, ws.max_row + 1):
            if str(ws.cell(row=r, column=1).value or "") == label:
                return [ws.cell(row=r, column=2).value,
                        ws.cell(row=r, column=3).value]
        return None

    # `duties` are sorted continuous-looking first, "peak" last (the paper
    # card's own order), so the columns are [rated, peak].
    label = "Continuous current (S1) at saved cooling (A rms)"
    torque_label = "Continuous torque (S1) at saved cooling (N·m)"
    blob = build_datasheet(
        die="D", cfg="L13", die_doc=die_doc, cfg_doc=cfg_doc,
        coupled={"peak": {"continuous_rating": dict(_RATING_BLOCK)}})
    with_ = _row(blob, label)
    assert with_ is not None
    # The duty with no block of its own carries no number on the same row.
    assert with_[0] is None
    assert with_[1] == pytest.approx(34.4, abs=1e-6)   # one decimal, per `row`
    # The current is not VERIFIED (owner 2026-09-21, second addendum) — the
    # torque row prints no number for it, ever the linear estimate.
    torque_unverified = _row(blob, torque_label)
    assert torque_unverified is not None
    assert torque_unverified == [None, None]

    without = _row(build_datasheet(die="D", cfg="L13", die_doc=die_doc,
                                   cfg_doc=cfg_doc, coupled=None), label)
    assert without is None                 # no row at all — never computed here

    # …and once the rating IS verified, the torque prints — real, from the
    # FEM pass that confirmed it.
    blob_v = build_datasheet(
        die="D", cfg="L13", die_doc=die_doc, cfg_doc=cfg_doc,
        coupled={"peak": {"continuous_rating": dict(_RATING_BLOCK_VERIFIED)}})
    torque_verified = _row(blob_v, torque_label)
    assert torque_verified[0] is None
    assert torque_verified[1] == pytest.approx(1.175, abs=1e-6)


# ---------------------------------------------------------------------------
# THE FIX, PINNED (2026-09-21 production defect) — _continuous_rating_for_loop
# itself, called directly, with every dependency mocked so the WIRING is what
# is pinned: which map it rates from, at which point, and how a bad answer is
# caught.
# ---------------------------------------------------------------------------

def _mock_rating_deps(monkeypatch, *, rate_return, solve_map_calls=None,
                      capture_em=True):
    """Every collaborator `_continuous_rating_for_loop` calls, mocked.

    Returns the list `_cr_solve_map` calls are recorded into, so a test can
    assert WHICH map (whose point, with or without `em_map`) the rating was
    built from — the exact thing the production bug got wrong.
    """
    from motor_ai_sim.routes import coupled as cp
    from motor_ai_sim.routes import thermal as th
    from motor_ai_sim import thermal_capacities as tc

    calls = solve_map_calls if solve_map_calls is not None else []

    def _solve_map(point, cooling, *, em_map=None, em_source=None,
                   capture=None):
        calls.append({"point": dict(point), "cooling": dict(cooling or {}),
                      "em_map": em_map, "capture_given": capture is not None})
        if capture is not None and capture_em:
            capture["em"] = {"domain_per_tri": [0], "loss_density_per_tri": [1.0],
                             "P_cu_W": 10.0, "P_cu_exact_W": 10.0}
            capture["loss_source"] = {"kind": "test_reference"}
        return {"ok": True,
               "components": {"winding": {"avg": 300.0, "max": 320.0},
                              "magnet": {"avg": 150.0, "max": 155.0}},
               "P_cu_exact_W": 10.0}

    monkeypatch.setattr(cp, "_cr_solve_map", _solve_map, raising=True)
    monkeypatch.setattr(cp, "_cr_em_summary", lambda: {
        "I_phase_rms_A": 63.64, "rpm": 10000.0, "coil_temp_C": 200.0,
        "T_em_avg_Nm": 2.176, "P_core_W": 13.2, "P_solid_W": 8.2,
    }, raising=True)
    monkeypatch.setattr(th, "_assignments", lambda: {"magnet": "F52SH_120C"},
                        raising=True)
    monkeypatch.setattr(th, "_dc_geometry", lambda geo: (
        {"stator_diameter": 50.0}, {}), raising=True)
    monkeypatch.setattr(th, "_dc_side_areas",
                        lambda field, summary, geom: {"a": 1.0}, raising=True)
    monkeypatch.setattr(tc, "part_capacities",
                        lambda summary, mats, x: {"winding": 1.0}, raising=True)

    def _rate(*, thermal_result, em_summary, caps, geometry, cooling,
             side_areas, d_housing_m, duty, mode, resolve, refit,
             magnet_grade):
        return dict(rate_return)

    monkeypatch.setattr(cp._ccr, "rate", _rate, raising=True)
    return calls


def test_the_rating_is_built_from_a_fresh_steady_lookup_not_the_loops_field(
        monkeypatch):
    """Cause (1) of the production defect: the reference must be a STEADY map
    looked up by the pass's own identity — never `_em_map`-injected from
    whatever the loop happens to be holding (which, in `limits`/`continuous`
    mode, can be the machine translated onto the INSTANT of a crossing)."""
    from motor_ai_sim.routes import coupled as cp

    calls = _mock_rating_deps(monkeypatch, rate_return={
        "feasible": True, "s": 0.5, "I_cont_A_rms": 30.0,
        "limiting_part": "magnet", "limits_c": {"magnet": 150.0},
        "temperatures_c": {"magnet": 150.0}, "notes": [],
        "converged": True, "n_thermal_fem_solves": 1})

    out = cp._continuous_rating_for_loop(
        {"geo": None}, cooling={"cooling_mode": "air", "air_speed_mps": 40.0},
        rpm=10000.0, coil_temp_c=200.0, magnet_temp_c=48.2, duty="peak",
        mode="motor", time_to_limit={"within_limits": False,
                                     "limiting_part": "winding"})

    assert out is not None and out["ok"] is True
    # ONE lookup for the reference — no `_em_map` override, so
    # `solve_thermal_field` finds its OWN map by identity rather than being
    # handed the loop's possibly-limited snapshot.
    assert len(calls) == 1
    assert calls[0]["em_map"] is None
    assert calls[0]["capture_given"] is True
    # …and it is looked up AT THE PASS's own temperatures, never the body's.
    assert calls[0]["point"]["coil_temp_c"] == 200.0
    assert calls[0]["point"]["magnet_temp_c"] == 48.2
    assert calls[0]["point"]["rpm"] == 10000.0


def test_a_scaled_pass_reuses_the_captured_map_not_a_fresh_lookup(monkeypatch):
    """The rescale passes (`resolve`) must scale the CAPTURED reference map —
    never re-look-up (that would silently drift onto a different run) and
    never crash on a map that lacks the mesh-level fields (cause 2)."""
    from motor_ai_sim.routes import coupled as cp

    calls = _mock_rating_deps(monkeypatch, rate_return={
        "feasible": True, "s": 0.5, "I_cont_A_rms": 30.0,
        "limiting_part": "magnet", "limits_c": {"magnet": 150.0},
        "temperatures_c": {"magnet": 150.0}, "notes": [],
        "converged": True, "n_thermal_fem_solves": 1})

    resolve_holder = {}

    def _rate(*, resolve, **kw):
        resolve_holder["resolve"] = resolve
        return {"feasible": True, "s": 0.5, "I_cont_A_rms": 30.0,
               "limiting_part": "magnet", "limits_c": {"magnet": 150.0},
               "temperatures_c": {"magnet": 150.0}, "notes": [],
               "converged": True, "n_thermal_fem_solves": 1}

    monkeypatch.setattr(cp._ccr, "rate", _rate, raising=True)
    cp._continuous_rating_for_loop(
        {"geo": None}, cooling={"cooling_mode": "air", "air_speed_mps": 40.0},
        rpm=10000.0, coil_temp_c=200.0, magnet_temp_c=48.2, duty="peak",
        mode="motor", time_to_limit=None)

    # Calling `resolve` a second time must not crash (the TypeError of the
    # production bug) and must reuse the SAME captured map, scaled.
    out2 = resolve_holder["resolve"](1.05)
    assert out2 is not None
    assert len(calls) == 2
    assert calls[1]["em_map"] is not None
    assert calls[1]["em_map"]["P_cu_exact_W"] == pytest.approx(10.0 * 1.05)


# ---------------------------------------------------------------------------
# the consistency guard
# ---------------------------------------------------------------------------

def test_over_the_limit_but_rated_above_the_duty_current_is_flagged():
    from motor_ai_sim.routes import coupled as cp

    block = {"ok": True, "feasible": True, "trustworthy": True, "s": 1.02,
            "notes": []}
    out = cp._cr_consistency_guard(
        block, {"within_limits": False, "limiting_part": "winding"})
    assert out["trustworthy"] is False
    assert any("CONTRADICTS" in n for n in out["notes"])
    assert "cannot hold MORE current" in out["note"]


def test_inside_every_limit_but_rated_below_the_duty_current_is_flagged():
    from motor_ai_sim.routes import coupled as cp

    block = {"ok": True, "feasible": True, "trustworthy": True, "s": 0.8,
            "notes": []}
    out = cp._cr_consistency_guard(block, {"within_limits": True})
    assert out["trustworthy"] is False
    assert any("CONTRADICTS" in n for n in out["notes"])


def test_a_consistent_answer_is_left_alone():
    from motor_ai_sim.routes import coupled as cp

    # over the limit, rated BELOW the duty current — consistent (the L13 peak
    # case: s* = 0.64).
    block = {"ok": True, "feasible": True, "trustworthy": True, "s": 0.64,
            "notes": ["some ordinary note"]}
    out = cp._cr_consistency_guard(
        block, {"within_limits": False, "limiting_part": "winding"})
    assert out.get("trustworthy", True) is True
    assert out["notes"] == ["some ordinary note"]

    # inside every limit, rated AT/above the duty current — consistent (the
    # L13 rated case: s* = 1.12).
    block2 = {"ok": True, "feasible": True, "trustworthy": True, "s": 1.12,
             "notes": []}
    out2 = cp._cr_consistency_guard(block2, {"within_limits": True})
    assert out2.get("trustworthy", True) is True


def test_the_guard_does_not_touch_an_already_untrustworthy_or_infeasible_block():
    from motor_ai_sim.routes import coupled as cp

    non_mono = {"ok": True, "feasible": True, "trustworthy": False, "s": 1.5,
               "notes": ["THE 2-D THERMAL SOLVE IS NOT MONOTONE ..."]}
    out = cp._cr_consistency_guard(
        non_mono, {"within_limits": False, "limiting_part": "winding"})
    assert out is non_mono                 # untouched, not re-wrapped

    infeasible = {"ok": True, "feasible": False, "s": None}
    assert cp._cr_consistency_guard(
        infeasible, {"within_limits": False}) is infeasible


def test_the_headline_of_a_contradiction_names_the_reason():
    from motor_ai_sim import coupled_continuous_rating as ccr
    from motor_ai_sim.routes import coupled as cp

    block = {"ok": True, "feasible": True, "trustworthy": True, "s": 1.02,
            "I_cont_A_rms": 64.9, "limiting_part": "winding", "notes": []}
    flagged = cp._cr_consistency_guard(
        block, {"within_limits": False, "limiting_part": "winding"})
    line = ccr.headline(flagged)
    assert line.startswith("NOT A RATING")
    assert "cannot hold MORE current" in line


# ---------------------------------------------------------------------------
# the re-solve failure path
# ---------------------------------------------------------------------------

def test_a_resolve_that_never_settles_is_marked_not_trustworthy(monkeypatch):
    """`coupled_continuous_rating.rate` itself only NOTES a re-solve that
    fails or does not converge and keeps going on the map(s) it already has —
    right for its own module, but a network-only pass must not be left
    looking trustworthy on the coupled record, which is what stamped the
    64.9 A / trustworthy:true answer on the record the owner saw."""
    from motor_ai_sim.routes import coupled as cp

    _mock_rating_deps(monkeypatch, rate_return={})   # unused; _rate overridden below
    monkeypatch.setattr(cp._ccr, "rate", lambda **kw: {
        "feasible": True, "s": 1.0195, "I_cont_A_rms": 64.9,
        "limiting_part": "winding", "limits_c": {"winding": 200.0},
        "temperatures_c": {"winding": 200.0},
        "notes": ["the loss map could not be re-solved at 1.022x the "
                 "reference copper (float() argument must be a string or a "
                 "real number, not 'NoneType')"],
        "converged": False, "n_thermal_fem_solves": 2}, raising=True)

    out = cp._continuous_rating_for_loop(
        {"geo": None}, cooling={"cooling_mode": "air", "air_speed_mps": 40.0},
        rpm=10000.0, coil_temp_c=200.0, magnet_temp_c=48.2, duty="peak",
        mode="motor", time_to_limit={"within_limits": False,
                                     "limiting_part": "winding"})

    assert out["trustworthy"] is False
    assert any("did not converge" in n for n in out["notes"])
    # The consistency guard does not re-wrap an already-untrustworthy block
    # (pinned separately) — the re-solve-failure note is reason enough on its
    # own, and is the one that actually explains THIS record.


def test_a_one_pass_answer_with_no_resolve_attempt_is_not_penalised(monkeypatch):
    """`n_thermal_fem_solves == 1` (the reference pass alone, no rescale
    attempted at all — e.g. `resolve` returned `None` on the first try because
    the reference itself could not be captured) must not be flagged by the
    re-solve-failure rule, which only fires once a SECOND pass was tried and
    still did not converge."""
    from motor_ai_sim.routes import coupled as cp

    _mock_rating_deps(monkeypatch, rate_return={
        "feasible": True, "s": 0.64, "I_cont_A_rms": 29.4,
        "limiting_part": "winding", "limits_c": {"winding": 200.0},
        "temperatures_c": {"winding": 200.0}, "notes": [],
        "converged": False, "n_thermal_fem_solves": 1})

    out = cp._continuous_rating_for_loop(
        {"geo": None}, cooling={"cooling_mode": "air", "air_speed_mps": 40.0},
        rpm=10000.0, coil_temp_c=200.0, magnet_temp_c=48.2, duty="peak",
        mode="motor", time_to_limit={"within_limits": False,
                                     "limiting_part": "winding"})
    assert out.get("trustworthy", True) is True
