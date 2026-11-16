# CampusOps V2.0｜可执行的项目详细设计与验收标准

**版本**：V2.0 · 设计冻结候选（未开发、未测试）  
**设计日期**：2026-11-05  
**比赛**：第五届“未来杯”创新大赛，赛题一「Agent编排创新应用」（以官方竞赛页面最终版本为准）  
**初赛提交截止**：2026-11-20  
**团队**：三人，只有一人主要编程。  
**唯一实施场景**：GearShift（学生社团共享器材异常后的智能调度）。  
**未来场景但不实现**：ClubRescue（社团活动资源调整）。  
**隔离底线**：与个人商业项目「析见」完全独立，不读取、不复用、不暴露其代码、算法、素材、业务数据与工作树。

> 文档是可交给 Codex 的实现约束，不是已经完成的软件，不应把设计目标当成实现结果向评委陈述。

## 1. 为什么这样定位

- 使用者：摄影社、影音社、机器人社等有少量共享器材的学生组织管理者。
- 当前问题：一件已经被预约的器材发生故障，会影响未来预约；管理员需要判断受影响范围、查找功能等效的器材、排除时间冲突并落实变更。
- 产品价值：异常触发后由系统主动进入处理流程，不要求用户每次复制粘贴背景。人工只负责确认重大变更与无法自动解决的问题。
- 赛道价值：实现多 Agent 的定义/配置、工作流编排、任务分派、真实工具集成、运行监控、结果验证、反馈优化；不是单 Agent 聊天，也不是只靠硬编码的固定流程。
- 真实性要求：在系统内变更已有的**虚构预约数据**，不得假称对学校正式资产或微信用户执行操作。

## 2. 首版用户故事与完整流程

### 故事 A：故障成功补位（主演示）

模拟摄影社拥有 10 件器材、20 条预约、6 个匿名借用者。一台已经预约的相机被管理员标记为故障。

1. 管理员在资源页把设备 A 标记为「故障」，填写原因和生效时间。
2. 应用服务先持久化事故，并查询从故障生效时刻起、与故障时段相交的有效预约。
3. 如未影响预约，工作流记录 `NO_IMPACT`，无需付费模型调用。
4. 如有影响，调度器按能力注册表选择 Agent：影响分析 → 并行候选/规则查询 → 规划 → 验证。
5. 规划 Agent 只能从确定性工具返回的合法候选中提出组合方案，并给出可追溯的决策依据。
6. 程序重新核查所有硬约束；验证 Agent 检查证据、解释方案并质疑不足。如果可修正，则带反馈重规划，最多 2 次。
7. 可行方案进入 `WAIT_APPROVAL`；预览变更前后数据，不得提前修改预约。
8. 管理员点击批准，应用服务在**同一个数据库事务中**重新读取与校验，验证快照/版本，原子更新预约与审计记录。
9. 提交后查询数据库确认新状态；重复批准同一方案只返回原有结果，不重复修改。

### 故事 B：找不到替代器材

所有兼容器材都被预约或不可用：结果为 `NEEDS_MANUAL`，展示不能安排的原因，不虚构成功。初赛允许后续由管理员人工调整，不做自动改期功能。

### 故事 C：审批前资源状态变化

方案生成后，在同一系统中新增冲突预约或修改设备状态。批准时事务校验失败并回滚，方案标记 `STALE` 或 `NEEDS_REPLAN`，预约数据不被污染。

### 故事 D：故障注入与反馈验证

在隔离测试环境中让规划 Agent 产生可重复的无效候选（注明为故障注入）；规则校验器拒绝，调度器记录验证原因并尝试有限重规划。演示必须明确标注为「故障注入测试」，不能当作模型自然犯错。

## 3. 场景范围与明确不做

### P0：非完成不可
- 已授权管理者本地使用，虚构器材/预约记录可创建、展示与查询。
- 一种核心异常：`equipment_failure`。管理员登记故障触发工作流。
- 三个基于真实 LLM 调用的独立 Agent 角色，模型可共用一个 API，但角色、允许工具、输入输出及运行记录独立。
- 能力注册表（至少角色/技能/可用工具/是否启用/提示词版本），并能证明改变配置会影响路由或显式报错。
- 顺序、并行、条件、受限循环四类流程；动态调度理由可追踪。
- 确定性候选搜索、全局冲突验证和方案排序；无解时安全失败。
- 人工审批、事务内再验证、幂等执行、运行审计。
- 三个简单 Web 页面；模型 API 手动切换且预算受限。
- 本地一键重置**虚构演示数据**，以及 pytest 离线测试和至少一次真实 API 端到端验收。

