"""The coupled loop's EM passes and the Simulation tab's Run must share ONE
ledger key for the same operating point — owner, 2026-09-22: *"я не совсем
понимаю, зачем он ещё пересчитывает электромагнитный расчёт, если во время
каплинга он уже считал его и нашёл эту точку?"*.

After a coupled run (any of the three ``solve_to`` answers — ``steady``,
``limits``, ``continuous``) auto-sets the Simulation tab's coil / magnet
temperature (and, for ``continuous``, the current) fields, pressing Run at
those exact panel values must be a ledger hit, not a second solve.

THE TWO CAUSES FOUND (both fixed here):

  1. ``routes/simulation.py``'s results-ledger key (``_sb_key_fields`` in
     ``get_fem_transient``) rounded ``magnet_temp_c`` to TWO decimals while
     ``coil_temp_c`` — and the Simulation tab's own round-trip
     (``web/coupledApi.ts`` ``adoptConvergedTemperatures``:
     ``Math.round(c.magnet_temp_c*10)/10``) — round to ONE.  A converged
     magnet temperature almost never lands on an exact tenth, so the key the
     loop's pass wrote under and the key the panel's next Run would look up
     disagreed on the second decimal digit nearly every time.  Same fix
     applied to the field-snapshot key (``_field_snap_key_fields``), which
     the J⟳ / Loss views read the same way.
  2. ``routes/coupled.py``'s loop fed the RAW, unrounded damped-update /
     at-the-limit / S1-verify-guess temperature into the electromagnetic
     pass that becomes the record.  Even with (1) fixed, Python's
     ``round(x, 1)`` and JavaScript's ``Math.round(x*10)/10`` disagree on a
     value that lands exactly on a ``.x5`` boundary (189.95 → 189.9 in
     Python's round-half-to-even, 190.0 in JS's round-half-up) — a coin flip
     that showed up on the 30 mm fixture on the first real run made while
     diagnosing this.  Fixed by rounding the temperature to the panel's own
     one-decimal precision BEFORE solving the pass that becomes the record,
     not after: the EM pass is then solved at the exact number the panel
     will show, so no re-rounding on either side can ever disagree with it.

THE REVERSE (also asked for): a plain Run made at an operating point BEFORE
Coupled thermal is switched on must be reused by the loop's own first pass,
which is solved at that same body-supplied coil/magnet temperature.  Fixed by
letting ``_em_run``'s new ``ledger`` parameter through to
``get_fem_transient`` — ON only for the loop's ``it == 1`` (and off again
the moment ``history_fresh`` — the coupled run's own "Recompute" — is set),
never for any later iteration, which must always solve at the temperature
the previous pass's thermal map fed back.

Real-solve tests reuse ``tests/test_coupled.py``'s 30 mm 12s/14p fixture and
``sandbox`` fixture (every store redirected to a tmp dir — see that file's
own docstring); the mocked wiring tests below cost nothing and pin the exact
place each rounding happens, independent of the physics.
"""
from __future__ import annotations

import pytest

from tests.test_coupled import EM_BODY, COOLING, client, sandbox  # noqa: F401
from tests.test_coupled_limited_state import _ttl_block


LOOP_BODY = {**EM_BODY, "thermal_settings": COOLING, "magnet_temp_c": 90.0,
            "mechanical": False}


# ---------------------------------------------------------------------------
# THE WIRING — mocked EM/thermal, no real solve (mirrors the sibling files'
# own ``_fake`` / ``_fake_verify`` pattern)
# ---------------------------------------------------------------------------

def _run(client, **body):
    r = client.post("/api/coupled/run",
                    json={**LOOP_BODY, "max_iter": 4, "tol_k": 1.0,
                          "cold_constants": False, **body})
    assert r.status_code == 200, r.text[:800]
    return r.json()["coupling"]


