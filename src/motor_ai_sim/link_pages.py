"""Server-rendered pages for the links in the account mails: NO JavaScript, no external asset, inline CSS, well under 10 kB.

Why: a visitor in China (2026-10-15) clicked the confirmation link but the single-page app (a ~1.2 MB bundle) aborted mid-transfer on
the China -> Germany link, so the browser-side confirmation call was never made.  The link now opens a server route that
confirms the address itself and answers with one of these tiny pages (EN + zh-CN), so it works in any in-app browser (WeChat, QQ),
on a slow line, and with scripts disabled.  The forms are plain HTML POSTs.

Pages: ``verified`` (confirmed, or already confirmed), ``invalid`` (expired / used / forged, with a resend form), ``reset_form``
(new password + repeat), ``reset_done``, ``sent`` (the generic answer after a resend request).  Every dynamic value is escaped.
"""
from __future__ import annotations

import html
import re
from typing import Optional

#: mail domains whose owners are, with near certainty, Chinese readers (used only when no language is stored)
CN_MAIL_DOMAINS = frozenset("""qq.com vip.qq.com foxmail.com 163.com 126.com yeah.net 188.com vip.163.com vip.126.com sina.com sina.cn
sohu.com aliyun.com aliyun.cn 139.com 189.cn wo.cn 21cn.com tom.com 263.net yahoo.cn yahoo.com.cn msn.cn""".split())

STRINGS = {
    "en": {
        "brand": "AeroStator",
        "ok_title": "E-mail confirmed",
        "ok_head": "E-mail confirmed ✓",
        "already_head": "Already confirmed ✓",
        "ok_text": "Your address {email} is confirmed. You can use everything now.",
        "open": "Open AeroStator",
        "signin": "Sign in",
        "cont": "Continue connecting your AI app",
        "open_next": "Open the page you came for",
        "bad_title": "Link expired",
        "bad_head": "This link has expired or was already used",
        "bad_text": "Enter your e-mail address and we will send a new link.",
        "email": "E-mail address",
        "resend": "Send a new link",
        "sent_title": "Check your inbox",
        "sent_head": "Check your inbox",
        "sent_text": "If this address can be used, we sent it a new link. Not there? Look in the spam folder.",
        "reset_title": "Set a new password",
        "reset_head": "Set a new password",
        "reset_text": "Choose a new password for {email}. Setting it signs out every other session.",
        "pw": "New password (at least 10 characters)",
        "pw2": "Repeat the new password",
        "reset_go": "Set password",
        "mismatch": "The two passwords do not match.",
        "reset_done_head": "Password changed ✓",
        "reset_done_text": "Your password is set and your e-mail is confirmed. Sign in with the new password.",
        "bad_reset_head": "This reset link has expired or was already used",
        "bad_reset_text": "Enter your e-mail address and we will send a new reset link.",
        "resend_reset": "Send a new reset link",
        "policy_hint": "At least 10 characters; common passwords are refused.",
        "other": "中文",
    },
    "zh-CN": {
        "brand": "AeroStator",
        "ok_title": "邮箱已确认",
        "ok_head": "邮箱已确认 ✓",
        "already_head": "邮箱已确认 ✓",
        "ok_text": "您的邮箱 {email} 已确认，现在可以使用全部功能。",
        "open": "打开 AeroStator",
        "signin": "登录",
        "cont": "继续连接您的 AI 应用",
        "open_next": "打开您要访问的页面",
        "bad_title": "链接已失效",
        "bad_head": "此链接已过期或已使用过",
        "bad_text": "请输入您的邮箱地址，我们会发送新的链接。",
        "email": "邮箱地址",
        "resend": "发送新链接",
        "sent_title": "请查收邮件",
        "sent_head": "请查收邮件",
        "sent_text": "如果该地址可用，我们已向其发送新链接。没收到？请查看垃圾邮件文件夹。",
        "reset_title": "设置新密码",
        "reset_head": "设置新密码",
        "reset_text": "请为 {email} 设置新密码。设置后其他所有登录会话将退出。",
        "pw": "新密码（至少 10 个字符）",
        "pw2": "再次输入新密码",
        "reset_go": "设置密码",
        "mismatch": "两次输入的密码不一致。",
        "reset_done_head": "密码已修改 ✓",
        "reset_done_text": "密码已设置，邮箱也已确认。请用新密码登录。",
        "bad_reset_head": "此重置链接已过期或已使用过",
        "bad_reset_text": "请输入您的邮箱地址，我们会发送新的重置链接。",
        "resend_reset": "发送新的重置链接",
        "policy_hint": "至少 10 个字符，不能是常见密码。",
        "other": "English",
    },
}

