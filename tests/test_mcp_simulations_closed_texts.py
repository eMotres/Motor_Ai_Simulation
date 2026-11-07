"""The MCP texts follow ``MOTRES_SOLVE_ADMIN_ONLY`` (owner 2026-11-04): while simulations are closed, every text an agent reads
says that MOTRES engineers run them and that ``simulate`` answers ``simulations_closed``; with the setting off the previous
texts are back.  No price word in either state.  No network, no solver."""
from __future__ import annotations

import asyncio
import json
import re

import pytest

from motor_ai_sim import agent_keys as K
from motor_ai_sim import mcp_app
from motor_ai_sim import mcp_discovery as D
from motor_ai_sim import mcp_tools as T

ENV = "MOTRES_SOLVE_ADMIN_ONLY"
MONEY = re.compile(r"\b(price[sd]?|pricing|quot(?:e|es|ed|ation|ations)|budgets?|lead[- ]?times?|costs?(?! torque)|"
                   r"costing|invoices?|payments?|currency|discounts?|eur|usd)\b|[€$]", re.IGNORECASE)


def _tools():
    return {t.name: t.description for t in asyncio.run(mcp_app.build_server().list_tools())}


def _everything() -> str:
    """Every agent-facing text this change touches, as one string."""
    llms = D.llms_txt([])
    return "\n".join([T.guide(), mcp_app.instructions(), llms,
                      json.dumps(D.capabilities(), ensure_ascii=False),
                      json.dumps(D.calculation_types(), ensure_ascii=False),
                      json.dumps(D.input_requirements("em_fem_transient"), ensure_ascii=False),
                      json.dumps(D.describe_service(), ensure_ascii=False),
                      json.dumps(D.how_to_authenticate(), ensure_ascii=False),
                      json.dumps(K.scope_descriptions(), ensure_ascii=False),
                      json.dumps(_tools(), ensure_ascii=False)])


def test_closed_texts_say_motres_runs_the_simulations(monkeypatch):
    monkeypatch.setenv(ENV, "1")
    assert T.simulations_closed()
    tools = _tools()
    assert "simulate" in tools                                    # stays listed
    assert "simulations_closed" in tools["simulate"] and "MOTRES engineers" in tools["simulate"]
    assert "Queue a FEM simulation" not in tools["simulate"]
    for text in (T.guide(), mcp_app.instructions()):
        assert "simulations_closed" in text and "MOTRES engineers" in text
        assert T.NO_PRICE_RULE in text
    # only simulate is closed: drafts, Configure links and the read tools stay in the texts
    assert "-> job_id" not in T.guide() and "CLOSED for now" in T.guide()
    for kept in ("start_design(requirements", "open_in_configure(design_id)", "get_machine_performance"):
        assert kept in T.guide(), kept
    assert "start_design when nothing fits" in mcp_app.instructions()
    assert "start_design" in tools and "Start a DRAFT motor design" in tools["start_design"]
    assert "next: open_in_configure" in tools["start_design"] and "next: simulate" not in tools["start_design"]
    caps = {t["name"]: t for t in D.capabilities()["tools"]}
    assert caps["simulate"]["purpose"].startswith("CLOSED for now")
    scopes = {s["scope"]: s for s in D.capabilities()["scopes"]}
    assert "MOTRES engineers" in scopes["simulate"]["description"]
    assert "MOTRES engineers" in K.scope_descriptions()["simulate"]
    types = {t["id"]: t for t in D.calculation_types()["calculation_types"]}
    for cid in ("em_fem_transient", "thermal", "coupled_em_thermal"):
        assert types[cid]["status"] == "closed_for_clients"
        assert D.input_requirements(cid)["status"] == "closed_for_clients"
    assert "status" not in types["machine_fit_check"]
    assert "job queue" not in D.describe_service()["summary"]
    assert "simulations_closed" in D.llms_txt([])


def test_open_texts_are_the_previous_ones(monkeypatch):
    monkeypatch.setenv(ENV, "0")
    assert not T.simulations_closed()
    assert "Queue a FEM simulation of a draft" in _tools()["simulate"]
    assert "simulate(design_id, what=em|thermal|coupled, steps?) -> job_id" in T.guide()
    assert "next: simulate(design_id, 'em')" in _tools()["start_design"]
    assert "start_design when nothing fits" in mcp_app.instructions()
    caps = {t["name"]: t for t in D.capabilities()["tools"]}
    assert caps["simulate"]["purpose"] == D.TOOL_SUMMARIES["simulate"]
    assert K.scope_descriptions() == K.SCOPE_DESCRIPTIONS
    types = {t["id"]: t for t in D.calculation_types()["calculation_types"]}
    assert all("status" not in types[c] for c in ("em_fem_transient", "thermal", "coupled_em_thermal"))
    assert "simulations_closed" not in _everything()
    # the module attributes older callers import read the setting too
    assert T.GUIDE == T.guide() and mcp_app.INSTRUCTIONS == mcp_app.instructions()


@pytest.mark.parametrize("value", ["1", "0"])
def test_no_price_word_in_either_state(monkeypatch, value):
    monkeypatch.setenv(ENV, value)
    text = _everything().replace(T.NO_PRICE_RULE, "").replace("^d-[0-9a-f]{12}$", "")   # a regex, not a currency sign
    hit = MONEY.search(text)
    assert not hit, (value, text[max(0, hit.start() - 80):hit.end() + 80])


def test_the_discovery_surfaces_name_no_user_job_queue_while_closed(monkeypatch):
    monkeypatch.setenv(ENV, "1")
    for text in (T.guide(), mcp_app.instructions(), D.describe_service()["summary"]):
        assert "user's own queue" not in text and "own job queue" not in text
