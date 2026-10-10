# Claude API agent helper

`scripts/Invoke-ClaudeApiAgent.ps1` runs one bounded `claude -p` (Claude Code 2.1.295) request from Windows PowerShell 5.1, billed to the Anthropic **API** key.

**Hardening status 2026-10-09:** parser, three forbidden ReadPaths refusals and explicit empty tools-argument check passed. Live read-only call with approved inline document returned HARDENED_READONLY_OK (Haiku5.5, exit0). ReadWrite and cancellation transport tests were not repeated after hardening; prior write checks belong to the previous version. Synthetic transport remains blocked by Windows Application Control, not bypassed. Implementation worker reported Codex GPT-5; root verified. No FEM/solver rights.

## Smoke test

Expected success = the output contains `API_AGENT_OK` plus a `[meta] model=... cost_usd=... turns=...` line:

```powershell
cd C:\Users\vadim\Projects\motor_ai_sim
powershell -File scripts\Invoke-ClaudeApiAgent.ps1 -Prompt "Reply exactly: API_AGENT_OK"
```

## Usage

Read-only (default; no filesystem tools; approved rules/context are supplied inline):

```powershell
powershell -File scripts\Invoke-ClaudeApiAgent.ps1 -Prompt "Summarize the supplied file in 3 lines" -ReadPaths docs/claude-api-agent.md
powershell -File scripts\Invoke-ClaudeApiAgent.ps1 -PromptFile .\task.txt -Model haiku
```

ReadWrite (explicit files only):

```powershell
powershell -File scripts\Invoke-ClaudeApiAgent.ps1 -Prompt "Fix typos" -ReadWrite -OwnPaths docs/notes.md
```

## Parameters

| Parameter | Rule |
|---|---|
| `-Prompt` / `-PromptFile` | mutually exclusive, one required |
| `-Model` | `haiku`, `sonnet` (default), `opus` |
| `-MaxBudgetUsd` | decimal, 0.01..20, default 1 |
| `-MaxTurns` | 1..100, default 12 |
| `-TimeoutSeconds` | 30..3600, default 600 |
| `-ReadWrite` | needs `-OwnPaths` (mandatory with it) |
| `-OwnPaths` | explicit project-relative files. Whitelist: ASCII letters, digits, `_ - . /` and space only (so no control chars, parentheses, `#`, wildcards, unicode). No `..`, absolute paths, directories, `.git`, `.claude`, `.codex`, `config`, `deploy`, private, secrets/credentials, `.env*`, key/cert files. Any existing path component (root down) that is a reparse point/junction/symlink is rejected |
| `-PromptFile` | same validator and forbidden locations; must be an existing file inside the project root (real path), never a secret/credential/.env/.git/.claude/.codex/config/deploy/private-key file or reparse-point location |

## Behavior

- The working directory is always the project root (parent of `scripts/`), never the caller's cwd.
- `claude.exe` is pinned to `%USERPROFILE%\.local\bin\claude.exe` to prevent PATH shadowing. This does not independently verify Windows signature or publisher.
- Fixed flags: `-p --bare --restricted --permission-mode dontAsk --no-session-persistence --strict-mcp-config --mcp-config {"mcpServers":{}} --setting-sources ""` plus model, budget, turns, `--output-format json`, inline settings and `--tools ""` for read-only calls. ReadWrite adds only exact Edit/Write rules for `-OwnPaths`.
- Stdin carries the prompt plus project rules read in-process from `AGENTS.md`, `CLAUDE.md`, `C:\Users\vadim\Projects\AGENT_RULES_motor_ai_sim.md` and `AGENT_DATASET_RULES.md` (missing file = fail closed), plus shared-workspace constraints: path ownership for the task, dirty shared branch, API 8001 GET only, no deploy/FEM/git/ERP/Drive/secrets, no nested agents.
- Output: only the final result and model/cost/turns metadata. `is_error` or a nonzero exit is rejected. Raw JSON/debug is never printed or written to disk. No automatic retry; no session resume.
- Timeout kills only the child's own tree with `taskkill /PID <id> /T /F`. On success, error or timeout, the script disposes its own process handles once those processes have exited; no broad process killing.

## API key and billing

- API usage is billed separately from any Claude subscription.
- The key is read from the current process `ANTHROPIC_API_KEY`, else the existing **User** environment variable (preferred place for it). Absent = fail closed. Never put the key in text, files, arguments or settings.
- The key is set only in the child process environment; the parent and persistent environments are not modified. The exact key value is redacted from stdout/stderr before anything is printed. If the assembled stdin text (or settings) contains the exact key, the run fails before starting the child; the prompt is never silently redacted and the key is never sent as task text.
- The child environment is rebuilt from an allow-list and then explicit Anthropic/safety variables are set. Parent proxy, `NODE_OPTIONS`, custom CA, provider overrides and unrelated `ANTHROPIC_*` variables are not inherited.

## Permissions vs. sandbox

ReadWrite adds `Edit`/`Write` tools, but `dontAsk` mode permits only exact `permissions.allow` entries generated from `-OwnPaths`; all else is denied. These are Claude Code **permission rules, not an OS sandbox**. Approved existing files are read by the wrapper and supplied inline. Timeout cleanup reports success only after `taskkill` and child exit are confirmed; final cleanup makes a best-effort stop of the helper's own child.

## Scope

Research/editing helper for small bounded tasks only. No shell, MCP, web or sub-agent tools. Not a deployment, FEM or ERP path.
