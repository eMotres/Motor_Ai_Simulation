"""Evidence-backed checks that two company web and e-mail domains belong together."""
from __future__ import annotations

import re
import urllib.parse

from motor_ai_sim.verification import net as NET
from motor_ai_sim.verification import textutil as T


def _domain(value: str) -> str:
    return T.host_of(str(value or "").strip().lower())


def _domain_relation(a: str, b: str) -> bool:
    return bool(a and b and (a == b or a.endswith("." + b) or T.registrable_domain(a) == T.registrable_domain(b)))


def _mx_hosts(domain: str) -> set[str] | None:
    records = NET.lookup_mx(domain)
    if records is None:
        return None
    hosts = set()
    for _pref, host in records:
        h = str(host or "").strip().lower().rstrip(".")
        if h.startswith("*."):
            h = "*." + h[2:].encode("idna").decode("ascii")
        else:
            try:
                h = h.encode("idna").decode("ascii")
            except UnicodeError:
                continue
        if h and h != ".":
            hosts.add(h)
    return hosts


def _visible_domain_mention(domain: str, pages: list[dict]) -> dict | None:
    # Match a full domain with label boundaries; do not accept text embedded in a lookalike hostname.
    escaped = re.escape(domain)
    pattern = re.compile(r"(?<![a-z0-9.-])" + escaped + r"(?![a-z0-9-]|\.[a-z0-9-])", re.I)
    for page in pages:
        if pattern.search(page.get("text") or ""):
            return {"url": page.get("url", ""), "matched": domain, "type": "visible_domain_text"}
        for addr in page.get("mailto") or []:
            if _domain(addr.rpartition("@")[2]) == domain:
                return {"url": page.get("url", ""), "matched": addr, "type": "mailto"}
    return None


def _norm_url(value: str) -> str:
    try:
        u = urllib.parse.urlsplit(value)
        host = _domain(u.hostname or "")
        if not host or u.scheme not in ("http", "https"):
            return ""
        port = u.port
        netloc = host + ((":" + str(port)) if port and not (u.scheme == "https" and port == 443) and not (u.scheme == "http" and port == 80) else "")
        path = u.path or "/"
        return urllib.parse.urlunsplit((u.scheme.lower(), netloc, path, u.query, ""))
    except (ValueError, TypeError):
        return ""


def _official_host(host: str, official: str) -> bool:
    return _domain_relation(_domain(host), _domain(official))


def _same_site_proof(email_domain: str, website_domain: str, site_material: dict, ctx) -> dict | None:
    site_pages = site_material.get("pages") or []
    official_canonicals = []
    for page in site_pages:
        canon = page.get("canonical_url") or ""
        u = urllib.parse.urlsplit(canon)
        if canon and _official_host(u.hostname or "", website_domain):
            official_canonicals.append(_norm_url(canon))

    candidate_url = "https://" + email_domain + "/"
    robots: dict = {}
    p = NET.fetch_page(candidate_url, ctx.http, robots)
    if p.ok:
        chain = p.redirects or [candidate_url, p.url]
        destinations = [urllib.parse.urlsplit(x).hostname or "" for x in chain[1:]]
        if any(_official_host(host, website_domain) for host in destinations):
            return {"type": "http_redirect", "from": candidate_url, "chain": chain, "to": p.url}
        candidate_canonical = _norm_url(p.canonical_url)
        if candidate_canonical and candidate_canonical in set(official_canonicals):
            canon_host = urllib.parse.urlsplit(candidate_canonical).hostname or ""
            if _official_host(canon_host, website_domain):
                return {"type": "identical_canonical_url", "url": candidate_canonical, "from": candidate_url}
    return None


_PRIVATE_OR_REDACTED = re.compile(r"\b(?:redact(?:ed|ion)?|privacy|private|proxy|withheld|not disclosed|data protected|whois guard|domains by proxy|contact privacy)\b", re.I)


