# The in-app assistant: questions, bugs and tickets

Owner decisions of 2026-10-05: the model stays Gemini Flash; **every question and
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

## The prompt follows the role

| Caller | Prompt | Also appended |
|---|---|---|
| visitor (no account) | `USER_PROMPT` | the visitor note (access-request marker), no session |
| role `user` | `USER_PROMPT`: **Motors** and **Configure** only | ticket protocol, hidden context |
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

## Ticket types

`bug`, `feature`, `question`, `account` (`ticket_store.TYPES`). The admin list
(`GET /api/admin/tickets`) is the short form; `?detail=1` adds `conversation` and `context`.

## Checks

- `pytest tests/test_support_assistant.py` — draft parsing, the confirm flow, sanitisation,
  role-aware prompts, the example exchanges (`tests/fixtures/support_assistant_exchanges.json`);
- `node --test web/src/lib/__tests__/support*.test.mjs web/src/components/support/__tests__/*.mjs`;
- `GEMINI_API_KEY=... python scripts/support_assistant_live_check.py` — the same questions against
  the live model with the same string rules (no tab the account does not have, a draft of the
  right type). It makes about nine provider calls and writes nothing.