### P1：仅在 P0 全部通过后考虑
- 延期归还 `overdue_return` 作为第二异常事件；更丰富的调度分支。
- 批准前由受影响人员模拟接受/拒绝（仅内部演示记录）。
- 允许用户同意的时间调整与更复杂的偏好排序。

### 明确不做
- 实时硬件故障自动感知、微信/短信推送、正式校园账号、接入真实学校系统、自动付款、多人同时在线正式运营。
- 任意自然语言控制数据库、直接自动修改学校资产、不受控制的 Agent 自动写入、在线云服务24小时运行。
- 大型拖拽式通用工作流编辑器、训练自有模型、本地大模型推理、全面活动管理系统。

## 4. 系统分层与单一事实来源

```text
Streamlit 管理端（只负责界面和调用应用服务）
  └── ApplicationService（唯一业务入口；Main 负责）
        ├── Domain Validation（时间/能力/状态/审批规则）
        ├── SQLite Repositories（设备、预约、事故、方案、运行日志）
        ├── ApprovalService（BEGIN IMMEDIATE + 重查 + 原子写库）
        └── AgentOrchestrationPort（注入 Agent Core 实现）
              ├── Agent Registry / Capability Router
              ├── LangGraph StateGraph / checkpoint
              ├── LLM Adapter（OpenAI兼容接口，只读 env）
              ├── Impact Agent
              ├── Planning Agent
              ├── Verification Agent
              └── Readonly Tool Adapter（仅通过 Main 暴露的工具端口）
```

关键约束：**只有 Main 的应用服务层能够进行预约写操作**。Agent 和 UI 不直接执行业务 SQL UPDATE，不允许模型执行任意 SQL 或 shell。Streamlit Session State 只保存临时界面状态，真实数据与运行记录都落到 SQLite。

## 5. Agent 的实际职责与工具权限

| 组件 | LLM | 真实能力 | 明确禁止 |
|---|---|---|---|
| ImpactAgent | 是 | 通过只读工具获取事故与受影响预约，提出影响范围与约束解释，输出结构化结果 | 修改资源状态、编造预约 |
| PlannerAgent | 是 | 选择允许的候选查询与求解工具，从工具返回的可用计划中选择/排序并解释理由 | 直接写数据库、凭空指定不存在设备 |
| VerifierAgent | 是 | 独立读取证据和约束校验结果，判断是否合格，输出有原因的反馈或人工复核建议 | 自我批准变更、替代确定性校验器 |
| CapabilityScheduler | 可调用已选模型辅助决策 | 根据 `required_skills + available_agents + dependencies + priority` 输出分派路径，并记录理由；能力不足安全失败 | 固定三 Agent 顺序假装动态调度 |
| ConstraintSolver | 否 | 枚举同时间窗候选，回溯构造多预约一致的计划；确定性排序 | 绕过硬约束 |
| ConstraintValidator | 否 | 重新读取并检查设备状态、能力标签、预约重叠和快照版本 | 信任 LLM 自称合格 |

动态调度**最低可演示行为**：
1. 无影响预约 → `NO_IMPACT` 路径（不做无意义 Agent 调用）。
2. 一条受影响预约 → 单任务求解；两条以上 → 独立候选查询并行 fan-out / gather，最后统一全局校验。
3. 禁用某个必需 Agent / 能力不匹配 → 阻断或选择另一个满足能力的 Agent（若确实存在）；记录路径变化。
4. 验证失败 → 给 PlanningAgent 反馈新的约束，最多两次重新规划；超过上限 `NEEDS_MANUAL`。

必须记录模型真正调用了几次、执行了哪些工具。不能把静态文本或模拟日志包装成真实 Agent。

## 6. 约束求解策略：不要过度设计

### 硬约束
- `equipment.status == AVAILABLE`，且该设备不在事故影响区间被封禁。
- 新设备的 `capabilities` 必须包含预约的全部 `required_capabilities`；不做含糊的「差不多兼容」。
- 原始预约的时间段默认**不变**。存储一律 UTC；展示采用 `Asia/Shanghai`。时间段使用半开区间 `[start, end)`，要求 `start < end`。
- 同一器材两条已生效预约不得重叠。重叠条件：`a.start < b.end AND b.start < a.end`。
- 检测历史有效预约、同一个新方案内部的冲突、审批前新增的冲突。
- 非授权的其他预约不得被静默改动；批准前任何方案仅有 `PROPOSED`/`VALIDATED` 状态，预约状态不发生改变。

