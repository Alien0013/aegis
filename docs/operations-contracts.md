# AEGIS Operations Contracts

This document turns maturity gaps into operational contracts that can be tested, documented, and audited.

## Session lifecycle

A session lifecycle covers creation, resume, continuation, compression, lineage, branch/fork, export, search, linked runs, and trace replay. A crash recovery flow is complete only when a later process can find the session, inspect recent context, and continue without asking the user to repeat stable context.

Contract evidence:

- `aegis/session.py`
- `aegis/session_checks.py`
- `aegis/runs.py`
- dashboard session routes
- session/search tests

## Prompt and context lifecycle

Prompt context has stable, context, and volatile sections. Context references, project files, diff/staged refs, URL refs, memory blocks, skills, platform hints, and safety text should be bounded and auditable.

## Tool execution lifecycle

A tool call flows through schema validation, permission policy, backend validation, execution, redaction/truncation, result recording, and trace/run metadata. Long-running work must be represented as a tracked process or durable job instead of disappearing into an untracked shell.

## Gateway lifecycle

A gateway message flows through adapter normalization, auth/pairing, session key resolution, busy-mode handling, agent execution, outbox delivery, retry/dead-letter state, and redacted logging.

## Cron lifecycle

A scheduled job records schedule parsing, next-run calculation, prompt/script settings, delivery target, execution status, stdout/final response, error handling, and follow-up visibility. Script-only jobs and prompt-driven jobs are distinct contracts.

## Provider lifecycle

Provider calls include model selection, auth readiness, capability matrix, credential pool selection, fallback behavior, response-state handling, cancellation, and redacted error reporting.

## Skills lifecycle

A skill moves through discovery, loading, usage tracking, management, patching, curation, archive, restore, and pin protection. Skill procedures are not generic memory facts.

## Security lifecycle

Security includes command approvals, yolo bypass as explicit operator choice, file safety, redaction, dashboard token safety, WebSocket tickets, gateway authorization, and sensitive-output controls.

## Live QA lifecycle

A live proof is separate from a local fake-adapter proof. Live records must contain sanitized evidence and must not embed credentials.
