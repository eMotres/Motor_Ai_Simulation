# Shared default40 motor — 2026-10-06

Owner requests every user see40mm by default. Existing Configure uses /api/me.defaultMotor only when no own remembered choice. Effective read-time grants add canonical shared CIANO14 40 new and fallback L12 to enabled registered users. Existing explicit defaults, grants and remembered choice remain. No users.json migration; no broad grant-all; anonymous/disabled/unknown accounts get no new baseline. Baseline requires both shared die.yaml/L12.yaml. Private motors remain gated.

Implementation: GPT-6 Luna, no model escalation. OpenRouter dispatch of auth source rejected by automatic security review; no request was sent. Owner then explicitly requested self or Luna. Luna implementation reviewed and focused tests run by orchestrator because agent interpreter access was blocked. Release based on exact deployed8392b964, separate checkout; shared owner edits preserved.

Validation and deployment results appended after verification.

Validation: 53 focused tests passed in30.22s (default40, granted loading, last-motor preference). Additional legacy access suite6passed/7failed identically against candidate and unchanged8392b964; missing D85catalog fixtures and anonymous exhibit expectation, not new regression. First collection attempt failed sibling fixture plugin import, fixed by Luna package-qualified imports. No browser end-to-end run.
