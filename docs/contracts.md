# CampusOps 公共契约 1.7.0

状态：**Main 已建立并通过测试的 M0/M1 冻结接口**。需求原文是 project-spec-v2.md。
冻结源为 `src/campusops/contracts/models.py`、`ports.py`、`errors.py`；
JSON Schema 与端口签名在 `contracts-v1.7.0.schema.json` 中由测试比对。
原1.0.0至1.6.0快照均保留，兼容测试逐一核实所有旧模型与方法签名完全不变。
功能分支不得自行改字段、签名、错误码或 schema。需要变更时向 Main 提交精确申请，
由 Main 升版/测试/提交，然后功能分支保护自己的成果后同步 main。

## 1.7.0 可选器材台账与故障边界

Main 新增 EquipmentCatalogPort；原设备状态枚举及所有旧端口保持不变：

```python
list_equipment_catalog(include_archived: bool = False) -> tuple[EquipmentCatalogEntry, ...]
create_equipment(request: EquipmentCreate) -> EquipmentCatalogMutationResult
update_equipment(request: EquipmentUpdate) -> EquipmentCatalogMutationResult
archive_equipment(request: EquipmentArchiveCreate) -> EquipmentCatalogMutationResult
restore_equipment(request: EquipmentRestoreCreate) -> EquipmentCatalogMutationResult
find_equipment_command(idempotency_key: str) -> EquipmentCatalogMutationResult | None
```

创建包含name/category/capabilities/actor_alias/note/idempotency_key；编辑再带设备ID及预览版本。
移除/恢复包含设备ID、版本、actor_alias/reason/key。返回完整设备读回、归档标识/时间/原状态和失效方案ID。
幂等键跨重启保存；同键异内容拒绝。BEGIN IMMEDIATE内验证预约关联、变更、读回、作废待批及审计原子提交。
归档设备沿用旧MAINTENANCE状态以禁止分配，归档元数据与幂等回执使用原audit_log，不增加迁移或删除旧数据。
ACTIVE预约阻止归档/改类别/丢失必需能力；恢复故障设备不冒充维修。任何器材命令不修改预约。
相同未解决故障重复提交复用原记录；其他未解决重复事件明确拒绝；旧故障完整保留。
当前器材可用却有OPEN事故时禁止新工作流，0新模型请求；UI历史只读，不当作当前故障。
新入口和每次业务调用继续受TOTP会话检查，不新增免验证模式。

## 1.6.0 可选预约生命周期

RC10保持以下所有模型/方法签名及冻结Schema不变。修改候选新增Main同类硬守卫，
其独立策略标识/版本和正文进入修改快照指纹，旧待批修改因此在批准时重新验证或过期。
requested_equipment_id=None表示自动匹配原器材同类候选；指定ID仍表示只允许这一台，不静默改用其他设备。
ValidationResult的原issues/violations/evidence_ids用于保存逐器材的冻结诊断，既有CAPTURED事件额外保存
candidate_policy与candidate_validation；不更改数据库schema或覆盖旧事件。
失败仍以context.request保存请求目标，after=None明确没有可执行方案，禁止伪造after显示。
历史没有逐项诊断时，界面明确标记缺项和当前器材状态，不改写历史为新验证结果。

用户明确授权新增、修改和取消预约。Main新增可选ReservationManagementPort，原ApplicationPort/ReadonlyPort不变：

```python
create_reservation(request: ReservationCreate) -> ReservationMutationResult
cancel_reservation(request: ReservationCancelCreate) -> ReservationMutationResult
find_reservation_command(idempotency_key: str) -> ReservationMutationResult | None
plan_reservation_change(request: ReservationChangeCreate) -> ReservationChangeRun
get_reservation_change(change_id: str) -> ReservationChangeRun
list_reservation_changes(reservation_id: str | None = None) -> tuple[ReservationChangeRun, ...]
approve_reservation_change(change_id: str, actor_alias: str, idempotency_key: str) -> ReservationChangeDecision
reject_reservation_change(change_id: str, actor_alias: str, reason: str) -> ReservationChangeDecision
get_reservation_change_budget(change_id: str) -> ModelBudgetSnapshot
```