def test_the_damped_update_is_rounded_to_the_panels_one_decimal(
        client, monkeypatch):
    """Every temperature the loop feeds a LATER iteration's electromagnetic
    pass must already be at the panel's own precision — never the raw
    floating-point sum ``t + damping*d``."""
    from motor_ai_sim.routes import coupled as cp

    seen = {"coil_in": [], "magnet_in": []}

    def _em(body, *, coil_temp_c, magnet_temp_c, **_k):
        seen["coil_in"].append(coil_temp_c)
        seen["magnet_in"].append(magnet_temp_c)
        # A P_loss/hence delta that will NEVER land on an exact tenth, so a
        # test that passed by accident (the fixture's own numbers happening
        # to be clean) is not possible here.
        n = len(seen["coil_in"])
        return {"summary": {"P_loss_total_W": 733.333333 + n,
                            "T_em_avg_Nm": 5.0, "rpm": 1000.0,
                            "coil_temp_C": float(coil_temp_c)},
                "I_phase_rms_solved_A": 20.0}

    def _th(body, cooling, *, coil_temp_c, magnet_temp_c, rpm, **_k):
        # A map that keeps handing back a fractional correction (never inside
        # tol, so the loop keeps updating right up to max_iter) — e.g.
        # winding 3.333...K hotter than the input every time.
        return {"ok": True,
                "components": {"winding": {"avg": coil_temp_c + 3.333333,
                                           "max": coil_temp_c + 5.0},
                               "magnet": {"avg": (magnet_temp_c or 0.0) + 2.777777,
                                         "max": (magnet_temp_c or 0.0) + 4.0}}}

    monkeypatch.setattr(cp, "_em_run", _em, raising=True)
    monkeypatch.setattr(cp, "_thermal_solve", _th, raising=True)
    monkeypatch.setattr(cp, "_ttl_step", lambda *a, **k: None, raising=True)
    monkeypatch.setattr(cp, "_attach_coupling", lambda em, block: False,
                        raising=True)
    monkeypatch.setattr(cp, "_remember_last", lambda out, **k: None,
                        raising=True)

    c = _run(client, solve_to="steady", max_iter=4, tol_k=0.001)
    assert len(seen["coil_in"]) >= 3, "the loop must have kept iterating"
    # Pass 1 is the body's own 120.0 / 90.0 — untouched, nothing to round yet.
    assert seen["coil_in"][0] == 120.0
    assert seen["magnet_in"][0] == 90.0
    # Every LATER pass is fed the previous pass's damped update, which must
    # already sit on the panel's one-decimal grid.
    for t in seen["coil_in"][1:]:
        assert t == round(t, 1), t
    for t in seen["magnet_in"][1:]:
        assert t == round(t, 1), t
    # …and the record itself carries that same rounded number.
    assert c["coil_temp_c"] == round(c["coil_temp_c"], 1)
    assert c["magnet_temp_c"] == round(c["magnet_temp_c"], 1)


def test_the_at_the_limit_pass_is_rounded_to_the_panels_one_decimal(
        client, monkeypatch):
    """``_limited_block``'s ``em_pass_at`` numbers are whatever the step
    response's own arithmetic produced — never guaranteed to land on a
    tenth — and the pass solved AT them must be rounded before it is made,
    not only when it is later displayed."""
    from motor_ai_sim.routes import coupled as cp

    seen = {"coil_in": [], "magnet_in": []}

    def _em(body, *, coil_temp_c, magnet_temp_c, **_k):
        seen["coil_in"].append(coil_temp_c)
        seen["magnet_in"].append(magnet_temp_c)
        return {"summary": {"P_loss_total_W": 700.0 + len(seen["coil_in"]),
                            "T_em_avg_Nm": 5.0, "rpm": 1000.0,
                            "coil_temp_C": float(coil_temp_c)},
                "I_phase_rms_solved_A": 20.0}

    def _th(body, cooling, *, coil_temp_c, magnet_temp_c, rpm, **_k):
        return {"ok": True,
                "components": {"winding": {"avg": 400.0, "max": 430.0},
                               "magnet": {"avg": 93.0, "max": 95.0}}}

    monkeypatch.setattr(cp, "_em_run", _em, raising=True)
    monkeypatch.setattr(cp, "_thermal_solve", _th, raising=True)
    monkeypatch.setattr(cp, "_ttl_step",
                        lambda *a, **k: dict(_ttl_block()), raising=True)
    monkeypatch.setattr(cp, "_attach_coupling", lambda em, block: False,
                        raising=True)
    monkeypatch.setattr(cp, "_remember_last", lambda out, **k: None,
                        raising=True)

    # A deliberately dirty limit — no real card is ever a round number to
    # three decimals, and a test built on 200.0 could not tell "rounded" from
    # "happened to already be clean".
    monkeypatch.setattr(
        cp, "_limited_block",
        lambda *a, **k: {
            "part": "winding", "quantity": "the winding hot spot",
            "limit_c": 200.037, "limit_source": "class", "t_cold_s": 24.0,
            "t_cold_words": "24 s", "t_rated_s": None, "t_rated_words": None,
            "temperatures_at_limit": {"winding": 172.5, "magnet": 47.0},
            "at_limit_c": {}, "em_pass_at": {"coil_c": 200.037,
                                             "magnet_c": 48.163,
                                             "coil_basis": "class",
                                             "magnet_basis": "hot spot"},
            "steady_state_would_be": {}, "steady_state_converged": False,
            "steady_state_runaway": False, "calibration_passes": 1,
            "cooling": {}, "cooling_words": "", "line": "test line",
        }, raising=True)

    c = _run(client, solve_to="limits")
    assert c["mode"] == "limited"
    # The AT-THE-LIMIT pass is the last one made.
    assert seen["coil_in"][-1] == round(200.037, 1) == 200.0
    assert seen["magnet_in"][-1] == round(48.163, 1) == 48.2
    assert c["coil_temp_c"] == 200.0
    assert c["magnet_temp_c"] == 48.2


