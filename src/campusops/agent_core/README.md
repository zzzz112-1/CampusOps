# Agent Core A0–A3 / GearShift

This package implements the frozen `OrchestrationPort` and `ModelClientPort` from
CampusOps contracts 1.2.0, preserving the original 1.0.0/1.1.0 execution interfaces.
Main explicitly composes it; importing the package does not read a key, instantiate
an SDK client, initialize a database or call a model.

## Composition owned by Main

```python
from campusops.agent_core import GearShiftOrchestrator, LLMAdapter
from campusops.bootstrap import create_application
from campusops.contracts.models import ExecutionMode

# Only after the operator manually configures a single HTTPS endpoint, model,
# key, free quota / billing protection, and explicitly enables real execution.
client = LLMAdapter.from_env()
service = create_application(
    orchestration=GearShiftOrchestrator(),
    model_client=client,
    execution_mode=ExecutionMode.REAL_API,
    provider="openai_compatible",
    model_id=client.settings.model_id,
)
```

The default Main factory remains unchanged. There is no automatic adapter binding,
provider switch, key acquisition, paid activation, seed, reset or approval here.
Core returns Main's `PlanResult` with `proposal_id=None`. Main validates and stores
the proposal and exclusively controls all booking writes and approval transactions.

## A0: provider and budgets

Required environment variables are `MODEL_API_KEY`, `MODEL_BASE_URL`, `MODEL_ID`,
and `CAMPUSOPS_LLM_ENABLED=true`. `scripts/dev-env.ps1` deliberately resets the last
flag to false, so an operator must enable it AFTER dot-sourcing that script.
`.env` is never loaded by this package. URLs must be HTTPS without embedded
credentials, a query or fragment. The selected endpoint must support Chat
Completions and strict JSON Schema output; compatibility requires real testing.

Optional limits are `MODEL_CALLS_PER_RUN_MAX=6`, `MODEL_DAILY_CALLS_MAX=30`,
`MODEL_TIMEOUT_SECONDS=30`, `MODEL_MAX_OUTPUT_TOKENS=1600`, `REPLAN_MAX=2` (0–2).
`MODEL_THINKING=default|disabled|enabled` is a strictly validated manual setting.
Default omits provider-specific fields; disabled/enabled passes only
`extra_body={"thinking":{"type":...}}`. No provider/model detection or automatic
fallback is performed, and the token/call caps are not increased. The Ark setup
selected for supervised acceptance explicitly uses disabled; real compatibility
for this RC6 conversation revision remains NotRun until newly authorized execution.
Earlier fixed-version real acceptance remains historical evidence, not an RC6 rerun.
The SDK has `max_retries=0` and HTTP redirects are disabled. A 429, timeout, refusal, incomplete response, bad
structure or provider exception is a controlled failure, never a fallback success.
Only safe HTTP status numbers are added to error reasons, never provider error
bodies. Invalid non-finite JSON schemas fail locally before any reservation.
`UNCONFIGURED` and `NO_FEASIBLE_PLAN` are explicit reasons within the frozen error
codes/statuses, not additions to Main's public enums.

Before a provider attempt, `BudgetLedger` reserves and fsyncs one entry in this
worktree's ignored `var/model-budget/usage.jsonl`, with a separate OS lock file.
Editable imports anchor that path to their verified `src/campusops/agent_core`
project, even if cwd changes. Installed `site-packages` code instead requires cwd
to be the explicit CampusOps project root, or a trusted caller to supply the
private `BudgetLedger(project_root=absolute_path)` argument. The root must be on
D on Windows, have the CampusOps project identity in pyproject.toml and the
AGENTS/spec markers. No parent-directory search or fallback into the installation
venv/Lib or C drive is performed. Invalid/missing context fails before creating
any quota directory. Editable callers cannot redirect the ledger into a different
project with the private argument. Main's root-directory launch needs no new
bootstrap or environment setting. Changing installation mode in the same project
therefore keeps its existing ledger location; no ledger is copied or reset.

