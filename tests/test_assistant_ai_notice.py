"""All three assistants say they are an AI before the first interaction (owner decision D3, 2026-10-31).

One shared string (``aiNotice`` in ``common.json``), shown by the help widget, the specification assistant panel and the
design assistant.  The zh-CN text has no Latin letters (the zh UI rule).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_WEB = Path(__file__).resolve().parents[1] / "web" / "src"


def _common(lang: str) -> dict:
    return json.loads((_WEB / "locales" / lang / "common.json").read_text(encoding="utf-8"))


def test_the_notice_exists_in_both_languages_with_the_agreed_wording():
    assert _common("en")["aiNotice"] == "AI assistant — answers can be wrong."
    zh = _common("zh-CN")["aiNotice"]
    assert zh == "人工智能助手——回答可能有误。"
    assert not re.search(r"[A-Za-z]", zh), "zh UI: no Latin letters"


def test_every_assistant_surface_renders_the_notice():
    for rel in ("components/support/SupportWidget.tsx",
                "modules/development/components/SpecAssistantPanel.tsx",
                "modules/designer/DesignerTab.tsx"):
        src = (_WEB / rel).read_text(encoding="utf-8")
        assert "aiNotice" in src and 'data-testid="ai-notice"' in src, rel