### 软约束（仅排序）
- 尽量保持原设备类型、更多能力匹配、减少变更数量；相同时按 ID 稳定排序确保可复现。
- 初赛先用候选枚举 + 有限回溯，最多 10 件器材、20 条预约。无需引入 OR-Tools 或大型资源优化框架。
- 一旦没有可行方案，明确 `NEEDS_MANUAL`。不要为演示强行安排无效器材。

## 7. 运行状态机与可观测性

`CREATED → SCOPING → PLANNING → VERIFYING → WAIT_APPROVAL → APPLYING → APPLIED`

分支状态：`NO_IMPACT`、`REPLANNING`（最多2次）、`NEEDS_MANUAL`、`REJECTED`、`STALE`、`FAILED`。记录状态变更、触发原因、执行时间、Agent 名称、工具名称、参数摘要、响应摘要、错误、调用数量。

执行轨迹必须是由实际模型调用/工具调用自动记录，**不能手动编辑日志制造成功结果**。可以有清楚标注的 `offline_mock_test` 与 `failure_injection_test`，但与正式真实模型运行记录分开。

如果使用 LangGraph `interrupt()` 做人工审批，必须配置持久 checkpointer 和稳定 `thread_id`；恢复节点可能从头重执行，因此**数据库变更不放在可能重执行的 LLM/审批节点内**，最终仍通过 Main 的幂等审批服务提交。赶工时也可在应用层保存 `WAIT_APPROVAL` 并用服务接口恢复，前提是审批状态、恢复与执行追踪真实可靠。

## 8. 数据库设计（Main 独占 schema 与迁移）

- `equipment(id PK, name, category, capabilities_json, status, version, updated_at_utc)`
- `reservations(id PK, equipment_id FK, borrower_alias, starts_at_utc, ends_at_utc, requirements_json, status, version, updated_at_utc)`
- `incidents(id PK, equipment_id FK, kind, effective_at_utc, reason, status, created_at_utc)`
- `agent_registry(agent_id PK, display_name, role, skills_json, allowed_tools_json, enabled, prompt_version, updated_at_utc)`
- `workflow_runs(id PK, incident_id FK, status, provider, model_id, model_calls, retry_count, created_at_utc, finished_at_utc)`
- `run_steps(id PK, run_id FK, seq, agent_id, node, tool_name, decision_reason, input_digest, output_digest, duration_ms, error, created_at_utc)`
- `proposals(id PK, run_id FK, incident_id FK, status, snapshot_fingerprint, score, reasoning, created_at_utc)`
- `proposal_items(id PK, proposal_id FK, reservation_id FK, old_equipment_id, new_equipment_id, starts_at_utc, ends_at_utc, reservation_version)`
- `approvals(id PK, proposal_id UNIQUE, actor_alias, decision, idempotency_key UNIQUE, reason, created_at_utc)`
- `audit_log(id PK, actor_type, action, entity_type, entity_id, before_json, after_json, created_at_utc)`

索引：`reservations(equipment_id, status, starts_at_utc, ends_at_utc)`、`incidents(equipment_id, status)`、`run_steps(run_id, seq)`、`proposals(run_id)`。外键开启 PRAGMA foreign_keys=ON；所有 DB 变更通过 Main 的 migration/seed。

首版单用户本地演示并不意味着可以忽视冲突。SQLite 没有直接的时间区间排斥约束，业务代码必须在审批写事务中再次查验冲突。

## 9. 公共接口契约（由 Main 以 Pydantic 正式落地并冻结）

合同文件：`src/campusops/contracts/models.py` 与 `docs/contracts.md`，所有分支只消费 Main 已发布的版本。以下是逻辑草案，Main 必须生成可运行实现。

```python
from datetime import datetime
from typing import Literal
from pydantic import BaseModel

class IncidentCreate(BaseModel):
    equipment_id: str
    kind: Literal["equipment_failure"]
    effective_at: datetime  # 必须有时区
    reason: str

class ProposalItem(BaseModel):
    reservation_id: str
    from_equipment_id: str
    to_equipment_id: str
    starts_at: datetime
    ends_at: datetime
    expected_reservation_version: int

class ValidationResult(BaseModel):
    passed: bool
    issues: list[str]
    evidence_ids: list[str]

class PlanResult(BaseModel):
    run_id: str
    incident_id: str
    proposal_id: str | None
    status: Literal["WAIT_APPROVAL", "NO_IMPACT", "NEEDS_MANUAL", "FAILED"]
    changes: list[ProposalItem]
    validation: ValidationResult
    explanation: str

class ApplyResult(BaseModel):
    proposal_id: str
    status: Literal["APPLIED", "STALE", "REJECTED", "FAILED"]
    changed_reservation_ids: list[str]
    message: str
```