创建含器材/预览时设备版本、借用者匿名别名、UTC起止、能力需求、登记人、可选note和持久请求key。
取消含原预约ID/版本、确认人、原因及key，标记CANCELLED并增加版本，保留全部历史。
二者不调用模型。ApprovalService是唯一正常预约INSERT/UPDATE入口，事务校验、失败整体回滚、成功审计与幂等结果同提交。
ReservationMutationResult含实际读回reservation、失效方案ID和中文说明。

修改请求含原ID/版本、请求时间、匿名别名、能力、可空requested_equipment_id（自动匹配）、原因、确认人和key。
单独的ReservationChangeContext/Run/PlanResult/Decision保存在现有audit_log的受限事件中，不迁移schema1，
不伪造incidents，不修改旧预约后再让AI解释。生成仅保存方案；批准在同一BEGIN IMMEDIATE中重验资源/规则指纹与全部硬约束，
更新同一预约ID及版本；拒绝/过期不写预约。新增/取消会使现有全量快照待批方案失效。
同一在途请求复用原运行；同一预约已有不同在途/待批请求时拒绝新启动。成功审批幂等，审计保存操作人匿名别名。

新增ReservationChangeOrchestrationPort.run_reservation_change及ReservationChangeReadonlyPort，复用三角色、工具白名单和RunContext预算。
只读端口的get_incident保留现有工具协议名称，但返回明确kind='reservation_change'的Context，绝不是设备事故；
get_impacted_reservations仅返回原单预约；get_booking_rules返回独立ReservationChangePolicy，原BookingRules的保留时间语义不变。
该策略及原规则目录都进入指纹；模型仅接收经过投影的预约/请求/工具观察，不接收借用者别名、确认人或自由原因。
工具观察必须进入同角色后续请求，每角色阶段最多一批+一次追加决策，所有请求共用原适配器6/run、30/UTC日预算。
配置快照固定，新提示词按角色与v1/v2使用真正不同正文，其他运行不串用。
模型选项只能来自确定性候选；请求外时间/设备、其他预约改动、无审批写库全部拒绝。

## 1.5.0 可选维修恢复

本轮用户明确授权增加维修恢复，扩展原 P0 单向故障状态；原设计原文保留，不覆盖历史范围说明。
新增 EquipmentMaintenancePort.recover_equipment(request: EquipmentRecoveryCreate) -> EquipmentRecoveryResult。
Create 含 equipment_id、expected_equipment_version、actor_alias、note、idempotency_key；均不可空。
Result 含恢复后的 equipment、resolved_incident_ids、invalidated_proposal_ids、recovered_at（UTC）。
原 ApplicationPort / ReadonlyPort 完全不变；Agent 无此写入能力，UI 必须经受认证的应用服务。
Main 在 BEGIN IMMEDIATE 中检查状态为故障/维护、版本相符，恢复可用、关闭该器材开放故障、提升设备版本，
并使所有绑定完整资源快照的待批方案及等待审批运行过期，原始方案/结果仍保留。写审计后原子提交。
已批准/拒绝记录不改；预约完全不改；已转移预约不自动回迁。无模型请求、无认证或预算重置。
同 key+相同完整请求返回持久原结果；同 key 不同内容拒绝。版本变化/已可用拒绝，事务错误全部回滚。
并发审批先后由数据库锁串行化；旧故障关闭后不得再生成方案。内部纯业务测试接口保留，无正式免认证开关。

## 1.4.0 可选持久工作流启动

