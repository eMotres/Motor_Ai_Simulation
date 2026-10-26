"""Online NDA click-through: the agreement text (versioned), validation, the append-only signature record, the signed PDF,
the e-mail copy and the admin overview.

Where things live (``<shared>`` = ``workspace.shared_root()``; ``MOTRES_LEGAL_ROOT`` overrides, tests):

    <shared>/legal/model_nda/current.txt                              the current version name
    <shared>/legal/model_nda/<version>/model_nda_<lang>.md            the text, placeholders {{company}} {{reg_no}} {{country}}
                                                                      {{address}} {{signatory}} {{title}} {{email}} {{date}} {{version}}
                                                                      and the blocks {{#if country_is_cn_hk}}..{{/if}} /
                                                                      {{#unless country_is_cn_hk}}..{{/unless}} (CN or HK)
    <shared>/legal/nda_signatures.jsonl                               append-only acceptance records (never rewritten)
    <shared>/legal/nda_signed/<record_id>.pdf                         the signed PDF next to the record

The repo ships a placeholder text (``config/legal/model_nda/v0-placeholder``, marked "NOT FOR SIGNATURE"); it is used when the
shared directory has no ``current.txt``, and while it is current signing is disabled.  This module never creates the legal
directory: the owner installs it, signing refuses when it is missing.

A signature stays valid for its own version; a newer version does not invalidate older signatures (the user record keeps the
version that was signed).
"""
from __future__ import annotations

import datetime
import hashlib
import io
import json
import logging
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

LANGS = ("en", "zh-CN")
PLACEHOLDERS = ("company", "reg_no", "country", "address", "signatory", "title", "email", "date", "version")
FIELDS = ("company", "reg_no", "country", "address", "signatory", "title")
PLACEHOLDER_VERSION_PREFIX = "v0-placeholder"
PLACEHOLDER_MARK = "NOT FOR SIGNATURE"
SPECIMEN_BLANK = "________"
SPECIMEN_HEADER = "SPECIMEN - NOT SIGNED - for review only"
SPECIMEN_WATERMARK = "SPECIMEN - NOT SIGNED"
_LOCK = threading.RLock()
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ID_RE = re.compile(r"^n[0-9]{8}-[0-9a-f]{12}$")

#: ISO 3166-1 alpha-2 codes (the UI names them with Intl.DisplayNames); an unknown code is refused
COUNTRIES = tuple("""AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF
CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR
GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA
MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW
PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US
UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW""".split())

_FREE_MAIL = frozenset("""gmail.com googlemail.com outlook.com hotmail.com live.com msn.com icloud.com me.com mac.com aol.com
qq.com foxmail.com 163.com 126.com yeah.net 139.com sina.com sina.cn sohu.com 189.cn proton.me protonmail.com pm.me
mail.ru inbox.ru list.ru bk.ru web.de zoho.com""".split())
_FREE_MAIL_PREFIX = ("yahoo.", "ymail.", "yandex.", "gmx.", "outlook.", "hotmail.", "live.")


# -- where the legal files are ------------------------------------------------

def legal_root() -> Path:
    env = (os.environ.get("MOTRES_LEGAL_ROOT") or "").strip()
    if env:
        return Path(env)
    from motor_ai_sim.workspace import shared_root
    return Path(str(shared_root())) / "legal"


def repo_legal_root() -> Path:
    """The placeholder text shipped with the code (``config/legal``)."""
    return Path(__file__).resolve().parents[2] / "config" / "legal"


def _read(p: Path) -> Optional[str]:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def current_version() -> Optional[tuple[str, Path]]:
    """(version, directory of ``model_nda/``) of the installed current version, else the shipped placeholder, else None."""
    for root in (legal_root(), repo_legal_root()):
        v = (_read(root / "model_nda" / "current.txt") or "").strip()
        if v and _VERSION_RE.match(v) and (root / "model_nda" / v).is_dir():
            return v, root / "model_nda"
    ph = repo_legal_root() / "model_nda" / PLACEHOLDER_VERSION_PREFIX
    return (PLACEHOLDER_VERSION_PREFIX, ph.parent) if ph.is_dir() else None


def is_placeholder(version: str, text: str) -> bool:
    return version.startswith(PLACEHOLDER_VERSION_PREFIX) or PLACEHOLDER_MARK in text