This Core-private ledger contains only UTC day, run ID and counters. It is
independent of the resettable fictional business database. New adapter instances,
processes and demo database resets cannot reset its counts. Timeouts and uncertain
attempts retain their reservation; quota is never refunded automatically. Invalid
or partially written journals and lock/storage failures stop calls. The package
never repairs, truncates, deletes or rotates a journal. Trusted callers may choose
a different directory only inside this worktree, e.g. isolated test scratch.

Default discovery remains per project root; a fresh extraction is not automatically
the existing authority. For final acceptance Main must use installed code and
explicitly inject `BudgetLedger(project_root=existing_authority_root)` into the
adapter, verify the expected existing journal before enabling real execution,
and reuse its original lock and append-only bytes. The directory still must be
inside that validated D-drive CampusOps authority; editable cross-root redirection
remains forbidden. No copying, new extraction-local counter or guard relaxation
is needed. Core tests this only with fictional temporary projects and does not
read/configure Main's real journal. Main owns enforcing this composition at the
real desktop entry; the default factory alone does not provide cross-root quotas.

UTC-day buckets do not delete old journal entries or cross-day run totals. This
is not an OS-clock tamper detector: Main must pin the authorized batch/run/date,
stop across a changed date, and never treat a date change or leftover quota as
new model-call authorization.

Core also checks Main's current real-call usage before every model invocation.
The journal synchronizes adapters using this worktree, not different installations,
worktrees or other clients sharing a provider account. Account-wide billing limits
remain the operator's responsibility. The per-run budget takes precedence over the
replan limit: initial impact/planner/verifier needs three requests; two complete
verifier-feedback loops need seven, so the default six-call limit can stop first.
Every request, including tool follow-ups, retains `max_completion_tokens=1600`
by default. With six calls the requested aggregate output allowance is at most
9600 tokens, assuming the configured provider honors that parameter. This is
not an input-token bound, an actual-usage measurement, or a price guarantee.
Provider-supplied usage is retained even when role JSON validation fails;
missing usage remains unknown, never zero or an invented currency amount.

`input_limits.py` bounds canonical ASCII-escaped JSON UTF-8 to 1 MiB, both for
each complete `ModelRequest` and for the SDK completion arguments. Initial and
cumulative follow-up inputs are checked before invocation/reservation; oversized
input fails rather than truncates, retries or increases a cap. Start events record
the Core request's counted bytes and limit. These conservative serialized-input
limits use no tokenizer and do not claim exact provider input-token counts.

Contract 1.1.0 adds `ModelBudgetPort`. `LLMAdapter.get_model_budget(run_id)` reads
the same ledger under the same process/OS lock as reserve; the observation time is
captured inside that lock. AVAILABLE returns known limits, cross-day run totals,
current UTC-day totals and clamped remaining reservations. UNAVAILABLE returns
None for all counters with a controlled error. Missing journal means zero known
reservations; reads never create, repair or append `usage.jsonl`, though lock
metadata may be created for coordination. Main validates the run and delegates
the query; UI never reads the journal. Reservations are not provider balance,
tokens, successful responses, or actual HTTP attempt counts.

Core additionally fixes one task deadline when `RunContext` is created, using
the monotonic clock. Private `CoreSettings.task_timeout_seconds` defaults to
180 seconds and accepts positive finite values up to 3600; no new environment
variable or public request field is added. Scope reads, tools, model requests,
solver results, follow-ups and replans share that deadline; none reset it. Core
checks before work and before accepting returned data, and checks the final graph
result before returning a plan. Expiry returns FAILED/TASK_DEADLINE_EXCEEDED and
takes precedence over a local follow-up/replan allowance. Audit/accounting may
still record facts after expiry: actually dispatched requests keep their counts,
reported usage and quota reservations, while late valid answers are discarded.

This is cooperative stopping, not instantaneous cancellation at 180 seconds.
An in-flight HTTP request or synchronous readonly operation cannot be preempted
by these checks; its return or existing timeout is still needed. The unchanged
SDK timeout (default 30 seconds) has request/IO semantics and is never extended
for a follow-up. It does not prove an absolute wall-clock cancellation bound for
arbitrary injected clients/ports. Main queueing and human approval remain outside
this Core execution clock.

