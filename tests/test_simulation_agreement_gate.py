"""The Platform Agreement at the START of a simulation (owner 2026-11-01: "the agreement is needed only by users who want to simulate a
motor"): ``platform_agreement.simulation_gate_detail`` behind every route through ``jobs.queued``, the MCP ``simulate`` and ``start_design``.

* env ``PLATFORM_AGREEMENT_AT_SIMULATION`` (default OFF): a deploy never activates the gate;
* ON: HTTP 428 ``{"code": "agreement_required", "version": <public agreement version>}`` for a non-staff, non-MOTRES account without a
  current acceptance; a company acceptance OR the account's own agreement acceptance (capacity, country, mode) satisfies it, a Terms-only
  sign-up line does not; reading stored results, the catalogue and the legal routes are never gated;
* ``TERMS_REQUIRED_FROM`` is gone (superseded).

Everything runs on tmp_path; no mail, no solver.
"""
from __future__ import annotations

import types

import pytest
from fastapi import HTTPException

from motor_ai_sim import legal_texts as LT
from motor_ai_sim import platform_agreement as PA
from tests.test_legal_texts import (ADMIN, AGREE, ANNA, BOB, ENG, LONE, OWN, TICKED, _agreement_record, _company, _terms_line, client, env,  # noqa: F401
                                    lines, signup)

FLAG = "PLATFORM_AGREEMENT_AT_SIMULATION"


def _call(env, email, role="user", kind="test.agreement_gate"):
    from motor_ai_sim import jobs as J
    from motor_ai_sim import workspace as WSP

    @J.queued(kind)
    def start():
        return {"ran": True}

    with WSP.use_caller({"id": email, "role": role}):
        return start()


def _refused(env, email, role="user"):
    with pytest.raises(HTTPException) as e:
        _call(env, email, role)
    assert e.value.status_code == 428
    return e.value.detail


def test_the_gate_is_off_by_default_and_a_deploy_does_not_activate_it(env):
    env["mp"].delenv(FLAG, raising=False)
    assert PA.agreement_at_simulation() is False and PA.simulation_gate_detail(ANNA) is None
    assert _call(env, ANNA)["ran"] is True
    for off in ("", "0", "false", "no", "off"):
        env["mp"].setenv(FLAG, off)
        assert PA.agreement_at_simulation() is False and _call(env, BOB)["ran"] is True, off
    for on in ("1", "true", "YES"):
        env["mp"].setenv(FLAG, on)
        assert PA.agreement_at_simulation() is True, on


def test_the_old_deadline_is_gone(env):
    env["mp"].setenv("TERMS_REQUIRED_FROM", "2020-01-01")                       # superseded: it does nothing any more
    assert _call(env, ANNA)["ran"] is True
    assert not hasattr(LT, "required_from") and not hasattr(LT, "gate_detail") and not hasattr(LT, "require_current")
    assert "required_from" not in client.get("/api/legal/status", headers=env["h"](ANNA)).json()


def test_on_a_compute_job_start_needs_the_agreement_428_with_the_public_version(env):
    env["mp"].setenv(FLAG, "1")
    d = _refused(env, ANNA)
    assert d["code"] == "agreement_required" and d["version"] == LT.AGREEMENT_PUBLIC_VERSION and "Platform Agreement" in d["message"]
    assert d["at"] == "simulation"                                              # the web opens the short account dialog for this one
    # a Terms-only line (the sign-up box) is not the agreement
    _terms_line(env, ANNA, LT.TERMS_VERSION)
    assert _refused(env, ANNA)["code"] == "agreement_required"
    # an older version of the public agreement does not count for the current one
    _terms_line(env, BOB, "v0.3", capacity="business", country="DE", mode="FREE")
    assert _refused(env, BOB)["code"] == "agreement_required"


def test_staff_motres_anonymous_and_the_global_switch_are_never_gated(env):
    env["mp"].setenv(FLAG, "1")
    for who, role in ((ENG, "engineer"), (ADMIN, "admin"), (OWN, "user")):
        assert _call(env, who, role)["ran"] is True, who
    assert _call(env, "process")["ran"] is True                                  # no e-mail: an anonymous / process identity
    assert PA.simulation_gate_detail(ENG) is None and PA.simulation_gate_detail("") is None   # role read from the registry
    env["mp"].setenv("TERMS_ACCEPTANCE_DISABLED", "1")
    assert _call(env, BOB)["ran"] is True


def test_the_accounts_own_agreement_acceptance_opens_the_gate_and_the_same_call_repeats(env):
    env["mp"].setenv(FLAG, "1")
    h = env["h"](ANNA)
    assert _refused(env, ANNA)["code"] == "agreement_required"
    r = client.post("/api/legal/accept-agreement", json=AGREE, headers=h)
    assert r.status_code == 200, r.text
    assert _call(env, ANNA)["ran"] is True                                       # the web repeats the refused call after this
    assert _refused(env, BOB)["code"] == "agreement_required"                    # another account is still asked