新增可选 `IdempotentWorkflowPort.start_workflow_once(incident_id, idempotency_key) -> PlanResult`。
原ApplicationPort.start_workflow签名和兼容语义不变，不重写编排图。
UI为同一逻辑动作复用稳定key，只有用户明确重新生成时创建新的逻辑key；不能每次rerun随机换key。
Main在同一个BEGIN IMMEDIATE事务里创建run和现有audit_log中的启动claim，key只存摘要，无schema迁移。
同key和同incident已有结果就返回原PlanResult；进行中/未知结果返回同run_id、FAILED/INVALID_STATE及
WORKFLOW_ALREADY_IN_PROGRESS提示，此返回不把原运行改成FAILED，也不再调用模型。
不同incident复用key返回IDEMPOTENCY_CONFLICT。即使重启应用服务，claim仍持久保存。
UI遇到进行中或丢失响应应读取get_run/get_proposal，不自动生成新key重试；状态未知不可伪装成功或退款。

真实验收移位时可由Main在进程环境显式指定CAMPUSOPS_BUDGET_PROJECT_ROOT为原有已验证项目上下文，
create_configured_application仅在原有明确模型opt-in成立时将该上下文交给Core既有BudgetLedger构造器。
Core原D盘、CampusOps身份和账本必须位于选定上下文内的守卫完全保留；无效上下文报错，不退回新空账本。
此选项不加载密钥、不启用模型，也不构成费用授权；不得放进可分发包或.env中的真实用户配置值。
移位验收沿用单一现有权威账本，禁止复制清零或新建一套抵销历史消费。服务商账户硬保护仍不可替代。

## 1.3.0 可选规则展示端口

为让界面直接查看实际规则，新增可选 `BookingRulesPort.get_booking_rules() -> BookingRuleCatalog`。
原ApplicationPort和ReadonlyPort的签名不变。Main的ApplicationService实现该只读查询：
不访问业务数据库、不加载模型、不读.env、不改配置或预算，仅返回当前发布的本地虚构规则目录。
UI先用isinstance(service, BookingRulesPort)检测，旧实现不提供该能力时明确说明不可用。
当前规则目录与历史运行冻结目录不同，界面不得把当前版本冒充历史运行使用版本。
1.2.0快照已封存，1.3.0为新增端口另立冻结点，不回写旧合同。

## 1.2.0 提交前修订

旧 `AgentConfigPatch`、`BookingRules`、`ResourceSnapshot` 与所有端口签名保持原样。
新增 `AgentRuntimeConfigPatch(AgentConfigPatch)`，增加可选 `allowed_tools` 元组（最多6项，不重复）。
仍调用 `ApplicationPort.update_agent_config`；Main接受该子类型或对应字典，验证角色工具上限后持久化。
旧类型直接传未知字段仍拒绝。允许工具只能缩减到该角色公布的只读集合，不能授予SQL或预约写权限。
`contracts/configuration.py` 公布 `SUPPORTED_PROMPT_VERSIONS=('v1','v2')` 与 `ROLE_TOOL_LIMITS`。
v1保留旧默认，v2由Core提供实际不同的正文；Main拒绝未知版本，Core也须在调用前拒绝未知正文。

新增 `BookingRuleDocument` 与 `BookingRuleCatalog(BookingRules)`。每条规则有rule_id/version/content/source/
fictional=true/incident_kind='equipment_failure'；目录有ruleset_id/version/source/fictional/rules/fingerprint。
只读端口 `get_booking_rules()` 返回这一兼容子类型，包含唯一GearShift故障场景的实际匹配正文，
不只是布尔常量。来源为包内 `campusops/domain/data/gearshift-rules-v1.json`，明确虚构，禁止覆盖硬策略。
规则文本不能执行SQL、改期或改变工具权限。Main按规范化目录内容计算SHA256，版本或正文变化都改变快照。

