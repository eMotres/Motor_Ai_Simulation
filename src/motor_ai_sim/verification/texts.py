"""Words the applicant reads: what we ask for (per request code), the mails and the status lines.  English is the source; the
zh-CN text is a full mirror (the mails carry both, English first, when the reader is Chinese)."""
from __future__ import annotations

from motor_ai_sim.verification import engine as ENG

#: one line per request code; {placeholders} come from the request's params
ASK = {
    "en": {
        ENG.REQ_CORPORATE_EMAIL: "Please confirm a corporate e-mail address{on_domain}{linked_address}. Add it on the page below and we send a six-digit code to it.",
        ENG.REQ_PERSON_PAGE: "Please send a link to a page of your company that lists you (team, contact or staff page), or to your professional profile.",
        ENG.REQ_WEBSITE: "Please give the address of your company's official website.",
        ENG.REQ_REG_NO: "Please give your company's official registration number.",
        ENG.REQ_VAT: "If your company has a VAT number, please give it.",
        ENG.REQ_DOMAIN_MISMATCH: "Your confirmed e-mail ({addresses}) is not on the domain of your website ({domain}). Please add an e-mail on {domain}, or correct the website address.",
        ENG.REQ_ROLE: "Please tell us in a few words what your role is ({title}) and why you need the models.",
        ENG.REQ_LICENCE: "Please upload a scan or photo of your company's business licence (营业执照).",
        ENG.REQ_ACCOUNT_EMAIL: "Please confirm your account e-mail address with the link we sent when you registered.",
        ENG.REQ_FREE_TEXT: "{text}",
    },
    "zh-CN": {
        ENG.REQ_CORPORATE_EMAIL: "请确认一个公司邮箱地址{on_domain}。在下方页面添加后，我们会向该邮箱发送六位验证码。",
        ENG.REQ_PERSON_PAGE: "请提供贵公司网站上列出您姓名的页面链接（团队、联系人或员工页面），或您的职业主页链接。",
        ENG.REQ_WEBSITE: "请提供贵公司的官方网站地址。",
        ENG.REQ_REG_NO: "请提供贵公司的官方注册号。",
        ENG.REQ_VAT: "如果贵公司有增值税号，请提供。",
        ENG.REQ_DOMAIN_MISMATCH: "您已确认的邮箱（{addresses}）不在您网站（{domain}）的域名下。请添加一个 {domain} 域名的邮箱，或更正网站地址。",
        ENG.REQ_ROLE: "请简要说明您的职位（{title}）以及需要这些模型的原因。",
        ENG.REQ_LICENCE: "请上传贵公司营业执照的扫描件或照片。",
        ENG.REQ_ACCOUNT_EMAIL: "请点击注册时发送给您的链接，确认您的账户邮箱。",
        ENG.REQ_FREE_TEXT: "{text}",
    },
}


def ask_line(code: str, params: dict, lang: str = "en") -> str:
    if (params or {}).get("linked"):
        lang = "en"
    tpl = ASK.get(lang, ASK["en"]).get(code) or ASK["en"].get(code) or code
    p = dict(params or {})
    p["linked_address"] = (" (%s is linked to the company website domain %s)" % (p.get("address"), p.get("website_domain"))
                           if p.get("linked") and p.get("address") else "")
    p["on_domain"] = (" on %s" % p["domain"] if lang == "en" else "（%s）" % p["domain"]) if p.get("domain") else ""
    if isinstance(p.get("addresses"), list):
        p["addresses"] = ", ".join(p["addresses"])
    p.setdefault("domain", "")
    p.setdefault("title", "")
    p.setdefault("text", "")
    p.setdefault("addresses", "")
    return tpl.format(**{k: v for k, v in p.items()})


_BRAND = "AeroStator"


def _items(requests: list, lang: str) -> str:
    return "\n".join("  %d. %s" % (i, ask_line(r["code"], r.get("params") or {}, lang)) for i, r in enumerate(requests, 1))