def test_a_private_request_is_a_current_acceptance_the_account_may_simulate(env):
    env["mp"].setenv(FLAG, "1")
    (env["tmp"] / "legal").mkdir(exist_ok=True)
    r = client.post("/api/legal/accept-agreement", json={**AGREE, "mode": "PRIVATE"}, headers=env["h"](ANNA))
    assert r.status_code == 200 and r.json()["hold"] is True
    assert _call(env, ANNA)["ran"] is True
    assert PA.training_allowed(ANNA) is False                                    # ... with its data held out of training


def test_the_first_release_line_with_capacity_and_country_counts_as_a_free_acceptance(env):
    env["mp"].setenv(FLAG, "1")
    _terms_line(env, ANNA, LT.TERMS_VERSION, capacity="sole_trader", country="DE")       # the #525 shape: no kind, no mode
    assert _call(env, ANNA)["ran"] is True
    _terms_line(env, BOB, LT.TERMS_VERSION, capacity="sole_trader")                      # no country: not complete
    assert _refused(env, BOB)["code"] == "agreement_required"


def test_a_company_acceptance_satisfies_the_gate_for_its_confirmed_users_and_so_does_an_account_record(env):
    env["mp"].setenv(FLAG, "1")
    comp = _company(env, "Acme Drones Ltd", "HRB 12345", [ANNA, BOB])
    assert _refused(env, ANNA)["code"] == "agreement_required"
    _agreement_record(env, "company:" + comp["id"], "FREE", ANNA)
    assert _call(env, ANNA)["ran"] is True and _call(env, BOB)["ran"] is True           # one acceptance covers all its users
    assert _refused(env, LONE)["code"] == "agreement_required"                            # not in the company
    _agreement_record(env, "user:" + LONE, "PRIVATE", LONE)                               # a click-through record of the account itself
    assert _call(env, LONE)["ran"] is True


def test_nested_runs_are_not_gated_twice_and_reads_stay_open(env):
    from motor_ai_sim import jobs as J
    from motor_ai_sim import workspace as WSP
    env["mp"].setenv(FLAG, "1")

    @J.queued("test.agreement_gate.inner")
    def inner():
        return {"inner": True}

    @J.queued("test.agreement_gate.outer")
    def outer():
        return inner()

    with WSP.use_caller({"id": ENG, "role": "engineer"}):                        # staff passes the outer gate; the nested call is not gated again
        assert outer()["inner"] is True
    # reading stored results, the catalogue, the legal texts, the status and the account record never go through the queue
    h = env["h"](BOB)
    assert client.get("/api/legal/status", headers=h).status_code == 200
    assert client.get("/api/legal/mine", headers=h).status_code == 200
    assert client.get("/api/legal/terms").status_code == 200
    assert client.get("/api/me", headers=h).status_code == 200


def test_the_gate_applies_to_the_real_workspace_routes(env):
    env["mp"].setenv("WORKSPACES_ROOT", str(env["tmp"] / "workspaces"))         # the server mode: every request resolves its caller
    env["mp"].setenv(FLAG, "1")
    r = client.post("/api/simulation/cogging/run", json={}, headers=env["h"](BOB))
    assert r.status_code == 428
    assert r.json()["detail"]["code"] == "agreement_required" and r.json()["detail"]["version"] == LT.AGREEMENT_PUBLIC_VERSION
    assert r.json()["detail"]["at"] == "simulation"
    env["mp"].setenv(FLAG, "0")
    assert client.post("/api/simulation/cogging/run", json={}, headers=env["h"](BOB)).status_code != 428


def test_mcp_simulate_and_start_design_are_refused_with_an_agreement_required_tool_error(env):
    from motor_ai_sim import agent_designs as AD
    env["mp"].setenv(FLAG, "1")

    def who(email):
        return types.SimpleNamespace(email=email, client_name="t", credential_id="c", kind="key")

    for call in (lambda p: AD.simulate(p, "d-x", "em"), lambda p: AD.start_design(p, {})):
        with pytest.raises(AD.DesignError) as e:
            call(who(BOB))
        assert str(e.value).startswith("agreement_required:") and LT.AGREEMENT_PUBLIC_VERSION in str(e.value)
    # staff are not gated: simulate gets past the gate (and fails later on the unknown draft), start_design asks for the requirements
    with pytest.raises(Exception) as e2:
        AD.simulate(who(ENG), "d-x", "em")
    assert not str(e2.value).startswith("agreement_required")
    assert AD.start_design(who(ENG), {})["status"] == "needs_input"
    # after the account's acceptance the gate lets BOB through to the same next step
    assert client.post("/api/legal/accept-agreement", json=AGREE, headers=env["h"](BOB)).status_code == 200
    assert AD.start_design(who(BOB), {})["status"] == "needs_input"
    # flag off: start_design keeps its former behaviour (the company click-through, which is not installed here)
    env["mp"].setenv(FLAG, "0")
    assert AD.start_design(who(ANNA), {})["status"] == "needs_input"