每个运行在开始事务内捕获资源、规则目录与完整Agent配置。工具查询和审计权限使用该不可变快照；
之后的配置修改只影响新任务，不撤销或偷偷切换进行中的角色/提示词/工具。Main将快照写入现有audit_log，
不改变schema1、不重置业务库。Core须使用所选冻结配置和实际提示词正文hash。
生成结束和人工批准时，Main重新读取当前目录并校验资源指纹；规则版本/正文变化使待批方案过期，
缺失/损坏目录受控失败。旧RC5待批方案因新版本指纹安全失效，已完成审批的幂等读回仍保留。
规则目录是本地只读发布配置，不提供任意在线编辑或大型知识库。

额外工具请求使用原有结构化JSON协议：权限/参数校验→实际只读工具→安全结构化观察→同角色后续模型请求→
再次决策。未消费观察前不能采用首次答案。Core负责有界循环、重复工具拒绝和每次complete统一预算；
SDK自动重试保持0、禁止隐藏回退。所有新增调用均消耗原6/run、30/UTC日预算，不自动提高。
Main冻结本节只代表接口/规则服务已验证；Core闭环、最终包真实模型与材料由独立证据验收。

## 通用约定

- Python 3.12；Pydantic 2.14.3。模型禁止未知字段、冻结实例、重新校验传入模型实例。
- 输入时间必须有时区，统一转 UTC。数据库 UTC，UI 仅展示时转换 Asia/Shanghai。
- 时窗为 `[starts_at, ends_at)`，严格 starts_at < ends_at；首版不得更改预约时间。
- 集合使用 tuple；输入允许普通 list，JSON 序列化为数组。不得依赖可变模型/列表作为状态存储。
- 标识符为 1–100 位 ASCII 字母/数字/下划线/短横线；版本为严格正整数，禁止 bool。
- 查询与命令的可预期异常为 `CampusOpsError`，读取 `.error: ServiceError(code,message)`。
  PlanResult / ApplyResult 以 `.error` 表达失败。UI 不显示原始数据库/提供商异常。

## 核心模型

| 模型 | 字段 / 含义 |
| --- | --- |
| IncidentCreate | equipment_id, kind='equipment_failure', effective_at, reason |
| Incident | 在创建模型上增加 id, status='OPEN'/'RESOLVED', created_at |
| Equipment | id, name, category, capabilities, status, version, updated_at |
| Reservation | id, equipment_id, borrower_alias, starts_at, ends_at, required_capabilities, status, version, updated_at |
| ProposalItem | reservation_id, from_equipment_id, to_equipment_id, starts_at, ends_at, expected_reservation_version |
| ValidationIssue | code: ErrorCode, message, entity_id? |
| ValidationResult | passed, issues（文本数组）, evidence_ids, violations（结构化错误数组）；通过不能同时带错误 |
| PlanResult | run_id, incident_id, proposal_id?, status, changes, validation, explanation, error? |
| ApplyResult | proposal_id, status, changed_reservation_ids, message, error? |
| Proposal | id, run_id, incident_id, status, snapshot_fingerprint, changes, validation, explanation, created_at |
| WorkflowRun | id, incident_id, status, execution_mode, provider?, model_id?, model_calls, retry_count, snapshot_fingerprint, created_at, finished_at?, result?, error? |
| AgentConfig | agent_id, display_name, role（impact/planner/verifier）, skills, allowed_tools, enabled, prompt_version, updated_at |
| AgentConfigPatch | 仅 enabled? 与 prompt_version?；不得通过 UI 增加 SQL/shell 等工具权限 |
| RunStepCreate | run_id, node, agent_id?, tool_name?, decision_reason, input_digest, output_digest, duration_ms, error?, model_calls（0/1）, retry_count（0..2）, status? |
| RunStep | 追加 id, seq, execution_mode, created_at；均由 Main 产生 |
| ResourceSnapshot | fingerprint, equipment, reservations, incidents；一次运行固定使用同一资源快照 |
| BookingRules | storage_timezone='UTC', display_timezone='Asia/Shanghai', interval_semantics='[start,end)', preserve_original_times=True, max_replans=2 |
| ModelRequest | run_id, agent_id, messages: tuple[ModelMessage], response_schema: str（JSON Schema 文本） |
| ModelMessage | role: system/user/assistant/tool, content |
| ModelResponse | content, provider, model_id, input_tokens?, output_tokens?；没有真实 token 数据时保持 None |
| ModelUsage | utc_date, daily_model_calls；只汇总当前库 real_api 日志，Core持久预算预留/并发控制独立实现 |