def need_info_mail(requests: list, url: str, zh: bool, name: str = "") -> tuple[str, str]:
    en = ("Hello%s,\n\nthank you for signing our model NDA. Before we open the 3-D tools and the STEP download we check who is asking - "
          "a short routine step that protects our customers' designs.\n\nWe need one more thing from you:\n\n%s\n\n"
          "Answer here (no sign-in needed, the link is personal):\n\n%s\n\n"
          "We do not ask for a passport or an ID. If something is unclear, just reply to this e-mail.\n\n%s support\nsupport@aerostator.com\n") % (
        (" " + name) if name else "", _items(requests, "en"), url, _BRAND)
    if not zh:
        return ("%s: one more thing we need to open your 3-D model access" % _BRAND, en)
    cn = ("\n---\n您好%s，\n\n感谢您签署模型保密协议。在开放 3D 工具和 STEP 下载之前，我们需要例行核实申请人身份，以保护客户的设计。\n\n"
          "还需要您提供以下信息：\n\n%s\n\n请在此页面回复（无需登录，链接为您专属）：\n\n%s\n\n"
          "我们不会索要护照或身份证件。如有疑问，直接回复此邮件即可。\n\n%s 支持团队\nsupport@aerostator.com\n") % (
        (" " + name) if name else "", _items(requests, "zh-CN"), url, _BRAND)
    return ("%s: one more thing we need / 还需要您补充一项信息" % _BRAND, en + cn)


def reminder_mail(requests: list, url: str, zh: bool, name: str = "") -> tuple[str, str]:
    en = ("Hello%s,\n\na gentle reminder about your request for 3-D model and STEP download access.\n\n"
          "We still need one more thing from you:\n\n%s\n\n"
          "Answer here (no sign-in needed, the link is personal):\n\n%s\n\n"
          "We do not ask for a passport or an ID. If something is unclear, write to support@aerostator.com or reply to this e-mail.\n\n"
          "%s support\nsupport@aerostator.com\n") % (
        (" " + name) if name else "", _items(requests, "en"), url, _BRAND)
    if not zh:
        return ("%s: reminder: one more thing we need to open your 3-D model access" % _BRAND, en)
    cn = ("\n---\n您好%s，\n\n温馨提醒：关于您申请 3D 模型和 STEP 下载权限的事宜。\n\n"
          "为了核实您的申请，我们还需要您补充以下信息：\n\n%s\n\n"
          "请在此页面回复（无需登录，链接为您专属）：\n\n%s\n\n"
          "我们不会索要护照或身份证件。如有疑问，可联系 support@aerostator.com 或直接回复此邮件。\n\n"
          "%s 支持团队\nsupport@aerostator.com\n") % (
        (" " + name) if name else "", _items(requests, "zh-CN"), url, _BRAND)
    return ("%s: reminder: one more thing we need / 温馨提醒：还需要您补充信息" % _BRAND, en + cn)


def review_mail(requests: list, url: str, zh: bool) -> tuple[str, str]:
    extra = ("\nIn the meantime we would also like:\n\n%s\n\nAnswer here:\n\n%s\n" % (_items(requests, "en"), url)) if requests else ""
    en = ("Hello,\n\nthank you for signing our model NDA. Your request is with our team for a manual check; we will write to you as soon as it is done "
          "(usually within one working day).%s\n\n%s support\nsupport@aerostator.com\n") % (extra, _BRAND)
    if not zh:
        return ("%s: your request is being reviewed" % _BRAND, en)
    cn_extra = ("\n同时请您补充：\n\n%s\n\n请在此页面回复：\n\n%s\n" % (_items(requests, "zh-CN"), url)) if requests else ""
    cn = "\n---\n您好，\n\n感谢您签署模型保密协议。您的申请已转交我们的团队人工核实，完成后我们会立即通知您（通常在一个工作日内）。%s\n\n%s 支持团队\nsupport@aerostator.com\n" % (cn_extra, _BRAND)
    return ("%s: your request is being reviewed / 您的申请正在审核" % _BRAND, en + cn)


