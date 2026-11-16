# CampusOps repository instructions

Read `docs/project-spec-v2.md`, `docs/contracts.md`, `docs/architecture.md`, and
`docs/handoff/main-bootstrap.md` (once created) before changing code.
For active managed worktree paths and the original-chat handoff, also read
`docs/handoff/managed-worktrees.md`. Earlier bootstrap paths are historical snapshots.
The sole P0 scenario is GearShift. ClubRescue is out of scope.

## Isolation and environment

- Work only in this independent D-drive project and its linked D-drive worktrees.
  Do not access, read, reference, copy or modify the user's commercial software,
  other repositories, data or worktrees. Do not change global Git/Codex/pip config.
- Python 3.12 is the supported baseline. Each worktree uses its own `.venv`,
  `.cache`, `.tmp`, and `var`; dot-source `scripts/dev-env.ps1` first.
- Preserve unknown files. Never force reset/push, force branch replacement,
  clean, delete worktrees, or delete data without explicit authorization.
- No paid API activation or fallback. Keys only in ignored `.env`/environment.
  Never log prompts containing personal data, API keys, headers or provider errors
  that could include secrets. All fixtures use fictional anonymous records.

## Ownership

| Owner | Paths |
| --- | --- |
| Main | `contracts/`, `domain/`, `storage/`, `services/` under `src/campusops/`; `bootstrap.py`, `cli.py`, root package init; `streamlit_app.py`; `tests/integration/`, `tests/conftest.py`; root config, dependencies/locks, scripts, docs |
| Main (TOTP) | `src/campusops/security/`, `maintenance/`; this feature's UI entry edits are temporarily coordinated with App UI |
| Agent Core | `src/campusops/agent_core/`, `tests/core/` |
| App UI | `src/campusops/ui/`, `tests/ui/` |

Contracts 1.7.0 are frozen by schema and signature tests, with exact 1.0.0/1.1.0/1.2.0/1.3.0/1.4.0/1.5.0/1.6.0 compatibility checks. Feature branches request
changes from Main; Main changes them and branches synchronize the resulting commit.
Do not edit another owner's paths or worktree. Shared `.git` is normal for linked
worktrees; that does not authorize editing another checkout.

## Service boundary and verification

- Agent Core and UI use `contracts/ports.py`; they never import `storage`, use
  SQL/sqlite3, open the DB path, or directly change reservations.
- Only Main `ApprovalService` updates bookings during normal operation. Seed and
  migration tools are explicit offline bootstrap exceptions. Incident registration
  marks equipment faulty and bumps snapshot revision; it does not change bookings.
- Proposals/logs go through Main services; approval rechecks current constraints
  and snapshot revision in a single transaction. Never trust LLM validation alone.
- Each run freezes agent configuration and the versioned fictional rule catalog.
  Later config edits affect new runs. Rule-content/version changes invalidate pending
  proposals through the snapshot fingerprint; do not weaken deterministic constraints.
- `NOT_IMPLEMENTED`, `UNCONFIGURED`, `TEST_INJECTED`, `DEMO_FIXTURE`, `NO_FEASIBLE_PLAN`
  must remain distinct from real LLM execution and commercial acceptance.
- Run `.venv\Scripts\python.exe -m pytest` and `-m pip check` as appropriate.
  Do not run networked AI tests automatically. Real three-agent/parallel/conditional/
  bounded-replan tests remain integration acceptance until actually exercised.
- Main bootstraps M0/M1 and then stops for the two feature chats; do not implement
  their full workflow or UI here. No sub-agent delegation is required by this file.
- Handoff includes branch, SHA, tests, actual behavior, pending work and blockers.
  Main merges reviewed feature commits with `--no-ff` and runs integration tests.
- The integrated Main launcher is `streamlit_app.py`, using
  `bootstrap.create_configured_application()`. The original `create_application()`
  remains the explicit-injection contract. A missing opt-in/client is a controlled
  unavailable-provider result, never a production mock fallback.

## Independent reviewer branch scope

This branch is RC15-Review-1 (0.2.0rc15.post1), explicitly authorized by the owner. Its normal entries use isolated reviewer data without personal TOTP. Original locked Main/runtime/history remain unchanged. Do not add a runtime authentication-disable flag to the original app. Business contracts, approval, quotas and deterministic validation stay frozen. No cloud model requests during development.