详细 required/default/类型以生成的 JSON Schema 为准。日志 digest 字段只放脱敏摘要，
不存模型 Key、授权头、真实个人资料或完整提供商异常。

## 状态与错误

RunStatus：CREATED、SCOPING、PLANNING、VERIFYING、WAIT_APPROVAL、APPLYING、APPLIED、
NO_IMPACT、REPLANNING、NEEDS_MANUAL、REJECTED、STALE、FAILED。

PlanResult.status 仅接受 WAIT_APPROVAL、NO_IMPACT、NEEDS_MANUAL、FAILED。
WAIT_APPROVAL 必须有非空 changes 与通过的 validation；其余结果不能附带变更或 proposal_id。
Agent Core 返回时 proposal_id=None，Main 校验后分配并持久化方案。
运行持久状态在批准/拒绝后变 APPLIED/REJECTED/STALE；PlanResult 是规划阶段原始结果，
不要把其历史 WAIT_APPROVAL 当成当前审批状态，刷新 get_run / get_proposal。

ProposalStatus：PROPOSED、VALIDATED、APPLIED、REJECTED、STALE。
ApplyStatus：APPLIED、STALE、REJECTED、FAILED；只有 APPLIED 可包含 changed_reservation_ids。
设备 AVAILABLE/FAULTED/MAINTENANCE；预约 ACTIVE/CANCELLED。

ExecutionMode：system、real_api、offline_mock_test、failure_injection_test、demo_fixture。
Main 绑定运行/日志模式，Agent 不能给日志改模式。system 是确定性服务操作；
测试注入和故障注入不可计作真实 AI 验收。

结构化 ErrorCode：NOT_IMPLEMENTED、NOT_FOUND、INVALID_STATE、INVALID_INPUT、INVALID_PROPOSAL、
RESOURCE_UNAVAILABLE、CAPABILITY_MISMATCH、RESERVATION_OVERLAP、SNAPSHOT_CONFLICT、
INCOMPLETE_PLAN、IDEMPOTENCY_CONFLICT、ALREADY_DECIDED、DATABASE_BUSY、DATABASE_ERROR、
WORKFLOW_FAILED、PROVIDER_UNAVAILABLE、BUDGET_EXCEEDED。

## ApplicationPort / UI 门面

Main 组合入口：

```python
from campusops.bootstrap import create_application

service = create_application(
    database_path=None,       # DATABASE_PATH，否则当前目录 var/data/campusops.db
    orchestration=None,       # OrchestrationPort，未接入明确失败
    model_client=None,        # ModelClientPort；无自动模型绑定
    execution_mode="system",  # 注入编排器时显式标记 real_api 或对应测试模式
    provider=None, model_id=None, seed_asset=None,
)
```

不自动迁移、seed、reset、加载 .env 或调用模型。UI 可以导入此 Main 工厂；不得导入 storage。

M2 新增 Main 组合工厂 `create_configured_application(database_path=None, *, seed_asset=None)`，
使用已合入的 GearShiftOrchestrator；只有 `CAMPUSOPS_LLM_ENABLED=true` 才读取显式模型配置
并建立适配器，不在建立时发送请求。未启用时没有模型客户端，有影响的运行返回
PROVIDER_UNAVAILABLE / 0 次调用；无影响仍为 NO_IMPACT。Main 正式界面用根目录
`streamlit_app.py`；原有 create_application 的签名/默认行为和 1.0.0 已有模型、方法签名保持不变。
执行模式 real_api 仅标识选择的运行路径，必须结合实际调用记录判定；零调用不计真实 AI 验收。