def test_s1_verify_guess_is_rounded_to_the_panels_one_decimal(
        client, monkeypatch):
    """The continuous rating's network-fit temperature GUESS is what the S1
    verification pass is solved at, and it must be rounded before that pass
    is made — the same rule as the two tests above, for the third solve_to."""
    from motor_ai_sim.routes import coupled as cp

    seen = {"coil_in": [], "magnet_in": []}

    def _em(body, *, coil_temp_c, magnet_temp_c, inverter=None, **_k):
        seen["coil_in"].append(coil_temp_c)
        seen["magnet_in"].append(magnet_temp_c)
        i = float(body.get("I_phase_rms") or 0.0)
        return {"summary": {"P_loss_total_W": 100.0, "T_em_avg_Nm": 5.0,
                            "rpm": 1000.0, "coil_temp_C": float(coil_temp_c),
                            "I_phase_rms_A": i},
                "I_phase_rms_solved_A": i}

    def _th(body, cooling, *, coil_temp_c, magnet_temp_c, rpm, **_k):
        return {"ok": True,
               "components": {"winding": {"avg": 95.0, "max": 100.0},
                              "magnet": {"avg": 145.0, "max": 148.0}}}

    monkeypatch.setattr(cp, "_em_run", _em, raising=True)
    monkeypatch.setattr(cp, "_thermal_solve", _th, raising=True)
    monkeypatch.setattr(
        cp, "_continuous_rating_for_loop",
        lambda *a, **k: {
            "ok": True, "feasible": True, "trustworthy": True, "s": 0.5,
            "I_cont_A_rms": 15.156, "limiting_part": "magnet",
            # Deliberately dirty — a network fit is never exactly a tenth.
            "temperatures_c": {"winding": 103.047, "magnet": 149.93},
            "limits_c": {"winding": 200.0, "magnet": 150.0},
            "power": {}, "reference": {}, "cooling_label": "",
            "headline": "",
        }, raising=True)

    c = _run(client, solve_to="continuous")
    cr = c["continuous_rating"]
    assert cr.get("record_is_s1") is True
    # The verification pass's own coil/magnet input must be the ROUNDED
    # guess (103.0 / 149.9), never the network fit's raw 103.047 / 149.93.
    verify_coil = seen["coil_in"][2:]
    verify_magnet = seen["magnet_in"][2:]
    assert len(verify_coil) >= 1
    assert verify_coil[0] == round(103.047, 1) == 103.0
    assert verify_magnet[0] == round(149.93, 1) == 149.9
    assert c["coil_temp_c"] == 103.0


def test_only_the_first_pass_may_ask_the_ledger(client, monkeypatch):
    """``_em_run``'s ``ledger`` kwarg — the reverse-reuse door — is open on
    ``it == 1`` only, and shut again by this run's own ``fresh`` (the
    coupled panel's own "Recompute")."""
    from motor_ai_sim.routes import coupled as cp

    seen_ledger = []

    def _em(body, *, coil_temp_c, magnet_temp_c, inverter=None,
            ledger=False, **_k):
        seen_ledger.append(bool(ledger))
        return {"summary": {"P_loss_total_W": 700.0 + len(seen_ledger),
                            "T_em_avg_Nm": 5.0, "rpm": 1000.0,
                            "coil_temp_C": float(coil_temp_c)},
                "I_phase_rms_solved_A": 20.0}

    def _th(body, cooling, *, coil_temp_c, magnet_temp_c, rpm, **_k):
        return {"ok": True,
                "components": {"winding": {"avg": 121.0, "max": 122.0},
                               "magnet": {"avg": 91.0, "max": 92.0}}}

    monkeypatch.setattr(cp, "_em_run", _em, raising=True)
    monkeypatch.setattr(cp, "_thermal_solve", _th, raising=True)
    monkeypatch.setattr(cp, "_ttl_step", lambda *a, **k: None, raising=True)
    monkeypatch.setattr(cp, "_attach_coupling", lambda em, block: False,
                        raising=True)
    monkeypatch.setattr(cp, "_remember_last", lambda out, **k: None,
                        raising=True)

    seen_ledger.clear()
    _run(client, solve_to="steady", tol_k=0.0001, max_iter=3)
    assert len(seen_ledger) >= 2
    assert seen_ledger[0] is True, "the first pass must offer the ledger a look"
    assert all(x is False for x in seen_ledger[1:]), (
        "no later iteration may consult the ledger")

    seen_ledger.clear()
    _run(client, solve_to="steady", tol_k=0.0001, max_iter=3, fresh=True)
    assert seen_ledger[0] is False, (
        "this run's own Recompute must shut the door even on pass 1")