def _registrant_org(domain: str, http=None) -> tuple[str, str]:
    url = "https://rdap.org/domain/" + urllib.parse.quote(domain, safe=".-")
    r = (http or NET.get_http()).get(url, public_only=True, max_bytes=250_000)
    if not r.ok:
        return "", url
    try:
        record = r.json() or {}
    except Exception:                                           # noqa: BLE001 - malformed public RDAP is unknown
        return "", url
    if not isinstance(record, dict):
        return "", url
    found = []
    entities = record.get("entities") or []
    if not isinstance(entities, list):
        return "", url
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        roles = {str(x).lower() for x in entity.get("roles") or []}
        if "registrant" not in roles:
            continue
        vcard = entity.get("vcardArray")
        entries = vcard[1] if isinstance(vcard, list) and len(vcard) > 1 and isinstance(vcard[1], list) else []
        for row in entries:
            if not isinstance(row, list) or len(row) < 4 or str(row[0]).lower() != "org":
                continue
            raw = row[3]
            if isinstance(raw, list) and all(isinstance(x, str) for x in raw):
                val = " ".join(raw)
            elif isinstance(raw, str):
                val = raw
            else:
                continue
            val = " ".join(val.split())
            normalized = _norm_org(val)
            if (normalized and any(ch.isalpha() for ch in normalized) and not _PRIVATE_OR_REDACTED.search(val)
                    and normalized not in {"n a", "na", "unknown", "none", "null", "not available"}):
                found.append(val)
    # Some registry responses expose an organization extension, but only beneath registrant entities.
    return (found[0] if found else ""), url


def _norm_org(value: str) -> str:
    return T.fold(value)


def email_domain_linked(email_domain: str, website_domain: str, *, context=None, site_material: dict | None = None,
                        website_url: str = "") -> tuple[bool, str, dict]:
    """Return (linked, how, evidence); optional context enables reuse of already fetched official-site pages."""
    ed, wd = _domain(email_domain), _domain(website_domain)
    evidence: dict = {"email_domain": ed, "website_domain": wd, "proofs": []}
    if not ed or not wd:
        return False, "insufficient_evidence", evidence
    if _domain_relation(ed, wd):
        proof = {"type": "domain_hierarchy", "email_domain": ed, "website_domain": wd}
        evidence["proofs"].append(proof)
        return True, "same_or_subdomain", evidence

    from motor_ai_sim.verification.sources.base import Context
    from motor_ai_sim.verification.sources.website_source import read_site_pages

    ctx = context or Context()
    if site_material is None:
        site = website_url or ("https://" + wd)
        site_material = read_site_pages(site, ctx)
    pages = site_material.get("pages") or []
    mention = _visible_domain_mention(ed, pages)
    mx_email, mx_site = _mx_hosts(ed), _mx_hosts(wd)
    evidence["mx"] = {"email_hosts": sorted(mx_email) if mx_email is not None else None,
                      "website_hosts": sorted(mx_site) if mx_site is not None else None}
    if mx_email is None or mx_site is None or not mx_email or mx_site != mx_email:
        evidence["mx_sets_match"] = False
        return False, "mx_records_do_not_match", evidence
    evidence["mx_sets_match"] = True
    proof = mention or _same_site_proof(ed, wd, site_material, ctx)
    if not proof:
        org_email, rdap_email_url = _registrant_org(ed, ctx.http)
        org_site, rdap_site_url = _registrant_org(wd, ctx.http)
        evidence["registrant_check"] = {"source_urls": [rdap_email_url, rdap_site_url],
                                        "organizations": {ed: org_email, wd: org_site}}
        normalized_email = _norm_org(org_email)
        normalized_site = _norm_org(org_site)
        if normalized_email and normalized_site and normalized_email == normalized_site:
            proof = {"type": "same_public_registrant_org", "organization": org_email,
                     "organization_values": {ed: org_email, wd: org_site}, "source_urls": [rdap_email_url, rdap_site_url]}
    if proof:
        evidence["proofs"].append({"type": "matching_mx_hosts", "hosts": sorted(mx_email)})
        evidence["proofs"].append(proof)
        how = "matching_mx_plus_website_mention" if proof["type"] in ("visible_domain_text", "mailto") else "matching_mx_plus_" + proof["type"]
        return True, how, evidence
    evidence["mx_only"] = True
    return False, "matching_mx_without_ownership_proof", evidence