当前可执行接口：

```python
service.list_equipment() -> tuple[Equipment, ...]
service.list_reservations() -> tuple[Reservation, ...]
service.list_incidents() -> tuple[Incident, ...]
service.list_runs() -> tuple[WorkflowRun, ...]
service.list_agent_configs() -> tuple[AgentConfig, ...]
service.list_run_steps(run_id: str) -> tuple[RunStep, ...]
service.register_equipment_failure(incident: IncidentCreate) -> Incident
service.start_workflow(incident_id: str) -> PlanResult
service.get_run(run_id: str) -> WorkflowRun
service.get_proposal(proposal_id: str) -> Proposal
service.get_approval_decision(proposal_id: str) -> ApprovalDecision | None
service.get_model_budget(run_id: str) -> ModelBudgetSnapshot
service.approve_proposal(proposal_id: str, actor_alias: str, idempotency_key: str) -> ApplyResult
service.reject_proposal(proposal_id: str, actor_alias: str, reason: str) -> ApplyResult
service.update_agent_config(agent_id: str, patch: AgentConfigPatch) -> AgentConfig
service.reset_demo_data(confirmation: str) -> None
```

登记故障保存事故、置设备 FAULTED、增加设备版本；不改预约。
start_workflow 同步执行但先持久化 run，再将编排调用放在 DB 事务外；可通过刷新读 run/steps。
NO_IMPACT 不调用模型。没有编排实现返回 FAILED / NOT_IMPLEMENTED；真实模式没有客户端
返回 FAILED / PROVIDER_UNAVAILABLE。Core 异常与无效结果安全失败。
Main 重新验证所有 WAIT_APPROVAL 内容，保存方案，不改预约。

批准仅由 Main `ApprovalService` 进行：必须显式管理员操作、非空 actor 与稳定键。
同键不同方案/决策/actor 为 IDEMPOTENCY_CONFLICT。同一已批准方案使用新键再次点击也
返回首次结果，不新增决策或版本；新键没有被额外占用。已拒绝方案不能再批准。
同一事务重查完整快照/每条版本/全部硬约束，成功全部写入，失败无部分预约提交。
过期时可提交 STALE 标记和失败审计，预约不变。中途数据库异常回滚整批更新。
拒绝可重复，不能拒绝已应用方案。审批不自动恢复/重新调用 LLM。

## 1.1.0 只读扩展 / 2026-11-10

`ApprovalDecision` 仅含 `proposal_id, decision: APPROVE|REJECT, actor_alias, reason?, created_at`。
已有方案尚未决策返回 None；方案不存在抛 NOT_FOUND。批准 reason=None，拒绝保留原原因。
读取跨会话持久记录，不暴露幂等键、result_json 或原始审计。查询不调用模型、不更新预约。

`ModelBudgetPort.get_model_budget(run_id: str) -> ModelBudgetSnapshot` 是可选的模型客户端只读能力。
Main 先校验 run 存在，再委托当前客户端；不读取 Core 私有文件，不根据业务日志推测余额。
旧客户端只实现 complete 仍可正常使用；缺少预算能力时返回 UNAVAILABLE，数值为 None。

预算快照字段为 `run_id, scope='current_worktree', status: AVAILABLE|UNAVAILABLE, utc_date,
observed_at`，以及可空 `per_run_limit, daily_limit, run_reserved, daily_reserved,
run_remaining, daily_remaining, error`。日期按 UTC；运行累计不随 UTC 跨日清空，每日累计按 UTC 日期。
AVAILABLE 必须有全部数值且无 error，remaining=max(0,limit-reserved)；UNAVAILABLE 必须数值
全部 None 且有脱敏错误。账本损坏、锁不可用或未知结果不冒充零余额。快照只证明观测时刻，
不保证下一次请求成功；调用前预算锁仍是最终权威。保守预留包含失败/超时/未知结果，不退款，
并非已成功响应次数、Token、金额或服务商账户余额。显示实际调用次数仍读取 WorkflowRun。

