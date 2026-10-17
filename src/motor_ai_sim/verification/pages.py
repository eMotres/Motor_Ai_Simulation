"""The applicant's reply page: server-rendered HTML, NO JavaScript, inline CSS, a few kB - like the sign-in links (link_pages.py), so
it works in any in-app browser and over a slow line.  Plain HTML forms: add a corporate e-mail (a code is sent to it), links and a
note, corrections of the company details, ONE document (pdf / png / jpg).  Every dynamic value is escaped."""
from __future__ import annotations

import html
from typing import Optional
from motor_ai_sim import link_pages as LP
from motor_ai_sim.verification import config as C
from motor_ai_sim.verification import engine as ENG
from motor_ai_sim.verification import texts as TX

STR = {
    "en": {
        "title": "Verification", "h": "Verification of your request",
        "intro": "To open the 3-D tools and the STEP download we check who is asking. This takes a minute and needs no sign-in.",
        "company": "Company", "status": "Status",
        "s_in_progress": "In progress - we are checking your details, or our team is looking at them.",
        "s_need_info": "We need one more thing from you.", "s_ok": "Verified - your access is open.",
        "s_denied": "We could not open access for this account. Please reply to our e-mail if you think this is a mistake.",
        "needs": "What we need", "nothing": "Nothing is needed from you right now. We will write to you.",
        "email_h": "Confirm a corporate e-mail", "email_label": "E-mail address at your company", "email_go": "We send a six-digit code to this address.",
        "code_h": "Enter the code we sent to {email}", "code_label": "Six-digit code", "code_go": "Confirm",
        "links_h": "Links and details", "urls_label": "Links (one per line): a page of your company that lists you, your professional profile, a press mention",
        "note_label": "A note (optional)", "site_label": "Official website", "reg_label": "Company registration number", "vat_label": "VAT number (optional)",
        "links_go": "Send", "doc_h": "Upload one document (optional)", "doc_hint": "PDF, PNG or JPG, up to 5 MB. For example a business licence. No passport or ID is needed.",
        "doc_go": "Upload", "verified_list": "Confirmed e-mail addresses", "open": "Open AeroStator",
        "m_sent": "We sent a code to {email}. It is valid for 30 minutes.", "m_verified": "Thank you - {email} is confirmed.",
        "m_reply": "Thank you - we received your answer and are checking it.", "m_doc": "Thank you - the document is stored.",
        "e_invalid": "That address is not valid.", "e_free": "Please use an address at your company, not a free or disposable mailbox.",
        "e_no_domain": "That domain does not exist - please check the address.", "e_limit": "Too many codes were sent today. Try again tomorrow or reply to our e-mail.",
        "e_wrong": "The code is not right.", "e_expired": "The code has expired. Ask for a new one.", "e_locked": "Too many wrong codes. Ask for a new one.",
        "e_none": "There is no code waiting for that address. Ask for a new one.", "e_type": "Only PDF, PNG or JPG files are accepted.",
        "e_size": "The file is too large (5 MB at most).", "e_empty": "Choose a file first.", "e_docs": "The limit of documents for this request is reached.",
        "e_nothing": "Please fill in at least one field.", "other": "中文",
        "m_need_email": "Your details are saved. To finish, enter an e-mail address at your company above and press Send - we send a code to it.",
        "done": "Already received", "d_company": "company confirmed", "d_links": "links: {n}", "d_email": "company e-mail confirmed",
        "no_code_h": "Did not get the code?", "no_code_spam": "Please check your spam or junk folder.", "resend_go": "Send a new code",
        "support_q": "Questions? Write to ", "e_cooldown": "Please wait 2 minutes before requesting a new code.",
    },
    "zh-CN": {
        "title": "身份核实", "h": "申请核实", "intro": "开放 3D 工具和 STEP 下载之前，我们需要核实申请人身份。只需一分钟，无需登录。",
        "company": "公司", "status": "状态",
        "s_in_progress": "处理中 - 我们正在核对您的信息，或由团队人工审核。",
        "s_need_info": "还需要您补充一项信息。", "s_ok": "已通过核实 - 您的权限已开通。",
        "s_denied": "目前无法为此账户开通权限。如您认为这是误判，请回复我们的邮件。",
        "needs": "需要您提供", "nothing": "目前无需您操作，我们会再与您联系。",
        "email_h": "确认公司邮箱", "email_label": "您的公司邮箱地址", "email_go": "我们会向该地址发送六位验证码。",
        "code_h": "请输入发送到 {email} 的验证码", "code_label": "六位验证码", "code_go": "确认",
        "links_h": "链接与资料", "urls_label": "链接（每行一个）：贵公司网站上列出您的页面、您的职业主页、媒体报道",
        "note_label": "备注（可选）", "site_label": "官方网站", "reg_label": "公司注册号", "vat_label": "增值税号（可选）",
        "links_go": "提交", "doc_h": "上传一份文件（可选）", "doc_hint": "PDF、PNG 或 JPG，不超过 5 MB，例如营业执照。无需护照或身份证件。",
        "doc_go": "上传", "verified_list": "已确认的邮箱", "open": "打开 AeroStator",
        "m_sent": "验证码已发送到 {email}，30 分钟内有效。", "m_verified": "谢谢 - {email} 已确认。",
        "m_reply": "谢谢 - 我们已收到您的回复，正在核对。", "m_doc": "谢谢 - 文件已保存。",
        "e_invalid": "该地址无效。", "e_free": "请使用贵公司的邮箱，而不是免费或一次性邮箱。",
        "e_no_domain": "该域名不存在，请检查地址。", "e_limit": "今天发送的验证码过多。请明天再试，或回复我们的邮件。",
        "e_wrong": "验证码不正确。", "e_expired": "验证码已过期，请重新获取。", "e_locked": "错误次数过多，请重新获取验证码。",
        "e_none": "该地址没有待验证的验证码，请重新获取。", "e_type": "仅接受 PDF、PNG 或 JPG 文件。",
        "e_size": "文件过大（最大 5 MB）。", "e_empty": "请先选择文件。", "e_docs": "此申请的文件数量已达上限。",
        "e_nothing": "请至少填写一项。", "other": "English",
        "m_need_email": "您的资料已保存。最后一步：请在上方填写您的公司邮箱并点击提交，我们会发送验证码。",
        "done": "已收到", "d_company": "公司已核实", "d_links": "链接：{n}", "d_email": "公司邮箱已确认",
        "no_code_h": "没收到验证码？", "no_code_spam": "请检查垃圾邮件箱。", "resend_go": "重新发送验证码",
        "support_q": "如有疑问，请写信至 ", "e_cooldown": "请等待 2 分钟后再重新获取验证码。",
    },
}