对 UI 的稳定服务入口由 Main 提供，例：

```python
service.list_equipment()
service.list_reservations()
service.register_equipment_failure(IncidentCreate(...))
service.start_workflow(incident_id: str) -> PlanResult
service.get_run(run_id: str)
service.get_proposal(proposal_id: str)
service.approve_proposal(proposal_id: str, actor_alias: str, idempotency_key: str) -> ApplyResult
service.reject_proposal(proposal_id: str, actor_alias: str, reason: str)
service.update_agent_config(agent_id: str, patch: AgentConfigPatch)
```

对 Agent Core 的稳定只读端口由 Main 提供：

```python
readonly_port.get_incident(incident_id)
readonly_port.get_impacted_reservations(incident_id)
readonly_port.list_equipment_candidates(reservation_id)
readonly_port.get_booking_rules()
readonly_port.validate_candidate_plan(changes)
readonly_port.append_run_step(step)       # 仅追加审计，不可任意改预约
```

Main 定义 `OrchestrationPort`，Agent Core 实现 `run_incident(incident_id, run_id, readonly_port, model_client) -> PlanResult` 或等价签名；所有更改以 `docs/contracts.md` 为准。**功能分支不得各自创造不兼容的同名契约。**

## 10. 审批后的唯一写入流程

1. 只允许 Main 的 `ApprovalService` 执行。
2. 检查管理员明确点击批准、方案当前状态有效、`idempotency_key` 未执行。
3. 打开 SQLite 显式写事务（可采用 `BEGIN IMMEDIATE`，具体 Python sqlite3 版本与事务策略统一测试）。
4. 在同一事务里重新读取设备状态、受影响预约版本、现有生效预约和本方案所有资源安排。
5. 任一数据/版本/约束变化 → 回滚，返回 `STALE` 或 `NEEDS_MANUAL`，**不发生部分提交**。
6. 全部通过 → 更新所有目标预约，写审批与审计记录，提交。
7. 提交后再查询验证，重复点击批准返回已完成结果；任何失败均有日志。
8. 模型绝不直接拥有 `UPDATE reservations` 权限。

## 11. 技术栈与版本策略

- Python 3.11 或 3.12，Main 检查与依赖兼容后锁定一个版本。
- `streamlit`：Web 界面；`langgraph`：Agent 工作流；`langgraph-checkpoint-sqlite`：如需持久 checkpoint；`pydantic`：合同/输出校验。
- `openai` 或兼容模型 SDK：仅接一家 OpenAI 兼容 API，手动修改环境变量切换；`sqlite3`：持久化；`pytest`：自动化验收。
- 使用 `pyproject.toml` 或一份锁定依赖文件，避免不同工作树自动选择冲突版本。首个 API 集成成功以后再测第二家，不开发自动 API 轮换。
- 环境变量：`MODEL_API_KEY`、`MODEL_BASE_URL`、`MODEL_ID`、`MODEL_CALLS_PER_RUN_MAX=6`、`MODEL_DAILY_CALLS_MAX=30`、`REPLAN_MAX=2`、`DATABASE_PATH`、`APP_TZ=Asia/Shanghai`。设置服务商账单硬停止/免费无超额扣费，软件侧限额只是第二道保护。
- 未配置可用 API Key 时，模型相关功能禁用或明确报错；离线假模型只用于标注的自动化测试，不在赛事真实演示中伪装成 AI。
- 临时文件、虚拟环境、缓存和日志尽量保留在 D 盘，不修改「析见」或其他任务的工作树配置。不能保证 Windows 或 Codex 自身完全不向系统 C 盘写缓存，所以启动时检查具体写入位置，不作虚假承诺。

## 12. 仓库目录与三个 Codex 聊天的文件所有权