def sha256_text(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def current(lang: str) -> Optional[dict]:
    """Current EN and zh-CN texts, retaining selected-language scalar fields for compatibility."""
    lang = lang if lang in LANGS else "en"
    cv = current_version()
    if cv is None:
        return None
    version, base = cv
    texts = {code: _read(base / version / ("model_nda_%s.md" % code)) for code in LANGS}
    # Both mirrors are presented and signed together; do not silently serve a one-language agreement.
    if any(text is None for text in texts.values()):
        return None
    hashes = {code: sha256_text(texts[code]) for code in LANGS}
    text = texts[lang]
    return dict(version=version, lang=lang, text=text, sha256=hashes[lang], texts=texts, sha256s=hashes,
                signable=not any(is_placeholder(version, texts[code]) for code in LANGS) and legal_root().is_dir(),
                countries=list(COUNTRIES))


_BLOCK = re.compile(r"\{\{#(if|unless)\s+(\w+)\s*\}\}(.*?)\{\{/\1\s*\}\}", re.S)
_BLANKS = re.compile(r"\n{3,}")


def template_values(fields: dict, email: str, date: str, version: str) -> dict:
    """The values a text is rendered with: the form fields, e-mail, date, version and the flag ``country_is_cn_hk``."""
    return dict(fields, email=email, date=date, version=version,
                country_is_cn_hk=str(fields.get("country") or "").upper() in ("CN", "HK"))


def render(text: str, values: dict) -> str:
    """Resolve the {{#if flag}}..{{/if}} / {{#unless flag}}..{{/unless}} blocks (one level, NOT nested: a hand-written renderer,
    no template engine), then fill {{placeholder}} entries; an unknown placeholder is left as it is (a typo in the text shows,
    never a blank).  Runs of blank lines left by a removed block collapse to one.  This is the text that is shown, hashed
    and put into the PDF; web/src/lib/nda.ts renders the same way."""
    def block(m):
        truth = bool(values.get(m.group(2)))
        return m.group(3) if (truth if m.group(1) == "if" else not truth) else ""

    t = _BLOCK.sub(block, text)
    t = re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), t)
    return _BLANKS.sub("\n\n", t).strip("\n") + "\n"


# -- validation ---------------------------------------------------------------

def free_mail(email: str) -> bool:
    d = (email or "").rsplit("@", 1)[-1].strip().lower()
    return d in _FREE_MAIL or d.startswith(_FREE_MAIL_PREFIX)


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def validate(body: dict) -> tuple[dict, dict]:
    """-> (clean fields, errors {field: message}).  Every field is required, the country is an ISO code, the typed name must
    equal the signatory name (case and spacing aside), the acceptance box must be ticked."""
    f = {k: _norm(body.get(k)) for k in FIELDS}
    err = {}
    for k in FIELDS:
        if not f[k]:
            err[k] = "required"
        elif len(f[k]) > 300:
            err[k] = "too long"
    f["country"] = f["country"].upper()
    if f["country"] and f["country"] not in COUNTRIES:
        err["country"] = "choose a country from the list"
    if body.get("accept") is not True:
        err["accept"] = "tick the box to accept the agreement"
    typed = _norm(body.get("typed_name"))
    if not typed:
        err["typed_name"] = "type your full name to sign"
    elif f["signatory"] and typed.casefold() != f["signatory"].casefold():
        err["typed_name"] = "must match the signatory name"
    return f, err


# -- the signed PDF -----------------------------------------------------------

