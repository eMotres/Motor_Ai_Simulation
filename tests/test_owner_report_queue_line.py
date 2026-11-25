"""The owner-queue line of the daily report: with it, without it, and the unavailable case (never breaks the report)."""
from __future__ import annotations

import calendar
import time

import pytest

from motor_ai_sim import owner_queue as OQ
from motor_ai_sim.owner_report import __main__ as M
from motor_ai_sim.owner_report import build as B
from motor_ai_sim.owner_report import run as R
from tests.test_owner_report import DATE, empty_sources  # noqa: F401

LINE = "Decisions: 4 open, 9 messages, oldest 2 d 5 h"


def test_line_sits_right_under_the_summary_in_text_and_html():
    rep = B.build(DATE, empty_sources(), base_url="https://app.example.test", queue_line=LINE)
    lines = rep.text.split("\n")
    assert lines[lines.index(rep.summary) + 1] == LINE
    assert lines.index(rep.summary) + 2 < len(lines) and lines[lines.index(rep.summary) + 2] == ""
    assert LINE in rep.html and rep.html.index(rep.summary) < rep.html.index(LINE) < rep.html.index("1. New users")
    assert rep.html.count(LINE) == 1 and rep.text.count(LINE) == 1


def test_without_the_line_nothing_changes():
    a = B.build(DATE, empty_sources(), base_url="https://app.example.test")
    b = B.build(DATE, empty_sources(), base_url="https://app.example.test", queue_line=None)
    assert a.text == b.text and a.html == b.html and "Decisions" not in a.text
    assert [s.id for s in a.sections] == list(B.SECTION_ORDER)


def test_line_format():
    now = calendar.timegm(time.strptime("2026-11-25T12:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))
    assert M.format_queue_line({"open_blocking": 4, "open_messages": 9, "oldest_open_at": "2026-11-23T07:00:00Z"}, now) == LINE
    assert M.format_queue_line({"open_blocking": 0, "open_messages": 0, "oldest_open_at": None}, now) == "Decisions: 0 open, 0 messages"
    assert M.format_queue_line({"open_blocking": 1, "open_messages": 0, "oldest_open_at": "2026-11-25T09:30:00Z"}, now).endswith("oldest 2 h")


def _fake_run(**kw):
    return {"status": "dry_run", "date": DATE, "report": R.B.build(DATE, empty_sources(), base_url="https://app.example.test")}


def test_main_adds_the_line_and_restores_build(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("MOTRES_OWNER_QUEUE_DIR", str(tmp_path / "oq"))
    OQ.create_item("t", {"kind": "question", "asked_by": "s", "question": "q?"})
    monkeypatch.setattr(R, "run_daily", _fake_run)
    orig = R.B.build
    assert M.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Decisions: 1 open, 0 messages, oldest 0 h" in out
    assert R.B.build is orig


def test_unavailable_never_breaks_the_report(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("MOTRES_OWNER_QUEUE_DIR", str(tmp_path / "oq"))
    (tmp_path / "oq").mkdir()
    (tmp_path / "oq" / "items.jsonl").write_text("garbage\n")             # a damaged store
    monkeypatch.setattr(R, "run_daily", _fake_run)
    assert M.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Decisions: unavailable" in out and "AeroStator owner report" in out
    monkeypatch.setattr(OQ, "counts", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert M.queue_line(0.0) == "Decisions: unavailable"


def test_urgent_items_are_named_capped_at_three_with_a_remainder():
    now = calendar.timegm(time.strptime("2026-11-25T12:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))
    c = {"open_blocking": 4, "open_urgent": 2, "open_messages": 9, "oldest_open_at": "2026-11-23T07:00:00Z"}
    u = [{"project_id": "Pump project", "question": "Which winding?"}, {"project_id": None, "question": "x" * 80}]
    line = M.format_queue_line(c, now, u)
    assert line == "Decisions: 2 urgent (Pump project: Which winding?; " + "x" * 59 + "…), 4 open, 9 messages, oldest 2 d 5 h"
    many = [{"project_id": f"p{i}", "question": f"q{i}"} for i in range(5)]
    assert M.format_queue_line({**c, "open_urgent": 5}, now, many).startswith("Decisions: 5 urgent (p0: q0; p1: q1; p2: q2; +2), 4 open")
    assert M.format_queue_line({**c, "open_urgent": 0}, now, None) == LINE                    # no urgent: the old line, unchanged


def test_main_line_names_the_urgent_item(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("MOTRES_OWNER_QUEUE_DIR", str(tmp_path / "oq"))
    OQ.create_item("t", {"kind": "question", "asked_by": "s", "question": "Which?", "project_id": "p1"})
    OQ.set_priority("p1", "urgent", "o")
    assert M.queue_line(time.time()).startswith("Decisions: 1 urgent (p1: Which?), 1 open, 0 messages")