# ---------------------------------------------------------------------------
# THE REAL THING — light physics (the 30 mm fixture, ~4 frames), no mocks:
# the ledger key the loop's final pass writes under really is the key a
# plain Run at the panel's auto-set values would look up.
# ---------------------------------------------------------------------------

def _panel_probe(client, coupling, *, current=None):
    """Build the query a plain Simulation-tab Run at the auto-set panel
    values would send, and ask the ledger (``ledger_probe=True``, no solve)
    whether it already has the answer."""
    coil_panel = round(float(coupling["coil_temp_c"]) * 10) / 10
    mag = coupling.get("magnet_temp_c")
    mag_panel = None if mag is None else round(float(mag) * 10) / 10
    params = {
        "n_steps_per_period": EM_BODY["n_steps_per_period"],
        "n_periods": EM_BODY["n_periods"], "gamma_deg": EM_BODY["gamma_deg"],
        "I_phase_rms": (current if current is not None
                        else EM_BODY["I_phase_rms"]),
        "coil_temp_c": coil_panel,
        "mesh_size_mm": EM_BODY["mesh_size_mm"],
        "min_size_mm": EM_BODY["min_size_mm"],
        "outer_air_factor": EM_BODY["outer_air_factor"],
        "n_sectors": EM_BODY["n_sectors"], "gap_layers": EM_BODY["gap_layers"],
        "stator_fillet_mm": EM_BODY["stator_fillet_mm"],
        "structured_gap": EM_BODY["structured_gap"],
        "iron_template": EM_BODY["iron_template"],
        "geo_mesh": EM_BODY["geo_mesh"], "element_order": EM_BODY["element_order"],
        "drive": EM_BODY["drive"], "eddy": EM_BODY["eddy"],
        "rotor_eddy": EM_BODY["rotor_eddy"], "demag": EM_BODY["demag"],
        "daxis_deg": EM_BODY["daxis_deg"], "geo": EM_BODY["geo"],
        "ledger_probe": True,
    }
    if mag_panel is not None:
        params["magnet_temp_c"] = mag_panel
    r = client.get("/api/simulation/physics/fem_transient", params=params)
    assert r.status_code == 200, r.text[:500]
    return r.json()


@pytest.mark.parametrize("solve_to", ["steady", "limits", "continuous"])
def test_a_run_at_the_auto_set_point_is_a_ledger_hit(client, sandbox, solve_to):
    body = {**LOOP_BODY, "solve_to": solve_to, "max_iter": 3,
           "cold_constants": False}
    r = client.post("/api/coupled/run", json=body)
    assert r.status_code == 200, r.text[:2000]
    coupling = r.json()["coupling"]

    current = None
    if solve_to == "continuous":
        cr = coupling.get("continuous_rating") or {}
        if cr.get("I_cont_A_rms") is not None:
            current = round(float(cr["I_cont_A_rms"]), 1)

    probe = _panel_probe(client, coupling, current=current)
    assert probe.get("match") is True, (solve_to, probe, coupling)


def test_a_plain_run_before_coupling_is_reused_by_the_first_pass(
        client, sandbox):
    from motor_ai_sim.routes import simulation as sim

    # Self-contained regardless of test order: the parametrized ledger-hit
    # test above shares this module's ledger directory (`sandbox` redirects
    # the OTHER stores, not `_ledger_dir` — that one lives under the
    # sandboxed `config/` for the whole session) and may already have
    # written an entry for this exact operating point.
    client.delete("/api/simulation/ledger")

    lookups = []
    orig_lookup = sim._ledger_lookup

    def _wrap(sb_key):
        out = orig_lookup(sb_key)
        lookups.append(out is not None)
        return out

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(sim, "_ledger_lookup", _wrap)
    try:
        run_params = {k: v for k, v in EM_BODY.items() if k != "cold_constants"}
        run_params["magnet_temp_c"] = 90.0     # matches LOOP_BODY below
        run_params["field_snapshot"] = True    # what the real panel sends
        r1 = client.get("/api/simulation/physics/fem_transient",
                        params=run_params)
        assert r1.status_code == 200, r1.text[:500]

        body = {**LOOP_BODY, "solve_to": "steady", "max_iter": 3,
               "cold_constants": False}
        r2 = client.post("/api/coupled/run", json=body)
        assert r2.status_code == 200, r2.text[:2000]
    finally:
        monkeypatch.undo()

    # lookups[0] is the plain Run's own ledger check (nothing stored yet — a
    # miss); lookups[1] is the coupled loop's first pass, which must find
    # exactly the run just made.
    assert len(lookups) >= 2, lookups
    assert lookups[0] is False
    assert lookups[1] is True, "the loop re-solved a point already run"