def _pdf_font(*texts: str) -> tuple[str, str]:
    """(regular, bold) font names: Helvetica for WinAnsi text, the built-in Chinese CID font otherwise (no font file needed)."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    try:
        "".join(texts).encode("cp1252")
        return "Helvetica", "Helvetica-Bold"
    except UnicodeEncodeError:
        if "STSong-Light" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
            pdfmetrics.registerFontFamily("STSong-Light", normal="STSong-Light", bold="STSong-Light",
                                          italic="STSong-Light", boldItalic="STSong-Light")
        return "STSong-Light", "STSong-Light"


def _inline(s: str) -> str:
    """XML-escaped paragraph markup with the Markdown **bold** and *italic* runs of the agreement text."""
    from xml.sax.saxutils import escape
    t = escape(s).replace("\n", "<br/>")
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t, flags=re.S)
    return re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", t, flags=re.S)


def build_pdf(rendered: str | dict[str, str], rec: dict, *, compress: bool = True, specimen: bool = False,
              label: str = "NDA", extra_rows: tuple = (), note: str | None = None) -> bytes:
    """Render one legacy text or both language sections followed by the acceptance block/specimen details.

    ``label`` / ``extra_rows`` / ``note`` let another click-through agreement (platform_agreement.py) reuse the same PDF: the
    document title, rows inserted before the record id, and the closing sentence."""
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    if specimen:
        block = [("Status", SPECIMEN_HEADER), ("Agreement version", rec["version"]),
                 ("Text SHA-256", rec["text_sha256"]), ("Template SHA-256", rec.get("template_sha256", "?"))]
        for code, label in (("en", "EN"), ("zh-CN", "zh-CN")):
            if rec.get("text_sha256s", {}).get(code):
                block.append(("%s text SHA-256" % label, rec["text_sha256s"][code]))
            if rec.get("template_sha256s", {}).get(code):
                block.append(("%s template SHA-256" % label, rec["template_sha256s"][code]))
    else:
        block = [("Signatory", rec["signatory"]), ("Title", rec["title"]), ("Company", rec["company"]),
                 ("Registration no.", rec["reg_no"]), ("Country", rec["country"]), ("Address", rec["address"]),
                 ("E-mail", rec["email"]), ("Signed (UTC)", rec["iso"]), ("IP address", rec.get("ip") or "?"),
                 ("Agreement version", rec["version"]), ("Text SHA-256", rec["text_sha256"]),
                 ("Template SHA-256", rec.get("template_sha256", "?")), ("Record ID", rec["id"])]
        for code, label in (("en", "EN"), ("zh-CN", "zh-CN")):
            if rec.get("text_sha256s", {}).get(code):
                block.insert(-1, ("%s text SHA-256" % label, rec["text_sha256s"][code]))
            if rec.get("template_sha256s", {}).get(code):
                block.insert(-1, ("%s template SHA-256" % label, rec["template_sha256s"][code]))
    if extra_rows and not specimen:
        block[-1:-1] = [(str(k), str(v)) for k, v in extra_rows]
    if isinstance(rendered, dict):
        if set(rendered) != set(LANGS):
            raise ValueError("Both English and zh-CN agreement texts are required.")
        sections = {code: rendered[code] for code in LANGS}
    else:
        sections = {"en": rendered}
    meta_font, _meta_bold = _pdf_font(*[str(v) for _, v in block])
    meta_wrap = {"wordWrap": "CJK"} if meta_font == "STSong-Light" else {}
    meta = ParagraphStyle("meta", fontName=meta_font, fontSize=8.5, leading=11, spaceAfter=0, **meta_wrap)
    tbl = Table([[Paragraph(escape(k), meta), Paragraph(escape(str(v)), meta)] for k, v in block], colWidths=[40 * mm, 125 * mm])
    tbl.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                             ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke)]))
    story = [tbl, Spacer(1, 8)] if specimen else []
    for index, (code, text) in enumerate(sections.items()):
        reg, bold = _pdf_font(text)
        wrap = {"wordWrap": "CJK"} if reg == "STSong-Light" else {}
        body = ParagraphStyle("body_%s" % code, fontName=reg, fontSize=9.5, leading=13.5, spaceAfter=5, **wrap)
        head = ParagraphStyle("head_%s" % code, parent=body, fontName=bold, fontSize=11.5, leading=15, spaceBefore=6, spaceAfter=4)
        title = ParagraphStyle("title_%s" % code, parent=head, fontSize=15, leading=19, spaceAfter=10)
        if len(sections) > 1:
            if index:
                from reportlab.platypus import PageBreak
                story.append(PageBreak())
            label = "English (EN)" if code == "en" else "Simplified Chinese (zh-CN)"
            story.append(Paragraph(escape(label), title))
        for chunk in re.split(r"\n\s*\n", text.strip()):
            line = chunk.strip()
            if not line:
                continue
            m = re.match(r"^(#{1,3})\s+(.*)$", line, re.S)
            if m:
                story.append(Paragraph(_inline(m.group(2).strip()), title if len(m.group(1)) == 1 else head))
            else:
                story.append(Paragraph(_inline(line), body))
    if not specimen:
        story += [Spacer(1, 8), Paragraph("Acceptance record", head), tbl, Spacer(1, 6),
                  Paragraph(escape(note or "Accepted online by typing the signatory name. The selected-language SHA-256 above is the hash "
                                   "of that exact rendered text; both language hashes are recorded."), meta)]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                            title=("%s %s SPECIMEN" % (label, rec["version"])) if specimen else ("%s %s" % (label, rec["id"])),
                            author="MOTRES d.o.o.", pageCompression=1 if compress else 0)
    if specimen:
        def _specimen_page(canvas, _doc):
            canvas.saveState()
            canvas.setFont("Helvetica-Bold", 9)
            canvas.setFillColor(colors.Color(0.45, 0.45, 0.45))
            canvas.drawCentredString(A4[0] / 2, A4[1] - 11 * mm, SPECIMEN_HEADER)
            canvas.translate(A4[0] / 2, A4[1] / 2)
            canvas.rotate(38)
            canvas.setFont("Helvetica-Bold", 42)
            canvas.setFillColor(colors.Color(0.85, 0.85, 0.85, alpha=0.35))
            canvas.drawCentredString(0, 0, SPECIMEN_WATERMARK)
            canvas.restoreState()

        doc.build(story, onFirstPage=_specimen_page, onLaterPages=_specimen_page)
    else:
        doc.build(story)
    return buf.getvalue()


def build_specimen_pdf(cur_or_lang: dict | str = "en", *, compress: bool = True) -> bytes:
    """Render the current agreement (EN followed by zh-CN) as an unsigned specimen; nothing is stored."""
    cur = current(cur_or_lang) if isinstance(cur_or_lang, str) else cur_or_lang
    if cur is None:
        raise FileNotFoundError("The agreement text is not installed.")
    blanks = {k: SPECIMEN_BLANK for k in FIELDS}
    values = template_values(blanks, SPECIMEN_BLANK, SPECIMEN_BLANK, cur["version"])
    texts = cur.get("texts") or {cur["lang"]: cur["text"]}
    if set(texts) != set(LANGS):
        raise FileNotFoundError("Both English and zh-CN agreement texts are required.")
    rendered_by_lang = {code: render(texts[code], values) for code in LANGS}
    hashes = cur.get("sha256s") or {code: sha256_text(texts[code]) for code in LANGS}
    rec = dict(version=cur["version"], text_sha256=sha256_text(rendered_by_lang[cur["lang"]]),
               template_sha256=cur["sha256"],
               text_sha256s={code: sha256_text(rendered_by_lang[code]) for code in LANGS},
               template_sha256s=hashes)
    return build_pdf(rendered_by_lang, rec, compress=compress, specimen=True)


specimen_pdf = build_specimen_pdf


# -- records ------------------------------------------------------------------

def signatures_path() -> Path:
    return legal_root() / "nda_signatures.jsonl"


def pdf_path(record_id: str) -> Optional[Path]:
    """Path of the signed PDF of a record; the id is matched against a strict pattern, never joined raw."""
    if not _ID_RE.match(record_id or ""):
        return None
    p = legal_root() / "nda_signed" / (record_id + ".pdf")
    return p if p.is_file() else None


def new_record_id(now: datetime.datetime) -> str:
    return "n%s-%s" % (now.strftime("%Y%m%d"), secrets.token_hex(6))


def read_records(limit: int = 200) -> list[dict]:
    p = signatures_path()
    rows = []
    try:
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return rows[-limit:][::-1]


def get_record(record_id: str) -> Optional[dict]:
    return next((r for r in read_records(100000) if r.get("id") == record_id), None)


def sign(*, user: str, email: str, fields: dict, cur: dict, ip: str, user_agent: str, data_sharing: bool = False) -> dict:
    """Render, write the PDF, append the record.  -> the record.  Raises OSError when the legal directory is not writable.

    ``data_sharing`` is the SEPARATE, optional consent to share the customer's own data with the engineering dataset
    (motor_ai_sim.data_sharing); it is stored in the record as ``data_sharing: {consent, version, time}`` and is not part of the
    agreement text, the hash or the PDF."""
    from motor_ai_sim.data_sharing import CONSENT_VERSION
    from motor_ai_sim.private_files import ensure_private_dir, open_private
    root = legal_root()
    if not root.is_dir():
        raise OSError("legal directory %s is not installed" % root)
    now = datetime.datetime.now(datetime.timezone.utc)
    values = template_values(fields, email, now.strftime("%Y-%m-%d"), cur["version"])
    texts = cur.get("texts") or {}
    if set(texts) != set(LANGS):
        raise ValueError("Both English and zh-CN agreement texts are required for signing.")
    rendered_by_lang = {code: render(texts[code], values) for code in LANGS}
    rid = new_record_id(now)
    rec = dict(id=rid, ts=round(now.timestamp(), 3), iso=now.strftime("%Y-%m-%dT%H:%M:%SZ"), user=user, email=email, **fields,
               version=cur["version"], lang=cur["lang"], template_sha256=cur["sha256"], text_sha256=sha256_text(rendered_by_lang[cur["lang"]]),
               template_sha256s={code: cur.get("sha256s", {}).get(code, sha256_text(texts[code])) for code in LANGS},
               text_sha256s={code: sha256_text(rendered_by_lang[code]) for code in rendered_by_lang},
               ip=ip, user_agent=(user_agent or "")[:300], pdf=rid + ".pdf", free_mail=free_mail(email),
               data_sharing=dict(consent=data_sharing is True, version=CONSENT_VERSION, time=now.strftime("%Y-%m-%dT%H:%M:%SZ")))
    pdf = build_pdf(rendered_by_lang, rec)
    with _LOCK:
        d = ensure_private_dir(root / "nda_signed")
        tmp = d / (rid + ".pdf.tmp")
        tmp.write_bytes(pdf)
        os.replace(tmp, d / (rid + ".pdf"))
        with open_private(signatures_path(), "a") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    rec["_pdf"] = pdf
    return rec


# -- e-mail copy --------------------------------------------------------------

def admin_address() -> str:
    env = (os.environ.get("NDA_NOTIFY_EMAIL") or "").strip()
    if env:
        return env
    first = [e.strip() for e in (os.environ.get("ADMIN_EMAILS") or "").split(",") if e.strip()]
    return first[0] if first else ""


def mail_copies(rec: dict, pdf: bytes) -> bool:
    """Send the signed PDF to the user and to the admin address.  Skipped quietly (logged) when mail is not configured."""
    from email.message import EmailMessage
    from motor_ai_sim import auth_email as AE
    if not AE.smtp_configured():
        log.info("nda: mail not configured - copy of %s not sent", rec["id"])
        return False
    to = [rec["email"]] + ([admin_address()] if admin_address() and admin_address().lower() != rec["email"].lower() else [])

    def _go():
        for addr in to:
            msg = EmailMessage()
            msg["To"] = addr
            msg["Subject"] = "Signed NDA %s - %s" % (rec["id"], rec["company"])
            msg.set_content("The non-disclosure agreement (version %s) was accepted online by %s, %s, for %s on %s.\n"
                            "The signed copy is attached. Text SHA-256: %s\nRecord: %s\n" % (
                                rec["version"], rec["signatory"], rec["title"], rec["company"], rec["iso"], rec["text_sha256"], rec["id"]))
            msg.add_attachment(pdf, maintype="application", subtype="pdf", filename="NDA_%s.pdf" % rec["id"])
            try:
                AE.deliver(msg)
                log.info("nda: copy of %s sent to %s", rec["id"], addr)
            except Exception as e:                                  # noqa: BLE001
                log.warning("nda: mail to %s failed (%s: %s)", addr, type(e).__name__, str(e)[:160])

    threading.Thread(target=_go, daemon=True, name="nda-mail").start()
    return True


def public_record(r: dict) -> dict:
    """Admin overview row (no user agent, no hashes of the user's agent)."""
    return {k: r.get(k) for k in ("id", "iso", "user", "email", "company", "reg_no", "country", "signatory", "title", "version",
                                  "lang", "free_mail", "ip")} | {"data_sharing": _consent_of(r)}


def _consent_of(r: dict) -> dict:
    """Consent as it was ticked at signing (older records: none = false)."""
    ds = r.get("data_sharing")
    return {"consent": isinstance(ds, dict) and ds.get("consent") is True,
            "version": str((ds or {}).get("version") or "") if isinstance(ds, dict) else "",
            "time": str((ds or {}).get("time") or "") if isinstance(ds, dict) else ""}