def verified_mail(zh: bool) -> tuple[str, str]:
    en = "Hello,\n\nyour verification is complete. The 3-D tools and the STEP download are now open in your account.\n\n%s support\nsupport@aerostator.com\n" % _BRAND
    if not zh:
        return ("%s: your 3-D model access is open" % _BRAND, en)
    return ("%s: your 3-D model access is open / 3D 模型权限已开通" % _BRAND,
            en + "\n---\n您好，\n\n核实已完成，您账户中的 3D 工具和 STEP 下载现已开通。\n\n%s 支持团队\nsupport@aerostator.com\n" % _BRAND)


def denied_mail(reason_public: str, zh: bool) -> tuple[str, str]:
    en = ("Hello,\n\nwe cannot open the 3-D model downloads for your account at this time.%s\n"
          "If you think this is a mistake, reply to this e-mail.\n\n%s support\nsupport@aerostator.com\n") % ((" " + reason_public) if reason_public else "", _BRAND)
    if not zh:
        return ("%s: about your 3-D model access" % _BRAND, en)
    return ("%s: about your 3-D model access / 关于您的 3D 模型权限" % _BRAND,
            en + "\n---\n您好，\n\n目前我们无法为您的账户开通 3D 模型下载。如您认为这是误判，请直接回复此邮件。\n\n%s 支持团队\nsupport@aerostator.com\n" % _BRAND)


def otp_mail(code: str, zh: bool) -> tuple[str, str]:
    en = ("Hello,\n\nyour AeroStator confirmation code is %s\n\nIt is valid for 30 minutes. Enter it on the verification page. "
          "If you did not ask for it, ignore this message.\n\n%s support\nsupport@aerostator.com\n") % (code, _BRAND)
    if not zh:
        return ("%s: your confirmation code %s" % (_BRAND, code), en)
    return ("%s: your confirmation code %s / 验证码" % (_BRAND, code),
            en + "\n---\n您好，\n\n您的 AeroStator 验证码是 %s，30 分钟内有效。请在核实页面输入。如果这不是您本人的操作，请忽略此邮件。\n\n%s 支持团队\nsupport@aerostator.com\n" % (code, _BRAND))


def admin_mail(case: dict, url: str) -> tuple[str, str]:
    a = case.get("application") or {}
    reasons = "\n".join("  - " + r.get("text", "") for r in case.get("reasons") or [])
    return ("[verification] %s needs a manual review: %s" % (a.get("legal_name", "?"), case["id"]),
            "Case %s\nApplicant: %s (%s) <%s>\nCompany: %s, %s, reg. %s\nStatus: %s\n\n%s\n\nOpen: %s\n" % (
                case["id"], a.get("applicant_name"), a.get("title"), case.get("user"), a.get("legal_name"), a.get("country"), a.get("reg_no"),
                case.get("status"), reasons, url))


def digest_mail(waiting: list[dict], url: str, now: float) -> tuple[str, str]:
    lines = []
    for c in waiting:
        a = c.get("application") or {}
        st = c.get("status", "")
        if st == ENG.MANUAL_REVIEW:
            ref_t = c.get("status_at") or c.get("created", now)
        else:
            ref_t = c.get("created", now)
        days = max(0.0, (now - ref_t) / 86400.0)
        days_str = ("%.1f days" % days) if days >= 0.1 else ("%.1f hours" % ((now - ref_t) / 3600.0))
        lines.append(
            "- Company: %s (%s)\n  Applicant: %s\n  Status: %s\n  Waiting: %s\n  Case: %s" % (
                a.get("legal_name") or "—",
                a.get("country") or "—",
                a.get("applicant_name") or c.get("user", "—"),
                st,
                days_str,
                c.get("id", "—"),
            )
        )
    body = (
        "Hello,\n\nThe following %d verification case(s) are waiting for administrator review:\n\n%s\n\n"
        "Review queue: %s\n\n%s\n"
    ) % (len(waiting), "\n\n".join(lines), url, _BRAND)
    subject = "[verification] Daily digest: %d case(s) waiting for review" % len(waiting)
    return (subject, body)


#: the public status line (the web translates by `state`; this English text is for API clients)
PUBLIC = {
    "in_progress": "Verification in progress.",
    "need_info": "We need one more thing from you.",
    "denied": "We could not open access for this account. Please contact support.",
    "verified": "Verified.",
    "ok": "",
}