```text
D:\CampusOps\                       # 主工作树 main
  .gitignore
  AGENTS.md
  pyproject.toml
  .env.example
  docs/
    architecture.md                 # Main 拥有
    contracts.md                    # Main 拥有，冻结接口
    progress.md                     # Main 总进度
    acceptance.md                   # Main 验收与测试汇总
    handoff/                        # 功能分支提交的摘要由 Main 归档
  src/campusops/
    contracts/                      # Main
    domain/                         # Main，硬约束与数据模型
    storage/                        # Main，SQLite/seed/迁移
    services/                       # Main，业务服务/审批事务/门面
    agent_core/                     # Agent Core，LLM/Agent/图/只读工具适配
    ui/                             # App UI，Streamlit 页面
  tests/
    core/                           # Agent Core
    ui/                             # App UI
    integration/                    # Main
  assets/demo_seed.json             # Main，全部虚构数据
  .worktrees/                       # Git 忽略，包含两个独立 worktree
    agent-core/                     # feature/agent-core
    app-ui/                         # feature/app-ui
```

所有开发分支在 Main 首次提交并冻结 `docs/contracts.md` 后创建。如果确实需要修改公共契约：功能分支先写申请，不直接编辑；Main 修改并提交，然后功能分支同步 `main`。

## 13. 仓库、分支、工作树与 Windows 安全

- Main：`D:\CampusOps`，分支 `main`；Agent Core：`D:\CampusOps\.worktrees\agent-core`，分支 `feature/agent-core`；App UI：`D:\CampusOps\.worktrees\app-ui`，分支 `feature/app-ui`。
- 主仓库根目录加 `.gitignore`：`/.worktrees/`、`/.venv/`、`/.env`、`/__pycache__/`、`/.pytest_cache/`、`/data/*.db*`、`/.cache/`、`/.tmp/` 等。
- 三个聊天固定目录；任何聊天都不得擅自切换到其他工作树写文件。Agent Core / App UI 不自行合并 main，Main 不未经检查强制覆盖功能分支改动。
- 每个阶段完成后发送最小信号：分支名、commit SHA、完成项、测试结果、是否请求契约变更、剩余问题。Main 验收后 `git merge --no-ff`；不要频繁广播所有聊天完整日志。
- 如果原 D:\CampusOps 目录存在文件，Main 必须先识别并暂停初始化以避免覆盖，不允许删除未知目录或历史成果。
- 初始化 GitHub 远端仅在用户授权与可用认证时进行；没有远端也应先可本地完成，不创建伪链接。

## 14. UI 页面及操作细节

### 页面 A：设备/预约
- 设备台账（名称、类别、能力标签、状态）、预约表（匿名用户、起止时间、要求）及虚构数据重置。
- 仅管理员可以通过本地演示操作登记器材故障；本版不做用户注册。

### 页面 B：异常智能调度
- 事故时间、受影响预约、调度运行状态、候选替代方案、每一项变更的前后对照。
- 显示硬约束结果、方案证据、需人工处理原因；审批按钮只调用 `service.approve_proposal`。
- 任何按钮点击都有稳定幂等标识，避免 Streamlit 重跑与双击造成重复写库。

### 页面 C：Agent 监控与配置
- 每个实际节点的时间、角色、工具、分支选择理由、执行结果、重试次数、预算消耗。
- Agent 启停、能力/允许工具查看与少量安全配置；模型 key 通过本地环境变量，不显示完整值。
- 离线测试、故障注入、真实 API 运行必须用明显不同的标识。

## 15. 测试矩阵与定量指标

### 必须通过的 14 组用例
1. 故障但无人受影响 → `NO_IMPACT`，无需模型调用。
2. 故障影响一条预约 + 合法替代 → 可执行方案。
3. 故障影响三条预约 → 并行查询、最终全局一致。
4. 候选器材缺少能力标签 → 不得通过。
5. 候选器材状态不是 AVAILABLE → 不得通过。
6. 时间区间重叠 → 不得通过（边界 `end == start` 可按半开区间允许）。
7. 同一新方案内部两条替代预约争用同一设备 → 不得通过。
8. 所有候选不可行 → `NEEDS_MANUAL`。
9. 模型输出错误结构/不存在设备 → 受控失败或纠正，无非法写库。
10. 验证 Agent 反馈后最多重规划2次，超过上限停止。
11. Agent 配置禁用/工具权限不足 → 路由变化或明确阻断。
12. 管理员拒绝 → 方案不执行，保留记录。
13. 方案产生后设备/预约发生变化 → 审批拒绝并回滚。
14. 同一审批重复提交、API 超额/超时/异常 → 不重复改库、不无声成功。

