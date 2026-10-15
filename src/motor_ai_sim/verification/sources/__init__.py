"""The default sources, registered in this order (order matters only for the display; sources are independent)."""
from motor_ai_sim.verification.sources.base import Context, VerificationSource, register, registered, run_all, unregister
from motor_ai_sim.verification.sources.applicant_source import ApplicantUrlsSource
from motor_ai_sim.verification.sources.companies_house_source import CompaniesHouseSource
from motor_ai_sim.verification.sources.email_source import EmailSource
from motor_ai_sim.verification.sources.gleif_source import GleifSource
from motor_ai_sim.verification.sources.stubs import ChinaRegistrySource, LlmResearchSource
from motor_ai_sim.verification.sources.vies_source import ViesSource
from motor_ai_sim.verification.sources.website_source import WebsiteSource

__all__ = ["Context", "VerificationSource", "LlmResearchSource", "register", "registered", "run_all", "unregister", "register_defaults"]


def register_defaults() -> None:
    for s in (EmailSource(), GleifSource(), CompaniesHouseSource(), ViesSource(), ChinaRegistrySource(), WebsiteSource(),
              ApplicantUrlsSource()):
        register(s, replace=False)


register_defaults()