_CSS = ("body{margin:0;background:#f4f5f7;color:#1f2937;font:16px/1.55 -apple-system,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',Arial,sans-serif}"
        "main{max-width:460px;margin:0 auto;padding:28px 18px}.card{background:#fff;border-radius:10px;padding:26px 22px;box-shadow:0 1px 3px rgba(0,0,0,.12)}"
        ".brand{font-weight:700;font-size:18px;color:#0f172a;margin:0 0 18px}h1{font-size:22px;margin:0 0 10px}p{margin:0 0 14px}"
        "a.btn,button{display:block;box-sizing:border-box;width:100%;text-align:center;padding:14px 16px;margin:10px 0;border:0;border-radius:8px;"
        "background:#2563eb;color:#fff;font-size:17px;font-weight:700;text-decoration:none;cursor:pointer}a.btn.alt{background:#fff;color:#2563eb;border:2px solid #2563eb}"
        "label{display:block;font-size:14px;margin:10px 0 4px;color:#475569}input{box-sizing:border-box;width:100%;padding:12px;font-size:16px;border:1px solid #cbd5e1;border-radius:8px}"
        ".err{color:#b91c1c;font-weight:600}.muted{color:#64748b;font-size:13px}.sw{text-align:right;font-size:13px;margin:0 0 6px}")


def norm_lang(lang: Optional[str]) -> str:
    t = str(lang or "").strip().lower().replace("_", "-")
    return "zh-CN" if t == "zh" or t.startswith("zh") else "en"


def mail_domain_is_chinese(email: str) -> bool:
    d = str(email or "").rpartition("@")[2].strip().lower()
    return d in CN_MAIL_DOMAINS or d.endswith(".cn")


def pick_lang(*, query: str = "", stored: str = "", accept_language: str = "", email: str = "") -> str:
    """The page language: an explicit ``?lang=`` wins; a stored Chinese choice; the browser's Accept-Language (first tag); a Chinese
    mail provider (qq.com ...); else English.  (A stored "en" is not trusted over the browser: the sign-up form always sends the
    interface language, whatever the reader prefers.)"""
    if query:
        return norm_lang(query)
    if stored and norm_lang(stored) == "zh-CN":
        return "zh-CN"
    for part in str(accept_language or "").split(","):
        tag = part.split(";")[0].strip().lower()
        if tag:
            return norm_lang(tag)
    return "zh-CN" if mail_domain_is_chinese(email) else "en"


def _e(x) -> str:
    return html.escape(str(x), quote=True)


def _page(lang: str, title: str, body: str, switch_url: str = "") -> str:
    s = STRINGS[lang]
    sw = f'<p class="sw"><a href="{_e(switch_url)}">{s["other"]}</a></p>' if switch_url else ""
    return ('<!doctype html><html lang="%s"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="robots" content="noindex,nofollow"><meta name="referrer" content="no-referrer"><title>%s</title><style>%s</style></head>'
            '<body><main><div class="card">%s<p class="brand">%s</p>%s</div></main></body></html>'
            % (lang, _e(title), _CSS, sw, s["brand"], body))


