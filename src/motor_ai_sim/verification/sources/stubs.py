"""Phase-2 hooks.  Nothing here changes a decision today.

* :class:`ChinaRegistrySource` - China has no free public registry API.  A company in CN that no registry-grade source confirmed
  goes to MANUAL_REVIEW and the applicant is asked for a business-licence document (the upload on the reply page).  Active now.
* :class:`LlmResearchSource` - phase 2: an LLM with search grounding that GATHERS cited evidence (a URL, a quotation, why it
  matters) and never decides.  It is an advisory source (``advisory = True``): the runner caps its strength at "supporting" and it
  can never confirm a company.  Not registered by default; ``run`` returns nothing until a backend is plugged in.
"""
from __future__ import annotations

from motor_ai_sim.verification import evidence as EV
from motor_ai_sim.verification.evidence import Evidence
from motor_ai_sim.verification.sources.base import Context, VerificationSource


class ChinaRegistrySource(VerificationSource):
    name = "china_registry"

    def applies(self, country: str, data: dict) -> bool:
        return country == "CN"

    def run(self, case: dict, ctx: Context) -> list[Evidence]:
        app = case.get("application") or {}
        return [Evidence("china_registry", EV.CHECK_REGISTRY_COMPANY, EV.INFO, EV.MANUAL, EV.NONE, app.get("reg_no") or app.get("legal_name", ""),
                         "no free public registry API for mainland China: manual review, business-licence document requested",
                         detail={"request": "business_licence"})]


class LlmResearchSource(VerificationSource):
    """Contract for the phase-2 research step.  ``gather(case)`` returns citations - ``{"url", "quote", "claim", "relevance"}`` -
    and ``run`` turns each into a SUPPORTING ``Evidence`` that carries the citation in ``detail``.  It never returns ``PASS`` for a
    company and never a status."""
    name = "llm_research"
    advisory = True

    def applies(self, country: str, data: dict) -> bool:
        return False                                   # switched on when a backend exists (phase 2)

    def gather(self, case: dict, ctx: Context) -> list[dict]:
        return []

    def run(self, case: dict, ctx: Context) -> list[Evidence]:
        out = []
        for c in self.gather(case, ctx):
            if not c.get("url") or not c.get("quote"):
                continue                                # a claim without a source URL and a quotation is not evidence
            out.append(Evidence(self.name, "research.citation", EV.INFO, EV.PASS, EV.SUPPORTING, c["url"], str(c.get("claim", ""))[:300],
                                source_url=c["url"], detail={"quote": str(c["quote"])[:500], "relevance": c.get("relevance", "")}))
        return out
