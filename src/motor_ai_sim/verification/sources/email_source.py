"""E-mail and domain checks: is the address verified (the account's own, or one confirmed with a one-time code), is it a free or
disposable mailbox, does the domain exist and take mail, and is it the domain of the official website."""
from __future__ import annotations

from motor_ai_sim.verification import evidence as EV
from motor_ai_sim.verification import domain_link as DL
from motor_ai_sim.verification import net as NET
from motor_ai_sim.verification import textutil as T
from motor_ai_sim.verification.evidence import Evidence
from motor_ai_sim.verification.sources.base import Context, VerificationSource


class EmailSource(VerificationSource):
    name = "email"

    def run(self, case: dict, ctx: Context) -> list[Evidence]:
        app = case.get("application") or {}
        site = T.normalize_website(app.get("website") or "")
        out: list[Evidence] = []
        seen: set = set()
        for row in case.get("emails") or []:
            addr = str(row.get("address") or "").strip().lower()
            if not addr or addr in seen:
                continue
            seen.add(addr)
            dom = T.host_of(addr.rpartition("@")[2])
            out.append(Evidence("email", EV.CHECK_EMAIL_VERIFIED, EV.EMAIL, EV.PASS if row.get("verified") else EV.FAIL, EV.NONE, addr,
                                "confirmed by %s" % row.get("method", "?") if row.get("verified") else "not confirmed yet",
                                detail={"method": row.get("method", ""), "verified": bool(row.get("verified"))}))
            free, disposable = T.is_free_or_disposable(addr)
            out.append(Evidence("email", EV.CHECK_EMAIL_CORPORATE, EV.EMAIL, EV.FAIL if (free or disposable) else EV.PASS, EV.NONE, addr,
                                "disposable mailbox provider" if disposable else "free mailbox provider" if free else "not a free or disposable provider",
                                detail={"free": free, "disposable": disposable, "domain": dom}))
            if free or disposable:
                continue
            out.append(self._domain(addr, dom, ctx))
            out.append(self._mx(addr, dom, ctx))
            if site:
                linked, how, proof = DL.email_domain_linked(dom, T.host_of(site), context=ctx, website_url=site)
                proof_source = next((p.get("url") or p.get("to") or p.get("source_url") or
                                     ((p.get("source_urls") or [""])[0]) or p.get("from") or "" for p in proof.get("proofs", [])
                                     if p.get("url") or p.get("to") or p.get("source_url") or p.get("source_urls") or p.get("from")), "")
                out.append(Evidence("domain", EV.CHECK_DOMAIN_MATCH, EV.DOMAIN, EV.PASS if linked else EV.FAIL, EV.NONE, addr,
                                    "e-mail domain %s is linked to the official website domain %s (%s)" % (dom, T.host_of(site), how)
                                    if linked else "e-mail domain %s is not linked to the official website domain %s (%s)" % (dom, T.host_of(site), how),
                                    source_url=proof_source,
                                    detail={"email_domain": dom, "website_domain": T.host_of(site), "how": how, "evidence": proof}))
        return out

    @staticmethod
    def _domain(addr: str, dom: str, ctx: Context) -> Evidence:
        from motor_ai_sim import email_domain as ED
        c = ED.check(addr)
        res = EV.UNKNOWN if c.reason in ("unknown", "invalid") else EV.PASS if c.ok else EV.FAIL
        return Evidence("email", EV.CHECK_EMAIL_DOMAIN, EV.EMAIL, res, EV.NONE, addr, "domain %s: %s" % (dom, c.reason),
                        detail={"reason": c.reason, "domain": dom})

    @staticmethod
    def _mx(addr: str, dom: str, ctx: Context) -> Evidence:
        mx = NET.lookup_mx(dom)
        if mx is None:
            return Evidence("email", EV.CHECK_EMAIL_MX, EV.EMAIL, EV.UNKNOWN, EV.NONE, addr, "MX lookup of %s gave no answer" % dom,
                            detail={"domain": dom})
        hosts = [h for _p, h in mx]
        null_mx = bool(mx) and all(h in ("", ".") for h in hosts)
        ok = bool(mx) and not null_mx
        return Evidence("email", EV.CHECK_EMAIL_MX, EV.EMAIL, EV.PASS if ok else EV.FAIL, EV.NONE, addr,
                        "MX: %s" % (", ".join(hosts[:3]) if ok else "none"), detail={"domain": dom, "mx": hosts[:5]}, raw={"mx": mx[:10]})
