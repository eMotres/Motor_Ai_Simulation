"""Pure text helpers of the verification: company-name normalisation and matching, person-name search in a page, registrable
domain (eTLD+1) without a dependency, domain-resembles-company, role plausibility.  No I/O."""
from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Optional

from motor_ai_sim.verification import config as C

#: legal-form words removed from both sides before names are compared (long forms and short forms alike)
LEGAL_FORMS = frozenset("""ltd limited gmbh ag sa sarl srl spa bv nv oy ab as aps inc incorporated corp corporation llc llp plc lp co company kg ohg ug mbh
kft zoo sp sro doo dd ooo oao zao pjsc jsc pte pty bhd sdn tbk pt kk kabushiki kaisha gesellschaft aktiengesellschaft haftung beschrankter
und and et the de of holding holdings group groep gruppe international intl""".split())
_CJK_FORMS = ("股份有限公司", "有限责任公司", "有限公司", "集团公司", "集团", "股份公司", "株式会社", "有限會社")
_CJK = re.compile(r"[㐀-鿿぀-ヿ가-힯]")


def fold(s: str) -> str:
    """Accent-free, lower-case, punctuation to spaces (CJK kept)."""
    t = unicodedata.normalize("NFKD", str(s or "")).casefold()
    t = re.sub(r"(?:\w\.){2,}\w?", lambda m: m.group(0).replace(".", ""), t)       # "b.v." / "s.p.a." -> "bv" / "spa"
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = t.replace("ß", "ss").replace("æ", "ae").replace("ø", "o").replace("ł", "l").replace("đ", "d")
    t = re.sub(r"[^\w㐀-鿿぀-ヿ가-힯]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def company_tokens(name: str) -> list[str]:
    t = fold(name)
    for f in _CJK_FORMS:
        t = t.replace(f, " ")
    toks = [w for w in t.split() if w not in LEGAL_FORMS]
    return toks or t.split()


def company_similarity(a: str, b: str) -> float:
    """0..1 similarity of two company names; 1.0 for equal token lists after legal forms are removed."""
    ta, tb = company_tokens(a), company_tokens(b)
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    return difflib.SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio()


def company_match(a: str, b: str) -> str:
    """'match' | 'ambiguous' | 'different' (thresholds in config)."""
    r = company_similarity(a, b)
    return "match" if r >= C.COMPANY_NAME_MATCH else "ambiguous" if r >= C.COMPANY_NAME_AMBIGUOUS else "different"


def norm_reg_no(s: str) -> str:
    """Registration numbers compared without spaces, dots, dashes and case; the country / register prefix is NOT guessed."""
    return re.sub(r"[^0-9a-z]", "", fold(s).replace(" ", ""))


def reg_no_equal(a: str, b: str) -> bool:
    """'HRB 6684' = 'HRB6684' = '6684'; '00445790' = '445790'.  Different digits never match."""
    x, y = norm_reg_no(a), norm_reg_no(b)
    if not x or not y:
        return False
    if x == y:
        return True
    dx, dy = re.sub(r"\D", "", x).lstrip("0"), re.sub(r"\D", "", y).lstrip("0")
    return bool(dx) and dx == dy


# -- persons ---------------------------------------------------------------------------------------------------------

def person_tokens(name: str) -> list[str]:
    return [w for w in fold(name).split() if len(w) > 1 or _CJK.search(w)]


def name_in_text(name: str, text: str) -> bool:
    """Is this person named in the text?  First and last token within a window of five words (either order), or the whole CJK
    name as a substring.  Middle names and titles are ignored."""
    raw = str(name or "").strip()
    if not raw or not text:
        return False
    hay = fold(text)
    if _CJK.search(raw):
        return fold(raw).replace(" ", "") in hay.replace(" ", "")
    toks = person_tokens(raw)
    if len(toks) < 2:
        return False
    first, last = toks[0], toks[-1]
    words = hay.split()
    pos_first = [i for i, w in enumerate(words) if w == first]
    pos_last = [i for i, w in enumerate(words) if w == last]
    return any(abs(i - j) <= 5 for i in pos_first for j in pos_last if i != j)


def officer_matches(officer_name: str, applicant: str) -> bool:
    """Companies House style 'SURNAME, Forename Middle' (or 'Forename Surname') against the applicant's name."""
    if "," in officer_name:
        sur, _, fore = officer_name.partition(",")
        officer_name = (fore.strip() + " " + sur.strip()).strip()
    a, o = person_tokens(applicant), person_tokens(officer_name)
    if len(a) < 2 or len(o) < 2:
        return False
    return a[0] == o[0] and a[-1] == o[-1]


def role_plausible(title: str) -> bool:
    t = " " + str(title or "").casefold() + " "
    return any(w in t for w in C.PLAUSIBLE_ROLE_WORDS)


# -- domains ---------------------------------------------------------------------------------------------------------

#: public suffixes of more than one label that matter for company sites (a conservative vendored subset, not the full list)
_MULTI = frozenset("""co.uk org.uk ac.uk gov.uk ltd.uk plc.uk me.uk net.uk com.cn net.cn org.cn gov.cn edu.cn ac.cn com.hk org.hk net.hk edu.hk
com.tw org.tw net.tw com.au net.au org.au edu.au gov.au co.nz org.nz net.nz co.jp or.jp ne.jp ac.jp go.jp co.kr or.kr ne.kr ac.kr
com.br net.br org.br gov.br com.mx com.ar com.co com.pe com.tr org.tr net.tr com.sg org.sg edu.sg com.my com.ph com.vn com.sa com.eg com.pk
com.ua com.pl com.ru co.in net.in org.in ac.in gov.in co.za org.za co.il org.il co.id or.id co.th ac.th or.th com.ng com.gh co.ke com.ec com.uy
com.ve com.bd com.np com.lk com.kh com.mm com.qa com.kw com.bh com.om com.jo com.lb""".split())
_SECOND_LEVEL = frozenset("co com org net gov edu ac or ne go ltd plc".split())


def host_of(url_or_host: str) -> str:
    t = str(url_or_host or "").strip().lower()
    t = re.sub(r"^[a-z][a-z0-9+.-]*://", "", t)
    t = t.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    t = t.rsplit("@", 1)[-1]
    t = t.split(":", 1)[0].strip(".")
    if t.startswith("www."):
        t = t[4:]
    try:
        return t.encode("idna").decode("ascii")
    except UnicodeError:
        return t


def registrable_domain(url_or_host: str) -> str:
    """eTLD+1 of a host or URL ('mail.siemens.co.uk' -> 'siemens.co.uk').  Conservative: a multi-label public suffix from the
    vendored subset, or a two-letter country code with a generic second level ('co', 'com', ...), takes three labels."""
    h = host_of(url_or_host)
    parts = h.split(".")
    if len(parts) <= 2:
        return h
    last2 = ".".join(parts[-2:])
    if last2 in _MULTI or (len(parts[-1]) == 2 and parts[-2] in _SECOND_LEVEL):
        return ".".join(parts[-3:])
    return last2


def domain_label(domain: str) -> str:
    """The brand label of a registrable domain ('siemens.co.uk' -> 'siemens')."""
    return registrable_domain(domain).split(".")[0]


def domain_resembles_company(domain: str, legal_name: str) -> bool:
    """Does the registrable domain look like it belongs to this company?  A significant name token (>= 4 letters) inside the
    domain label, or the label is the initials of the name (bmw.com / Bayerische Motoren Werke).  A heuristic that errs to
    'no' (the case then asks for more, it is never refused)."""
    label = fold(domain_label(domain)).replace(" ", "")
    if not label:
        return False
    toks = company_tokens(legal_name)
    if any(len(t) >= 4 and (t in label or (len(label) >= 4 and label in t)) for t in toks):
        return True
    initials = "".join(t[0] for t in toks if t and not _CJK.search(t))
    return len(initials) >= 3 and label.startswith(initials)


def valid_email(addr: str) -> bool:
    return bool(re.fullmatch(r"[^@\s<>\"',;]{1,64}@[^@\s<>\"',;]{1,255}\.[^@\s<>\"',;.]{2,}", str(addr or "").strip()))


def normalize_website(raw: str) -> Optional[str]:
    """'siemens.com' / 'https://www.siemens.com/about' -> 'https://www.siemens.com/about'; None when it is not a plausible
    public web address (no dot, an IP address, a credential, an odd scheme)."""
    t = str(raw or "").strip()
    if not t or len(t) > 300 or re.search(r"\s", t):
        return None
    if "://" not in t:
        t = "https://" + t
    m = re.fullmatch(r"(https?)://([^/?#:@]+)(:\d{1,5})?([/?#].*)?", t, re.I)
    if not m:
        return None
    host = m.group(2).lower().strip(".")
    if "." not in host or re.fullmatch(r"[0-9.]+", host) or host.endswith((".local", ".internal", ".localhost", ".lan")):
        return None
    return f"{m.group(1).lower()}://{host}{m.group(3) or ''}{m.group(4) or ''}"


def is_free_or_disposable(email: str) -> tuple[bool, bool]:
    """(free_mail, disposable) for an address."""
    from motor_ai_sim import link_pages as LP
    from motor_ai_sim import nda as N
    d = str(email or "").rpartition("@")[2].strip().lower()
    free = N.free_mail(email) or d in LP.CN_MAIL_DOMAINS
    return free, d in C.disposable_domains()
