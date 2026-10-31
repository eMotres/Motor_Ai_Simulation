# The in-app assistant: questions, bugs and tickets

Owner decisions of 2026-10-05: **every question and
every bug goes through the assistant**. The user just writes. The assistant answers
what it can, asks for what is missing, and when the message is a bug, a feature
request, an account problem or something it cannot answer, it prepares a ticket that
the user checks, edits and sends. There is no separate report form.

```
user writes ─▶ POST /api/support/chat {messages, context}
                  │  role from the token  ─▶ prompt for the role (support_prompt.py)
                  │  context sanitised    ─▶ hidden "Session context" block
                  ▼
               provider (one plain text completion)
                  │  reply may end with  [[TICKET_DRAFT {"type","title","description"}]]
                  ▼
               parse_ticket_draft: marker stripped, returned as `ticketDraft`
                  │  (signed-in callers only; a visitor never gets one)
                  ▼
widget shows a draft card ─▶ user edits ─▶ Send ─▶ POST /api/support/tickets
                                                   {type, title, description, conversation, context}
                                                   ─▶ ticket_store (conversation + context kept)
                                                   ─▶ Admin → Logs → Support tickets (click a row)
```

Nothing is filed by the model. The only write is the user's own Send.

## Provider and training (owner decision D3, 2026-10-31)

The 2026-10-05 decision "the model stays Gemini Flash" is withdrawn. The unpaid Gemini API
tier lets Google use the content to improve its products (and its terms allow only paid
services for EEA users); paid Gemini is not allowed for us. The help assistant and the
specification assistant therefore run on **Anthropic's commercial API** (inputs are not
used for training).

- `ANTHROPIC_API_KEY` - the key (already read by the code).
- `ANTHROPIC_SUPPORT_MODEL` - the model, default `claude-haiku-5-5`.
- Provider order is unchanged: Firestore `config/ai.provider` > `SUPPORT_PROVIDER` env >
  automatic. Automatic now prefers Anthropic (it used to prefer Gemini).
- **Gemini guard.** Gemini stays in the code, but when the effective provider is Gemini and
  the key is not explicitly marked paid (`GEMINI_PAID_TIER=1`), it is refused: one warning in
  the log, then Anthropic if an Anthropic key exists, else the "assistant unavailable" path
  (the flagged demo reply). The guard applies whatever chose Gemini (admin override, env,
  automatic) and to the specification assistant, which shares `_effective()`. The Admin
  status shows `geminiBlocked: true` in that case.
- `scripts/support_assistant_live_check.py` calls Gemini directly and is a developer check
  only; do not point it at user content.

**Server env change after deploy (orchestrator):**

1. Set `ANTHROPIC_SUPPORT_MODEL=claude-haiku-5-5` and make sure `ANTHROPIC_API_KEY` is set
   (the commercial key, not a consumer one).
2. Check Firestore `config/ai`: if `provider` is `gemini`, clear it (or set `anthropic`), and
   clear any `anthropic_model` override that names an older model.
3. `GEMINI_API_KEY` / `GOOGLE_API_KEY` / `GEMINI_MODEL`: remove them. If they stay, nothing
   calls Gemini (no `GEMINI_PAID_TIER=1`), but a stale key on the server is a liability.
   Never set `GEMINI_PAID_TIER=1` unless the owner buys a paid tier, which is not planned.
4. Restart the API and open Admin -> Support settings: provider `anthropic`, the model
   `claude-haiku-5-5`, configured.

The model also no longer receives engineering data: see "What is attached" below.

## The prompt follows the role

| Caller | Prompt | Also appended |
|---|---|---|
| visitor (no account) | `USER_PROMPT` | the visitor note (access-request marker), no session |
| role `user` | `USER_PROMPT`: **Motors**, **Configure** and **3D model** only | ticket protocol, hidden context |
| `admin` / `staff` | `STAFF_PROMPT`: every tab, held to `web/src/App.tsx` | ticket protocol, hidden context |

A custom prompt saved in Admin (config/ai.system_prompt) still replaces the default for
everyone; the ticket protocol is appended to it anyway. The Configure description is
read off `ConfiguratorPanel.tsx` and `locales/en/controller.json`;
`tests/test_support_assistant.py` checks every bold label in it against the panel's own
strings, so a renamed button cannot rot into a wrong answer.

## What is attached to a chat message and a ticket

The widget builds a compact snapshot (`web/src/lib/supportContext.ts`):

- the current tab, the interface language, the app version and deployed commit, the browser;
- while the user is on Configure: the machine / die / configuration, the preset, the knob
  values, drive (Sine or PWM, transistor, PWM frequency), propeller and ambient, the battery
  pack, the key result tiles, and the red lines (refusals, overheating, slot overflow);
- the last ten failed calls to our own API: method, path **without its query**, status and a
  clipped error message.

It never reads a header, a token, a cookie, a request body or another user's data. The server
sanitises it again (`src/motor_ai_sim/support_context.py`): whitelisted top-level keys only,
keys that name a secret dropped, credential-shaped strings and e-mail addresses scrubbed,
bounded size. The caller's role and the deployed build are added by the server. The user is
told on the draft card that the conversation and the screen state are attached.

**What the model sees is less than what a ticket stores.** Before the snapshot goes into the
prompt, `support_context.model_view` reduces it to the page name, the language, the app
build, the browser, whether Configure is open, and for each failed API call only the method,
path (no query), status and age. The machine / die / configuration, the knob values, the
result tiles, the warnings and the error messages never reach a model provider. A ticket the
user confirms still stores the full sanitised snapshot in our own store. The assistant asks
the user for any number it needs.

**Every assistant says it is an AI.** The line "AI assistant - answers can be wrong."
(`aiNotice` in `common.json`, with the zh-CN wording) is shown above the first message of the
help widget, in the specification assistant panel and in the design assistant.

## Ticket types

`bug`, `feature`, `question`, `account` (`ticket_store.TYPES`). The admin list
(`GET /api/admin/tickets`) is the short form; `?detail=1` adds `conversation` and `context`.

## Checks

- `pytest tests/test_support_provider_guard.py tests/test_assistant_ai_notice.py` — the provider guard and the AI notice;
- `pytest tests/test_support_assistant.py` — draft parsing, the confirm flow, sanitisation,
  role-aware prompts, the example exchanges (`tests/fixtures/support_assistant_exchanges.json`);
- `node --test web/src/lib/__tests__/support*.test.mjs web/src/components/support/__tests__/*.mjs`;
- `GEMINI_API_KEY=... python scripts/support_assistant_live_check.py` — the same questions against
  the live model with the same string rules (no tab the account does not have, a draft of the
  right type). It makes about nine provider calls and writes nothing.
