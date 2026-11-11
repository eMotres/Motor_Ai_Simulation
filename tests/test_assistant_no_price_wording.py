"""No price wording reaches the customer assistants' prompts (owner rule: no price or quote talk with a customer during design; the order is
full simulation, agreement with the customer, costs, quote, payment, production).

The only money wording allowed is the rule that forbids it (``NO_PRICE_RULE``) and the fact that PRIVATE is a paid mode.  What is scanned: the
help assistant's customer and visitor prompts as ``build_system_prompt`` assembles them, the ticket protocol, and the specification assistant's
prompt, kickoff and server-built context.  Synthetic data only; no provider is called.
"""
from __future__ import annotations

import re

import pytest

from motor_ai_sim import support_prompt as SP
from motor_ai_sim.development import spec_assistant as A
PR = SP                  # ONE assistant (2026-11-11): the specification part lives in support_prompt too
from motor_ai_sim.routes import support

MONEY = re.compile(r"\b(prices?|pricing|priced|quotes?|quotations?|quoting|budgets?|costs?|costing|fees?|discounts?|invoices?|"
                   r"lead[- ]times?|target_price|6_3|价格|预算|报价|费用)\b", re.IGNORECASE)


def money_words(text: str) -> list[str]:
    return [m.group(0) for m in MONEY.finditer(text.replace(SP.NO_PRICE_RULE, ""))]


@pytest.fixture()
def no_overrides(monkeypatch):
    monkeypatch.setattr(support, "_load_overrides", lambda: {})
    monkeypatch.setattr(support.support_catalog, "published_motors_block", lambda *a, **k: "")


@pytest.mark.parametrize("text", [SP.USER_PROMPT, SP.TICKET_PROTOCOL, support.VISITOR_NOTE, SP.SPEC_SECTION, SP.KICKOFF, SP.DATA_RULES,
                                  SP.CREATE_SPEC_PROTOCOL, support.VISITOR_CREATE_NOTE],
                         ids=["user", "ticket", "visitor", "spec", "kickoff", "data", "create", "visitor_create"])
def test_no_money_word_in_a_customer_prompt(text):
    assert money_words(text) == []


def test_the_checker_finds_money_words():
    assert money_words("Ask for the Target price / budget and a quote.") == ["price", "budget", "quote"]
    assert money_words(SP.NO_PRICE_RULE) == []


def test_the_prompts_as_the_route_builds_them(no_overrides):
    visitor = support.build_system_prompt("anon", anon=True)
    customer = support.build_system_prompt("user", anon=False)
    for sp in (visitor, customer):
        assert SP.NO_PRICE_RULE in sp and money_words(sp) == []


def test_the_rule_is_there_under_a_custom_prompt(monkeypatch):
    monkeypatch.setattr(support, "_load_overrides", lambda: {"system_prompt": "CUSTOM"})
    monkeypatch.setattr(support.support_catalog, "published_motors_block", lambda *a, **k: "")
    assert SP.NO_PRICE_RULE in support.build_system_prompt("anon", anon=True)
    assert SP.NO_PRICE_RULE in support.build_system_prompt("user", anon=False)
    assert SP.NO_PRICE_RULE not in support.build_system_prompt("admin", anon=False)


def test_the_specification_prompt_no_longer_asks_for_money():
    low = SP.SPEC_SECTION.lower()
    assert "target budget" not in low and "prices" not in low and "commercial" not in low
    assert "PRIVATE" not in SP.SPEC_SECTION


@pytest.mark.parametrize("lang", ["en", "zh-CN"])
def test_the_specification_context_never_lists_the_money_fields(lang, monkeypatch, tmp_path):
    from motor_ai_sim.development import models
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path))
    wrapper = {"schema_version": 2, "revision": 1, "draft": {"schema_version": 2, "fields": {
        "6_3": {"value": {"amount": 123456, "currency": "EUR"}, "status": "filled"}},
        "production": {"target_price": {"value": 654321, "status": "filled"}}, "mission_profile": [], "provenance": {}}}
    ctx = A.build_context({"title": "Alpha"}, wrapper, [], lang=lang, can_apply=True)
    assert money_words(ctx) == [] and "123456" not in ctx and "654321" not in ctx and "EUR" not in ctx
    assert "6_1" in ctx.split("CUSTOMER DECISIONS")[1]                          # the other customer decisions are still asked
    assert models.schema(2)                                                     # the form's schema itself is untouched