## A1: capabilities and permission checks

The actual Main registry selects an enabled agent by role, skill and required
tool subset, then stable agent ID. Task priority and satisfied dependencies govern
execution. Unsupported tool permissions block eligibility. Alternatives are used
only when they really exist in the registry. Main's published `ROLE_TOOL_LIMITS`
is an additional upper bound; registry entries cannot expand a role's authority.
Required permissions match mandatory execution: three impact reads, planner
candidate queries and validation, and the verifier's five independent reads/checks.
Audit markers are optional. Reducing an optional tool changes the advertised
schema; removing a mandatory tool blocks that route.
The selected complete configurations
are kept in graph state; registry and routing hashes, selected agent, priority,
dependencies and rejection counts are audited. Prompt version changes change the
actual system prompt. The registry is captured once per run, and Main's frozen
readonly port checks the same configuration for tools and audit appends. Later
enabled/permission/prompt edits affect new runs only; ongoing work keeps its
original configuration. Resource and rule freshness checks remain Main-owned.

`prompts.py` is the packaged prompt catalog. v1 and v2 have distinct role bodies,
not just different labels; unsupported versions fail before any model request.
Each `core.model_start` records the frozen version and SHA-256 of the exact UTF-8
system prompt body. This raw-body hash is explicitly distinct from canonical JSON
request/response digests. Prompt bodies and free-text responses are not audited.

The model can request only the six documented role tools. Dispatch is explicit,
never arbitrary `getattr` on a model-provided name. Tool arguments bind the current
incident/impact set and selected offered plan; the model cannot supply arbitrary
paths, SQL, shell commands, audit contents or terminal states. `get_snapshot`,
registry reads and usage reads are orchestration-only. Every executed role tool
emits a factual event through Main's append-only port.

Every nonempty model `tools` list is provisional. Core authorizes the entire
batch, rejects duplicates, executes the real port methods, and sends structured
`tool_observations` to the same role in a new user message, retaining the previous
assistant output and conversation history. Only a fresh, valid response with
`tools=[]` advances to the existing evidence/hard-validation/approval path. Failed
tools, invalid output, provider errors, exhausted budgets or unfinished rounds
never fall back to the initial answer. There is at most one tool batch and one
additional model decision per role stage. If the second answer still requests
tools, Core stops before executing them or making a third decision. Follow-ups
consume the same run/day budget and do not reset or
increase the replan limit. Duplicate tracking and messages are conversation-local.
Each observation has a unique digest linked to run, incident, frozen agent, role,
the parent model request, exact tool request and actual readonly snapshot/method.
Audit-marker observations include the real appended step ID. Observations remain
user-message data and cannot replace the system prompt or change permissions;
provisional conclusions never advance the graph.

Observations project only incident/booking/equipment facts: no borrower alias,
equipment name, incident free-text reason or validation error message is sent.
Validation observations contain typed codes/references. Main's trusted fictional
`BookingRuleCatalog` is sent with its actual ID/version/content/source/fingerprint,
using the unchanged `get_booking_rules` port; Core loads no rule files. A planner
validation tool is bound to its provisional offered plan, then the final plan is
validated again. Observations over 1 MiB fail explicitly rather than truncate.
Frozen catalogs also add exact `ruleset:<id>:v<version>` and
`rule:<id>:v<version>` references to the private allowed-evidence set. Unknown IDs
or versions remain rejected, and the snapshot reference is still required. Plain
legacy `BookingRules` objects continue to work without catalog references.

## A2: state graph and deterministic search

```text
scope -> route -> impact -> Send(query_candidates per booking) -> gather/solve
                                                               -> planner
                                                                  | hard rejection
                                                                  v
                                    verifier -> revise -------> replan
                                        |                         | <=2
                                        v                         +-> planner
                                 WAIT_APPROVAL / manual
```