def _e(x) -> str:
    return html.escape(str(x), quote=True)


def _status_line(s: dict, state: str) -> str:
    return s.get({"ok": "s_ok", "need_info": "s_need_info", "denied": "s_denied"}.get(state, "s_in_progress"), "")


def page(lang: str, case: dict, state: str, *, token: str, requests: list, flash: str = "", flash_kind: str = "", pending_email: str = "",
         switch_url: str = "", open_url: str = "", typed: Optional[dict] = None, saved: bool = False) -> str:
    """``typed`` = what the applicant entered in the form, shown again after an error so nothing typed is lost; ``saved`` = the links /
    details of the same submit were stored (said next to an e-mail error)."""
    lang = LP.norm_lang(lang)
    s = STR[lang]
    app = case.get("application") or {}
    base = "/api/verification/reply/" + token
    msg = ""
    if flash:
        key = ("e_" if flash_kind == "err" else "m_") + flash
        text = s.get(key, flash).replace("{email}", pending_email or "")
        msg = '<p class="%s" role="%s">%s</p>' % ("err" if flash_kind == "err" else "ok", "alert" if flash_kind == "err" else "status", _e(text))
    if saved and flash != "reply":
        msg += '<p class="ok" role="status">%s</p>' % _e(s["m_reply"])
    typed = typed or {}
    needs = ""
    got = []                                          # what is already settled, so a second visit does not look like the same form
    if ((case.get("decision") or {}).get("company") or {}).get("confirmed"):
        got.append(s["d_company"])
    if case.get("urls"):
        got.append(s["d_links"].replace("{n}", str(len(case["urls"]))))
    if (case.get("emails") or {}).get("verified"):
        got.append(s["d_email"])
    if got and state in ("need_info", "in_progress"):
        needs += '<p class="ok">%s: %s</p>' % (_e(s["done"]), _e(" · ".join("✓ " + g for g in got)))
    if state in ("need_info", "in_progress"):
        if requests:
            needs += "<h2>%s</h2><ol>%s</ol>" % (_e(s["needs"]), "".join("<li>%s</li>" % _e(TX.ask_line(r["code"], r.get("params") or {}, lang)) for r in requests))
        else:
            needs += "<p>%s</p>" % _e(s["nothing"])
    forms = ""
    if state not in ("ok", "denied"):
        suggested_email = next((str(r.get("params", {}).get("address") or "") for r in requests
                                if r.get("code") == ENG.REQ_CORPORATE_EMAIL and r.get("params", {}).get("linked")), "")
        code_form = ""
        if pending_email:
            code_form = ('<form method="post" action="%s"><input type="hidden" name="action" value="confirm_email"><input type="hidden" name="email" value="%s">'
                         '<h2>%s</h2><label for="code">%s</label><input id="code" name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="8" required>'
                         '<button type="submit">%s</button></form>'
                         '<div class="no-code"><h2>%s</h2><p class="muted">%s</p>'
                         '<form method="post" action="%s" style="border-top:none;margin-top:8px">'
                         '<input type="hidden" name="action" value="resend"><input type="hidden" name="email" value="%s">'
                         '<button type="submit" class="alt">%s</button></form>'
                         '<p class="muted">%s<a href="mailto:support@aerostator.com">support@aerostator.com</a></p></div>') % (
                _e(base), _e(pending_email), _e(s["code_h"].replace("{email}", pending_email)),
                _e(s["code_label"]), _e(s["code_go"]),
                _e(s["no_code_h"]), _e(s["no_code_spam"]), _e(base), _e(pending_email), _e(s["resend_go"]),
                _e(s["support_q"]))
        # ONE form, ONE button (owner 2026-10-17): it stores the links / details and sends a code to the e-mail typed.  Before, "Send me
        # a code" dropped the links / registration / VAT typed in a second form, and a refused address looked like a reset.
        contact_line = ('<p class="muted">%s<a href="mailto:support@aerostator.com">support@aerostator.com</a></p>' % _e(s["support_q"])) if not pending_email else ""
        forms = (
            '%s<form method="post" action="%s"><h2>%s</h2>'
            '<label for="em">%s</label><input id="em" name="email" type="email" value="%s" autocomplete="email">'
            '<p class="muted">%s</p><h2>%s</h2>'
            '<label for="ur">%s</label><textarea id="ur" name="urls" rows="3">%s</textarea>'
            '<label for="st">%s</label><input id="st" name="website" value="%s" autocomplete="url">'
            '<label for="rg">%s</label><input id="rg" name="reg_no" value="%s">'
            '<label for="vt">%s</label><input id="vt" name="vat_no" value="%s">'
            '<label for="nt">%s</label><textarea id="nt" name="note" rows="3" maxlength="%d">%s</textarea>'
            '<input type="hidden" name="action" value="send"><button type="submit">%s</button></form>'
            '<form method="post" action="%s/document" enctype="multipart/form-data"><h2>%s</h2><p class="muted">%s</p>'
            '<input name="file" type="file" accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg" required><button type="submit">%s</button></form>%s'
        ) % (code_form, _e(base), _e(s["email_h"]), _e(s["email_label"]), _e(typed.get("email") or suggested_email), _e(s["email_go"]),
             _e(s["links_h"]), _e(s["urls_label"]), _e(typed.get("urls", "")),
             _e(s["site_label"]), _e(typed.get("website") or app.get("website", "")), _e(s["reg_label"]), _e(typed.get("reg_no") or app.get("reg_no", "")),
             _e(s["vat_label"]), _e(typed.get("vat_no") or app.get("vat_no", "")), _e(s["note_label"]), C.MAX_NOTE_CHARS, _e(typed.get("note", "")),
             _e(s["links_go"]),
             _e(base), _e(s["doc_h"]), _e(s["doc_hint"]), _e(s["doc_go"]), contact_line)
    verified = ""
    vs = [v["email"] for v in case.get("emails", {}).get("verified", [])]
    if vs:
        verified = '<p class="muted">%s: %s</p>' % (_e(s["verified_list"]), _e(", ".join(vs)))
    opn = '<a class="btn alt" href="%s">%s</a>' % (_e(open_url), _e(s["open"])) if state == "ok" and open_url else ""
    body = ('<h1>%s</h1><p>%s</p><p><b>%s:</b> %s<br><b>%s:</b> %s</p>%s%s%s%s%s' % (
        _e(s["h"]), _e(s["intro"]), _e(s["company"]), _e(app.get("legal_name", "")), _e(s["status"]), _e(_status_line(s, state)), msg, needs, forms, verified, opn))
    sw = '<p class="sw"><a href="%s">%s</a></p>' % (_e(switch_url), s["other"]) if switch_url else ""
    return ('<!doctype html><html lang="%s"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="robots" content="noindex,nofollow"><meta name="referrer" content="no-referrer"><title>%s</title><style>%s%s</style></head>'
            '<body><main><div class="card">%s<p class="brand">AeroStator</p>%s</div></main></body></html>'
            % (lang, _e(s["title"]), LP._CSS, _EXTRA_CSS, sw, body))


_EXTRA_CSS = ("h2{font-size:17px;margin:20px 0 6px}textarea{box-sizing:border-box;width:100%;padding:12px;font:inherit;border:1px solid #cbd5e1;border-radius:8px}"
              ".ok{color:#166534;font-weight:600}ol{padding-left:20px;margin:0 0 14px}li{margin:6px 0}form{border-top:1px solid #e2e8f0;margin-top:14px}"
              "button.alt{background:#fff;color:#2563eb;border:2px solid #2563eb}.no-code{margin-top:16px;border-top:1px solid #e2e8f0;padding-top:12px}")


def bad_link(lang: str) -> str:
    lang = LP.norm_lang(lang)
    msg = ("This link is not valid or has expired. Reply to our e-mail and we will send a new one." if lang == "en"
           else "此链接无效或已过期。请回复我们的邮件，我们会发送新的链接。")
    return ('<!doctype html><html lang="%s"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="robots" content="noindex,nofollow"><title>AeroStator</title><style>%s</style></head><body><main><div class="card">'
            '<p class="brand">AeroStator</p><p>%s</p></div></main></body></html>' % (lang, LP._CSS, _e(msg)))