Core 实现同一账本/同一锁内完整解析，读取不能 reserve、调用网络或重置账本；
LLMAdapter 暴露此方法。Main/UI 只消费端口。Core 完成前查询诚实返回 UNAVAILABLE。
当前 schema v1 不变，不新增数据库迁移。冻结及兼容测试通过后 Main 发布固定提交再由分支同步。

reset_demo_data 只接受 `RESET_FICTIONAL_DEMO`，仅用于已标记的本项目演示库；
UI 必须先提供二次确认。服务先在原库同目录生成唯一备份，随后原子重建虚构数据。
不能将该功能用于任意上传/客户数据库。

## Agent Core 端口

```python
class OrchestrationPort(Protocol):
    def run_incident(self, incident_id: str, run_id: str,
                     readonly_port: ReadonlyPort,
                     model_client: ModelClientPort | None) -> PlanResult: ...

class ModelClientPort(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse: ...
```

Core 不直接保存 proposal；只返回 proposal_id=None 的 PlanResult。运行标识必须原样返回。
模型适配器在 Agent Core 分支实现，手动读 MODEL_API_KEY / MODEL_BASE_URL / MODEL_ID，
执行每运行/每日预算、超时、REPLAN_MAX<=2、白名单工具和脱敏。Main 不实现这些分支职责。
多预约候选搜索、并行 fan-out/gather、组合回溯/排序与反馈循环由 Agent Core 负责；
它们可以调用以下确定性只读工具，但不能替代 Main 最终校验或直接写库：

```python
readonly_port.get_incident(incident_id: str) -> Incident
readonly_port.get_impacted_reservations(incident_id: str) -> tuple[Reservation, ...]
readonly_port.list_equipment_candidates(reservation_id: str) -> tuple[Equipment, ...]
readonly_port.get_booking_rules() -> BookingRules
readonly_port.get_snapshot() -> ResourceSnapshot
readonly_port.list_agent_configs() -> tuple[AgentConfig, ...]
readonly_port.get_model_usage() -> ModelUsage
readonly_port.validate_candidate_plan(changes: tuple[ProposalItem, ...]) -> ValidationResult
readonly_port.append_run_step(step: RunStepCreate) -> RunStep
```

工具绑定当前 incident/run，候选只接受受影响预约。候选按设备 ID 排序、保留原时间。
开放事故在 effective_at 起永久封禁，原设备 FAULTED；返回单条候选后必须做全局组合校验。
append_run_step 只能写当前运行，seq 在事务内分配；运行快照中的Agent不存在/禁用/工具未允许时拒绝。
step.status 仅可选 SCOPING/PLANNING/VERIFYING/REPLANNING，不能冒充审批完成；
模型调用/重试计数按实际行为记录。运行终结后拒绝继续追加。模型额度检查必须在真正调用前
执行；事后日志汇总不是预算锁，不得对外宣称 Main 已实现完整限流。
演示重置会清空当前库的演示运行日志，get_model_usage 只反映当前库尚存的真实调用事实；
Core 不得单靠它充当不可重置的每日额度账本，需自行持久预算预留或向 Main 提出明确的存储申请。

## 数据与检查命令

```powershell
. .\scripts\dev-env.ps1
.\.venv\Scripts\python.exe -m campusops.cli init-db
.\.venv\Scripts\python.exe -m campusops.cli seed-demo
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

`scripts/freeze_contracts.py` 首次生成 schema；已存在且有差异时拒绝覆盖。
合同漂移测试、数据库测试和事务测试归 Main `tests/integration/`。
Agent Core 拥有 `tests/core/`，App UI 拥有 `tests/ui/`；不能为使测试通过修改 Main 契约快照。
