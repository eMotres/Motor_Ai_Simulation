# Account mail (confirmation and password reset)

## Which address the mails go out from

`auth_email.mail_from()`: env **`MAIL_FROM`**, else **`NOTIFY_FROM`**, else **`SMTP_USER`**.  All three are read from the API's
environment (`/etc/motres/api.env` on the server), never from the repo.  The mails are sent over SMTP with `SMTP_USER` /
`SMTP_PASS` (`SMTP_HOST`, `SMTP_PORT`; default smtp.gmail.com:587).  With only the Gmail login set (the setup in `notify.py`'s
docstring) the sender is the login mailbox itself, i.e. the owner's own address, and every bounce lands in that inbox.  To send from
a dedicated address set `MAIL_FROM=no-reply@...` (the SMTP account must be allowed to send as it; with Google Workspace add it as an
alias / "send as" of the login account) and restart the API.  The announcement mailing (`scripts/send_announcement.py`) uses the
same `mail_from()`.

## A mistyped domain is refused before anything is sent

`email_domain.py` (stdlib only): on e-mail sign-up, password-reset request and the address fixes below, the domain of the address
must exist.  `getaddrinfo` (A / AAAA, 2 s limit) resolves it: fine.  Otherwise one minimal UDP DNS query (type A) to the resolver in
`/etc/resolv.conf` reads only the response code: NXDOMAIN refuses ("The domain movigor.tech does not exist - please check the
address"), a name that exists without an address (an MX-only domain) is accepted.  A timeout, a resolver error or no resolver
(a Windows workstation) never blocks: the mail is sent.  Obvious typos of big providers get "did you mean" (gmial.com -> gmail.com,
qq.con -> qq.com, 163.con -> 163.com); company domains are never guessed.  The refusal is a 422 with the code
`auth.email_domain_unknown` (or `..._suggest`) so the web shows it in EN / zh-CN; it depends on the input alone, so it does not
reveal whether an account exists.  Verdicts are cached for ten minutes; MX records are not queried.

## Fixing a wrong address

* "Check your inbox" screen after sign-up: **Resend the link** (`POST /api/auth/register/resend`, 3 an hour per address and the
  per-IP limit, the same answer whatever the address is; the button also waits 60 s) and **Wrong address? Change e-mail**
  (`POST /api/auth/register/change-email`: current e-mail + the password it was registered with + the new e-mail).  Only an
  unconfirmed account can change; failures count against the sign-in limits; a new address that already has an account changes
  nothing and gets the usual "someone tried to register" note.  The old links die with the old address; what was ticked in the
  sign-up e-mail preferences follows the account (new `email_change` history lines).
* Admin > Users: an unconfirmed account shows an "unverified" chip with the last mail ("confirmation link sent 14:02 (accepted by
  the mail server; a later bounce is not tracked)" or "FAILED ..."), and its drawer can change the address
  (`PATCH /api/auth/users/{email}` with `new_email`); the new address is mailed a confirmation link.  The last mail is recorded on the
  account (`last_mail` in `users.json`: kind, time, accepted or refused by SMTP).

## The links in the mails need no JavaScript (2026-10-15)

A visitor in China clicked the confirmation link, but the single-page app's ~1.2 MB bundle aborted twice on the China -> Germany line,
so the browser-side confirmation call was never made.  The mails now carry a short link to a SERVER route and the server does the
work:

* Confirm: `GET /api/auth/link/v/<16-char id>` (about 55 characters, on a line of its own).  The server confirms the address and
  answers with a tiny self-contained page (`link_pages.py`: inline CSS, no script, no external asset, about 2 kB, EN or zh-CN) with
  "E-mail confirmed" and buttons Open AeroStator / Sign in.  A second click, or a mail scanner that opened it first, shows "Already
  confirmed".  Expired, used or forged: the same small page with a plain HTML form (POST `/api/auth/link/resend`, 3 an hour per
  address) that asks for a new link.  A sign-up started in an AI app's authorization window also resumes that authorization (the
  page's button returns to the consent page).
* Reset: `GET /api/auth/link/r/<id>` shows a plain HTML form (new password + repeat, POST `/api/auth/link/reset`); the link is
  burnt only when the password is accepted, and every other session is signed out.
* Only the SHA-256 of an id is stored (on the account, up to three live ones per purpose, so a resend does not kill the first mail).
* Old mails: nginx redirects `/?verify=<token>`, `/?reset=<token>` and the old consent-page confirmation to
  `/api/auth/link/verify|reset?t=...` before any JavaScript (`deploy/nginx.conf`); the single-page app's handler and the JSON
  endpoints (`/api/auth/verify`, `/api/auth/reset/confirm`, which also accept a short id) stay as a fallback.
* Language of a page: `?lang=` (the page has an "English / 中文" switch), a stored Chinese choice, the browser's Accept-Language,
  a Chinese mail provider, else English.  The mails carry the Chinese lines under the English ones when the account language is
  Chinese or the address is at qq.com / 163.com / 126.com / sina.com / aliyun.com / foxmail.com ... (or any .cn).