No impact exits before routing or model calls. Missing capability exits to manual
review. The graph uses real LangGraph `Send` fan-out and a reducer to gather
candidate results. Three-query concurrency is tested with a barrier, not inferred
from timing. All reads use Main's immutable run snapshot. Audit appends are
serialized per run, while the candidate reads run concurrently.

The bounded solver checks snapshot membership, status, capability inclusion,
open incidents, unaffected active bookings, unchanged times and internal overlap.
It backtracks across all affected bookings and keeps a stable ranked subset of
plans: category changes, excess capabilities, then equipment ID. Node and offer
limits bound the search. Exhaustive no-solution and incomplete search are distinct;
an incomplete search never claims global optimality. No time shifts are attempted.

The planner chooses an offered plan ID; fabricated IDs are rejected with feedback.
Main's readonly validator checks every selection. The verifier independently reads
the impact, candidates, rules and hard-validation result before its own request.
Its pass/revise/manual decision must have consistent structured reasons and real
snapshot/booking evidence. Invalid or insufficient evidence stops safely. Only
planning is repeated, at most twice; impact/query/search are not replayed to make
the trace look like new work. There is no checkpoint, interrupt, new dependency or
irreversible operation inside the graph. Main persists approval and resume state.

## A3: evidence, logging and test commands

Logs contain controlled reasons, counts, token counts when supplied, durations and
SHA-256 digests. Free-text model summaries, prompts, aliases, incident reasons,
headers, keys and raw provider exceptions are not logged. Prompt input excludes
borrower aliases, equipment names and incident reason. Debug transport logging is
refused, including explicitly DEBUG child loggers under SDK/HTTP namespaces.
Provider reasoning/encrypted reasoning fields are not copied into responses or
logs. Private role schemas do not introduce a second public `PlanResult`.

The model-start audit records the selected agent before dispatch. The completion
audit records an actual HTTP dispatch attempt for this adapter. Local SDK request
building failures count zero attempts, even when a conservative reservation has
already been prepared and retained. Configuration edits do not revoke that run's
frozen role. If an incompatible audit sink nevertheless rejects an agent's
completion, an orchestration accounting fallback preserves the attempted count
and `started_agent` link, then stops; it does not reread live configuration or
turn a rejected audit into success.
An unavailable audit sink prevents success; a durable quota reservation remains
consumed even if an actual attempt's completion cannot be persisted.

`tests/core/` fake clients and readonly ports explicitly identify `TEST_INJECTED`,
`offline_mock_test` or `failure_injection_test`. They are never production fallbacks.
SDK HTTP tests use a local `httpx.MockTransport` and no network. Isolated Main
composition tests initialize NEW fictional test databases through Main's CLI and
use only public application/readonly ports; they never open a DB directly.

```powershell
. .\scripts\dev-env.ps1
.\.venv\Scripts\python.exe -m pytest tests/core -q
.\.venv\Scripts\python.exe -m pytest -q --junitxml=var/logs/agent-core-offline.xml
.\.venv\Scripts\python.exe -m pip check
```

The separate `real_api` test is skipped by default. Once explicitly authorized,
with provider quota and billing protection checked and all model variables manually
configured, enable `CAMPUSOPS_LLM_ENABLED` after dot-sourcing, then run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/core/test_real_api.py --run-real-api -q
```

This runs real model requests against a NEW isolated fictional Main database and
keeps the run/step records in pytest's retained `.tmp` tree. It tests three roles,
evidence and WAIT_APPROVAL with real Main validation, not commercial acceptance.
It does not exercise a real approval, all feedback branches, account-wide budgets
or a UI. Main still owns integrated real acceptance and any publication claims.

Branch delivery must report the actual fixed commit and test output separately.
The supervised acceptance commands, exact node/call expectations and safe evidence
fields are documented in `tests/core/README.md`. Until the explicit real test is
exercised, real API / real three-role and branch combination acceptance remain
**NotRun for this revision** regardless of installed SDKs or passed
offline tests. Historical RC4/RC5 receipts remain tied to their own source hashes.
Core consumes the Main-published 1.2.0 contract without editing
Main contract/schema/storage/service/dependency files.