def verified(lang: str, email: str, *, already: bool = False, open_url: str, signin_url: str, continue_url: str = "", switch_url: str = "",
             open_label: str = "") -> str:
    s = STRINGS[norm_lang(lang)]
    lang = norm_lang(lang)
    body = ('<h1>%s</h1><p>%s</p><a class="btn" href="%s">%s</a>' % (_e(s["already_head"] if already else s["ok_head"]),
            _e(s["ok_text"].format(email=email)), _e(continue_url or open_url), _e(s["cont"] if continue_url else s[open_label or "open"])))
    body += '<a class="btn alt" href="%s">%s</a>' % (_e(signin_url), _e(s["signin"]))
    return _page(lang, s["ok_title"], body, switch_url)


def invalid(lang: str, *, kind: str = "verify", email: str = "", action: str, switch_url: str = "") -> str:
    """Expired / used / forged link, with a plain-HTML form that asks for a new one (works without JavaScript)."""
    lang = norm_lang(lang)
    s = STRINGS[lang]
    reset = kind == "reset"
    head, text = (s["bad_reset_head"], s["bad_reset_text"]) if reset else (s["bad_head"], s["bad_text"])
    body = ('<h1>%s</h1><p>%s</p><form method="post" action="%s"><input type="hidden" name="kind" value="%s">'
            '<label for="e">%s</label><input id="e" name="email" type="email" required autocomplete="email" value="%s">'
            '<button type="submit">%s</button></form>'
            % (_e(head), _e(text), _e(action), "reset" if reset else "verify", _e(s["email"]), _e(email),
               _e(s["resend_reset"] if reset else s["resend"])))
    return _page(lang, s["bad_title"], body, switch_url)


def sent(lang: str, *, open_url: str) -> str:
    lang = norm_lang(lang)
    s = STRINGS[lang]
    body = '<h1>%s</h1><p>%s</p><a class="btn alt" href="%s">%s</a>' % (_e(s["sent_head"]), _e(s["sent_text"]), _e(open_url), _e(s["open"]))
    return _page(lang, s["sent_title"], body)


def reset_form(lang: str, *, email: str, action: str, fields: dict, error: str = "", switch_url: str = "") -> str:
    """The new-password form.  ``fields`` are the hidden inputs that identify the link (``id`` or ``t``)."""
    lang = norm_lang(lang)
    s = STRINGS[lang]
    hidden = "".join('<input type="hidden" name="%s" value="%s">' % (_e(k), _e(v)) for k, v in fields.items())
    err = '<p class="err" role="alert">%s</p>' % _e(error) if error else ""
    body = ('<h1>%s</h1><p>%s</p>%s<form method="post" action="%s">%s'
            '<label for="p1">%s</label><input id="p1" name="password" type="password" required minlength="10" autocomplete="new-password">'
            '<label for="p2">%s</label><input id="p2" name="password2" type="password" required minlength="10" autocomplete="new-password">'
            '<p class="muted">%s</p><button type="submit">%s</button></form>'
            % (_e(s["reset_head"]), _e(s["reset_text"].format(email=email)), err, _e(action), hidden, _e(s["pw"]), _e(s["pw2"]),
               _e(s["policy_hint"]), _e(s["reset_go"])))
    return _page(lang, s["reset_title"], body, switch_url)


def reset_done(lang: str, *, signin_url: str, open_url: str) -> str:
    lang = norm_lang(lang)
    s = STRINGS[lang]
    body = ('<h1>%s</h1><p>%s</p><a class="btn" href="%s">%s</a><a class="btn alt" href="%s">%s</a>'
            % (_e(s["reset_done_head"]), _e(s["reset_done_text"]), _e(signin_url), _e(s["signin"]), _e(open_url), _e(s["open"])))
    return _page(lang, s["reset_title"], body)


def policy_message(lang: str, english: str) -> str:
    """The password-policy refusal for the form: the server's English sentence, with a Chinese lead for zh readers."""
    if norm_lang(lang) == "zh-CN":
        return "密码不符合要求：至少 10 个字符，且不能是常见密码。 (%s)" % re.sub(r"\s+", " ", english)[:160]
    return english
