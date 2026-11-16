"""Chinese presentation only. Persisted identifiers/evidence are never rewritten."""
import re

ERROR_LABELS = {
    "NOT_IMPLEMENTED": "此能力尚未接入", "NOT_FOUND": "未找到记录", "INVALID_STATE": "当前状态不允许此操作",
    "INVALID_INPUT": "输入信息不完整或格式有误", "INVALID_PROPOSAL": "方案未通过检查",
    "RESOURCE_UNAVAILABLE": "器材不可用", "CAPABILITY_MISMATCH": "器材能力不匹配",
    "RESERVATION_OVERLAP": "预约时间冲突", "SNAPSHOT_CONFLICT": "资源状态已变化",
    "INCOMPLETE_PLAN": "方案未覆盖全部受影响预约", "IDEMPOTENCY_CONFLICT": "重复请求内容不一致",
    "ALREADY_DECIDED": "此方案已有处理决定", "DATABASE_BUSY": "数据正在处理中，请稍后核对",
    "DATABASE_ERROR": "数据操作未完成", "WORKFLOW_FAILED": "本次处理未完成",
    "PROVIDER_UNAVAILABLE": "模型未就绪，请检查启用状态与配置", "BUDGET_EXCEEDED": "调用额度或任务限额不足",
}
ROLE_LABELS = {"impact": "影响分析", "planner": "调度规划", "verifier": "结果验证"}
TOOL_LABELS = {
    "get_incident": "读取故障记录", "get_impacted_reservations": "查询受影响预约",
    "list_equipment_candidates": "查找替代器材", "get_booking_rules": "查询借用规则",
    "get_snapshot": "读取资源快照", "list_agent_configs": "读取角色配置",
    "get_model_usage": "查询调用记录", "validate_candidate_plan": "校验候选方案",
    "append_run_step": "追加执行记录", "solve_candidates": "求解替代组合",
}
SKILL_LABELS = {
    "impact_analysis": "影响分析", "constraint_extraction": "提取约束", "scheduling": "调度规划",
    "plan_generation": "生成方案", "verification": "结果验证", "constraint_validation": "约束检查",
    "resource_planning": "资源规划", "schedule_planning": "调度规划", "plan_verification": "方案核验",
}
REASON_LABELS = {
    "preserve_times": "保留原预约时间", "capabilities_match": "满足预约所需能力",
    "minimize_category_change": "优先选择相同类别", "stable_rank": "按稳定顺序选择候选",
    "minimal_changes": "尽量减少调整", "NO_FEASIBLE_PLAN": "没有满足全部约束的替代方案",
    "REPLAN_LIMIT_REACHED": "已达到重新规划上限", "RUN_MODEL_BUDGET_EXCEEDED": "本次模型请求额度已用完",
    "TASK_DEADLINE_EXCEEDED": "本次任务已超时", "REQUIRED_AGENT_UNAVAILABLE": "必需的智能体未启用或能力不足",
}
NODE_LABELS = {
    "change.route": "分派预约修改角色", "change.replan": "根据反馈重新规划预约修改", "change.failed": "预约修改安全停止",
    "main.impact_scan": "检查故障影响", "main.result": "保存处理结果",
    "core.route": "选择可用角色", "core.impact": "分析受影响预约",
    "core.candidates": "查询替代器材", "core.plan": "选择调度方案", "core.verify": "独立验证方案",
    "core.plan_ready": "方案通过，交由人工审批", "core.plan_rejected": "拒绝无效方案",
    "core.replan": "根据反馈重新规划", "core.manual": "转交人工处理", "core.failed": "安全停止",
    "core.tool": "执行受限工具", "core.model": "请求模型决策", "core.model_request": "请求模型决策",
    "core.model_response": "记录模型响应", "core.tool_observation": "回传工具观察",
}


def borrower_label(value):
    match = re.fullmatch(r"borrower-(\d+)", str(value))
    return f"匿名借用者 {int(match[1]):02d}" if match else str(value)


def agent_label(value):
    for role, label in ROLE_LABELS.items():
        if str(value) in (role, "agent-" + role, role + "-agent", "agent_" + role):
            return label + "智能体"
    return str(value)


def node_label(value):
    if value in NODE_LABELS:
        return NODE_LABELS[value]
    if str(value).startswith("tool."):
        return TOOL_LABELS.get(str(value)[5:], "执行受限工具")
    return "已记录执行步骤（原始名称见详情）" if re.search(r"[A-Za-z]", str(value)) else str(value)


def chinese_summary(value):
    """Translate known program templates; never claim to translate arbitrary model prose."""
    text = str(value)
    match = re.fullmatch(
        r"Planner selected (plan_\d+) from (\d+) bounded deterministic offers; reasons=([^.]*)\. "
        r"All (\d+) affected bookings keep their time windows\. "
        r"Main readonly hard validation and independent verifier passed; "
        r"model_calls=(\d+);replans=(\d+);search_complete=(True|False)\. "
        r"Administrator approval and transactional recheck required\.", text)
    if match:
        reasons = "；".join(REASON_LABELS.get(item, "其他已记录的选择依据") for item in match[3].split(",") if item)
        return (f"规划智能体从 {match[2]} 个候选方案中选出当前方案，涉及 {match[4]} 条预约，原预约时间不变。"
                f"选择依据：{reasons or '见原始记录'}。程序硬约束与独立验证均通过。"
                f"本次记录模型调用 {match[5]} 次、重新规划 {match[6]} 次；"
                + ("候选搜索已完成。" if match[7] == "True" else "候选搜索受限，未证明已穷尽全部方案。")
                + "最终执行仍需人工批准，并在提交时再次检查资源状态。")
    if text in REASON_LABELS:
        return REASON_LABELS[text]
    if text.startswith("OFFLINE") or "offline test" in text.lower():
        return "离线测试记录：使用明确注入的替身数据，未调用真实模型。"
    if "No active reservations overlap" in text or text.startswith("NO_IMPACT:"):
        return "没有与故障时段相交的有效预约，本次未调用模型。"
    if text == "Approved and atomically applied to fictional bookings":
        return "已批准并在同一事务中更新虚构预约。"
    if text == "Administrator rejected the proposal; bookings were unchanged":
        return "已拒绝方案，预约保持不变。"
    if text == "Deterministic incident impact scan":
        return "通过程序规则检查本次故障影响的预约。"
    if re.search(r"[\u4e00-\u9fff]", text):
        return text
    return "此项原始记录未提供中文说明，可展开原始记录核对。"