### 需留存的量化证据
- 事务实际成功的计划硬约束违规数（**验收目标为零**，不预先声称已达成）。
- 真实 API 调用次数、Token 消耗（服务商有数据才记）、处理耗时。
- 纯规则基线 vs 单 Agent vs 多 Agent 的可行方案率/日志可追溯性/额外调用成本；若无证据，不宣称 Agent 优于基线。
- 决赛若需要压力测试，使用虚构数据批量运行，并明确区分 Mock 与真实 LLM 调用。

## 16. 截止日期与里程碑（2026-11-05 至 11-20）

| 日期 | 主开发/ Codex | 队友 A | 队友 B | 硬门槛 |
|---|---|---|---|---|
| 11/05—06 | Main 初始化、契约冻结、虚构数据库与测试骨架，创建两个 worktree | 设备/预约/规则样例、异常用例 | 产品需求、展示结构和方案书框架 | 公共服务能正常读取虚构数据 |
| 11/07—09 | 确定性候选搜索、冲突校验、审批写事务基础 | 核对硬约束标准答案 | PPT 草图、UI 流程 | **故障→影响→替代候选→验证** 跑通 |
| 11/10—12 | 三个真实 Agent、能力注册、LangGraph 图和模型 API | 核对工具日志与真实输出 | 文档中的架构/创新部分 | **真实多 Agent 调用及动态路由证据** |
| 11/13—15 | 并行/反馈循环、审批、幂等和全局联调 | 回归异常测试 | 完成方案书主要章节 | **真实审批改库且无非法计划** |
| 11/16—17 | 功能冻结、错误修复、回放与性能记录 | 测试结果表 | 方案书、PPT、视频脚本 | 至少3个可复现演示场景 |
| 11/18—19 | README 复现、清理密钥、录制支持 | 完整回归 | PDF ≥5000 字、PPT 10—15 页、演示视频 3—5 分钟 | 材料与程序可复现 |
| 11/20 | 仅应急修复/提交 | 核对 | 上传并核实 | 提交成功状态 |

**止损标准**：11/09 无真实数据库规则流程，删除 P1 并优先完成单故障；11/12 无真实模型端到端调用，停止 UI 美化并定位核心；11/15 审批事务无法验证，不得称已经实现完整闭环。不能为了赶时间用伪造日志或编造评测数据。

## 17. 比赛官方材料（按用户提供的赛题细则核对）

- 初赛方案书 PDF：不少于 5000 字，需写场景分析、架构、Agent 设计/调度/验证/反馈、核心模块、测试与应用效果、人工智能实际作用与限制。
- PPT：10—15 页，突出编排引擎、调度路径、执行监控和创新机制。
- 3—5 分钟真实演示视频：显示异常触发、Agent 协作、验证反馈、审批实际写库和执行追踪。
- 可运行代码仓库地址或在线演示环境：README 说明依赖、虚构数据初始化、环境变量及限制；不得提交真实密钥。
- 赛题允许使用开源框架但禁止只交默认 Demo，需在调度、验证、自循环等方面提供自定义实现。遵守对应开源许可证，并在材料中说明借鉴与自研部分。
- 正式平台可能追加命名/证明材料要求，以提交页面即时要求为准。

## 18. 对答辩与就业的真实价值

- 可展示：任务编排、工具权限、数据结构、事务一致性、幂等接口、日志、端到端测试、团队协作。
- 对 Java 后端就业是**工程思想的迁移价值**，而不是替代 Java/Spring Boot 学习。
- 对评委明确说明：CampusOps 在本次初赛仅支持虚构社团器材，尚未接入真实学校系统；其他资源场景是可扩展设计，而非已实现功能。
- 不能宣称一定获奖、完全自动化、百分百准确或永久免费。

## 19. 权威技术参考（开发时核对版本）

- LangGraph Graph API：https://docs.langchain.com/oss/python/langgraph/graph-api
- LangGraph Interrupts：https://docs.langchain.com/oss/python/langgraph/interrupts
- SQLite checkpoint：https://reference.langchain.com/python/langgraph.checkpoint.sqlite
- Git worktree：https://git-scm.com/docs/git-worktree
- Python sqlite3 事务：https://docs.python.org/3/library/sqlite3.html
- Streamlit Session State：https://docs.streamlit.io/develop/concepts/architecture/session-state

**本文件为唯一技术设计基线。任何与 V1.0 冲突的内容，以 V2.0 细化后的 P0 范围、主线契约和验收规则为准。**
