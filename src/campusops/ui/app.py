"""Shared GearShift pages. Main's formal launcher is streamlit_app.py."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

import streamlit as st

from campusops.bootstrap import create_application
from campusops.contracts.configuration import ROLE_TOOL_LIMITS, SUPPORTED_PROMPT_VERSIONS
from campusops.contracts.errors import CampusOpsError
from campusops.contracts.models import AgentRuntimeConfigPatch, ApplyStatus, BookingRuleCatalog, ExecutionMode, RunStatus, WorkflowRun
from campusops.contracts.ports import ApplicationPort, BookingRulesPort, IdempotentWorkflowPort, EquipmentMaintenancePort, EquipmentCatalogPort
from campusops.ui.incidents import current_incidents,incident_label
from campusops.ui.controller import RESET_CONFIRMATION, UiController, UiInputError, beijing_effective_at, can_decide
from campusops.ui.presentation import (
    DISPLAY_TZ, EQUIPMENT_LABELS, MODE_LABELS, STATUS_LABELS, booking_rules_html, change_cards_html, change_rows, equipment_cards_html, equipment_name,
    equipment_rows, local_time, record_table_html, reservation_cards_html, reservation_rows, safe_text,
    status_label, task_steps_html,
)
from campusops.ui.styles import APP_STYLES
from campusops.ui.labels import ERROR_LABELS, ROLE_LABELS, TOOL_LABELS, SKILL_LABELS, agent_label, chinese_summary, node_label

PAGES = ("资源与预约", "异常调度与审批", "处理记录与设置")
PAGE_IDS = {PAGES[0]: "resources", PAGES[1]: "workflow", PAGES[2]: "monitor"}
ACTIVE_STATES = {
    RunStatus.CREATED, RunStatus.SCOPING, RunStatus.PLANNING, RunStatus.VERIFYING,
    RunStatus.REPLANNING, RunStatus.WAIT_APPROVAL, RunStatus.APPLYING,
}


def _flash(level: str, message: str):
    st.session_state["campusops_flash"] = (level, safe_text(message))
    st.rerun()


def _show_error(error):
    st.error(ERROR_LABELS.get(error.code, "操作未完成，请刷新核对。"))
    if any("\u4e00" <= char <= "\u9fff" for char in error.message):
        st.caption(safe_text(error.message))
    with st.expander("错误原始记录（供维护者核对）"):
        st.text(f"{error.code} · {safe_text(error.message)}")


def _table_mode():
    view = os.environ.get("CAMPUSOPS_TABLE_VIEW", "")
    if view not in ("", "html"):
        raise UiInputError("表格显示配置无效，请联系维护者检查。")
    return view


def _table(rows):
    view = _table_mode()
    if rows:
        if view == "html":
            st.html(record_table_html(rows), unsafe_allow_javascript=False)
            return
        try:
            st.dataframe(rows, hide_index=True, width="stretch")
        except (ImportError, OSError):
            st.warning("表格视图暂不可用，以下以完整记录表格展示应用服务数据。")
            st.html(record_table_html(rows), unsafe_allow_javascript=False)
    else:
        st.info("暂无记录。")


def _report_render_failure(error: Exception, page: str):
    """Emit metadata only; never format exception messages, causes or source lines."""
    frames = []
    trace = error.__traceback__
    while trace is not None:
        code = trace.tb_frame.f_code
        frames.append({"file": os.path.basename(code.co_filename), "function": code.co_name, "line": trace.tb_lineno})
        trace = trace.tb_next
    kind = type(error)
    diagnostic = {
        "page": PAGE_IDS.get(page, "unknown"),
        "exception_type": f"{kind.__module__}.{kind.__qualname__}",
        "frames": frames,
    }
    if sys.stderr is not None:
        try:
            sys.stderr.write("CAMPUSOPS_UI_RENDER_FAILURE " + json.dumps(diagnostic, ensure_ascii=True) + "\n")
            sys.stderr.flush()
        except (OSError, ValueError):
            # A closed/unwritable diagnostic stream must not replace the fixed UI failure.
            pass


def _selection(records, *, widget_key, selected_key):
    """Resolve saved widget/context before deciding which section to render first."""
    ids = [row.id for row in records]
    if not ids:
        return None
    by_id = {row.id: row for row in records}
    if st.session_state.get(widget_key) not in ids:
        saved = st.session_state.get(selected_key)
        st.session_state[widget_key] = saved if saved in ids else ids[0]
    selected = st.session_state[widget_key]
    st.session_state[selected_key] = selected
    return by_id[selected]


def _pick(label, records, *, widget_key, selected_key, formatter):
    from campusops.security.streamlit_gate import touch_user_activity
    if _selection(records, widget_key=widget_key, selected_key=selected_key) is None:
        return None
    ids = [row.id for row in records]
    by_id = {row.id: row for row in records}
    selected = st.selectbox(label, ids, format_func=lambda key: formatter(by_id[key]), key=widget_key, on_change=touch_user_activity)
    st.session_state[selected_key] = selected
    return by_id[selected]


def _navigate(page, incident_id=None, run_id=None, historical=False):
    """Widget callback: context/navigation only, never a service command."""
    from campusops.security.streamlit_gate import touch_user_activity
    touch_user_activity()
    st.session_state["ui_page"] = page
    if incident_id is not None:
        st.session_state['incident_view']='历史记录' if historical else '当前待处理'
        st.session_state["campusops_incident_id"] = incident_id
        st.session_state.pop("incident_picker", None)
    if run_id is not None:
        st.session_state["campusops_run_id"] = run_id
    st.session_state.pop("workflow_run_picker", None)
    st.session_state.pop("trace_run_picker", None)


def _set_incident_clock(epoch, demo):
    moment = datetime(2026, 11, 11, 8, tzinfo=DISPLAY_TZ) if demo else datetime.now(DISPLAY_TZ)
    st.session_state[f"incident_date_{epoch}"] = moment.date()
    st.session_state[f"incident_clock_{epoch}"] = moment.time().replace(second=0, microsecond=0, tzinfo=None)
    st.session_state[f"incident_confirm_{epoch}"] = False


def _advance_intent(counter_key, current):
    # Preparing another request never dispatches it; confirmation remains separate.
    st.session_state[counter_key] = current + 1


def _affected(incident, reservations):
    # Display only. Main still revalidates the complete snapshot during approval.
    return tuple(row for row in reservations if incident.status == "OPEN" and row.equipment_id == incident.equipment_id
                 and row.status == "ACTIVE" and row.ends_at > incident.effective_at)


def _validation(validation):
    st.write("规划时硬约束校验：" + ("通过" if validation.passed else "未通过"))
    for violation in validation.violations:
        st.warning(ERROR_LABELS.get(violation.code, "约束检查未通过，请核对明细。"))
    if validation.issues and not validation.violations:
        st.warning(chinese_summary(safe_text(validation.issues[0])))
    with st.expander("校验原始记录与证据编号"):
        for issue in validation.issues:
            st.text(safe_text(issue))
        st.text("证据：" + (safe_text("、".join(validation.evidence_ids)) or "未提供"))


def _run_status(run):
    st.subheader(STATUS_LABELS.get(run.status, "处理状态"))
    st.caption(f"{MODE_LABELS[run.execution_mode]} · 已记录模型调用 {run.model_calls} 次")
    with st.expander("运行编号与技术状态"):
        st.text(f"运行编号：{run.id}\n技术状态：{run.status}\n执行模式代码：{run.execution_mode}\n创建：{local_time(run.created_at)} 北京时间\n"
                f"提供商：{safe_text(run.provider or '未记录')}\n模型：{safe_text(run.model_id or '未记录')}")
    if run.status == RunStatus.STALE:
        st.warning("方案已过期，此次审批未更新预约。请核对资源后重新规划。")
        st.info("下一步：查看最新器材与预约，再由你明确点击重新生成。旧方案不能继续批准。")
    elif run.status == RunStatus.NEEDS_MANUAL:
        st.warning("当前没有可直接审批的可行方案，需要管理员处理。")
        st.info("下一步：核对器材能力、可用状态与占用时间，联系管理员协调；现有预约保持原样。")
    elif run.status == RunStatus.REJECTED:
        st.info("方案已拒绝，预约未因拒绝而更改。历史决定与原因见持久审批记录。")
    elif run.status == RunStatus.NO_IMPACT:
        st.info("没有受影响的有效预约，本次无需调用模型。")
        st.caption("无影响是有效的程序规则结果，不表示模型成功。种子预约固定在 2026年11月10日至11日；"
                   "故障晚于预约结束时可能没有影响。应用不会自动改时间；若体验固定虚构数据，请主动选择演示时段。")
    elif run.status == RunStatus.APPLIED:
        st.info("预约已更新。持久运行状态为已执行，下方对照显示当前预约。")
    elif run.status == RunStatus.FAILED and not run.error:
        st.error("本次处理未完成，请检查持久运行详情。")
    if run.error:
        st.error("尚未生成可执行方案；请按下方说明处理，当前不表示模型成功。")
        if run.error.code == "PROVIDER_UNAVAILABLE":
            st.info("下一步：请维护者检查模型配置与明确启用状态，再由你主动生成方案。刷新不会重试，当前不表示模型成功。")
        else:
            st.info("下一步：先刷新持久状态核对结果，再联系维护者检查；不要直接重复提交。")
        with st.expander("错误详情 · 供维护者核对"):
            _show_error(run.error)
    if run.result:
        with st.expander("服务结果说明"):
            st.write(chinese_summary(safe_text(run.result.explanation)))
            with st.expander("未经改写的原始说明"):
                st.text(safe_text(run.result.explanation))


def _approval_record(service: ApplicationPort, run: WorkflowRun):
    proposal_id = run.result.proposal_id if run.result else None
    if proposal_id is None:
        return
    st.subheader("持久审批记录")
    try:
        decision = service.get_approval_decision(proposal_id)
    except CampusOpsError as exc:
        _show_error(exc.error)
        st.warning("审批记录查询未完成，历史操作人、原因和时间尚未确认。")
        return
    except Exception:
        st.warning("审批记录暂不可读取，历史操作人、原因和时间未知。")
        return
    if decision is None:
        if run.status in (RunStatus.APPLIED, RunStatus.REJECTED):
            st.warning("运行已有决定状态，但未读到对应审批记录；历史详情尚未确认。")
        else:
            st.info("当前方案尚无持久审批决定。")
        return
    if decision.proposal_id != proposal_id:
        st.warning("审批记录与当前方案不匹配，历史详情尚未确认。")
        return
    label = "批准" if decision.decision == "APPROVE" else "拒绝"
    st.text(
        f"决定：{label}\n操作人：{safe_text(decision.actor_alias)}\n"
        f"决定时间（北京时间）：{local_time(decision.created_at)}"
    )
    if decision.decision == "REJECT":
        st.text("持久拒绝原因：" + safe_text(decision.reason if decision.reason is not None else "未提供"))


def _model_budget(service: ApplicationPort, run: WorkflowRun | None):
    st.subheader("模型调用预算")
    st.caption("范围：当前工作树 / UTC / 保守调用预留。失败、超时和未知结果也占预留；不表示成功响应、Token 或服务商账户金额。")
    if run is None:
        st.info("暂无运行，选择一条持久运行后读取权威预算快照。")
        return
    try:
        snapshot = service.get_model_budget(run.id)
    except CampusOpsError as exc:
        _show_error(exc.error)
        st.warning("预算查询未完成，保守预留及剩余可预留调用均未知。")
        return
    except Exception:
        st.warning("预算快照暂不可读取，保守预留及剩余可预留调用均未知。")
        return
    if snapshot.run_id != run.id:
        st.warning("预算快照与当前运行不匹配，保守预留及剩余可预留调用均未知。")
        return
    st.text(
        f"快照状态：{'可读取' if snapshot.status == 'AVAILABLE' else '暂不可用'}\n账本日期（UTC）：{snapshot.utc_date.isoformat()}\n"
        f"观察时间（北京时间）：{local_time(snapshot.observed_at)}"
    )
    if snapshot.status == "UNAVAILABLE":
        st.warning("权威预算暂不可用，保守预留及剩余可预留调用均未知。")
        _show_error(snapshot.error)
    _table([{
        "运行上限": snapshot.per_run_limit if snapshot.per_run_limit is not None else "未知",
        "本运行保守预留": snapshot.run_reserved if snapshot.run_reserved is not None else "未知",
        "本运行剩余可预留调用": snapshot.run_remaining if snapshot.run_remaining is not None else "未知",
        "UTC 每日上限": snapshot.daily_limit if snapshot.daily_limit is not None else "未知",
        "UTC 当日保守预留": snapshot.daily_reserved if snapshot.daily_reserved is not None else "未知",
        "UTC 当日剩余可预留调用": snapshot.daily_remaining if snapshot.daily_remaining is not None else "未知",
    }])
    st.caption("快照只说明观测时刻；下一次调用仍由预算锁核验。业务运行的实际模型调用次数单独显示在上方。")


def _published_rules(service: ApplicationPort):
    with st.expander("借用规则 · 当前发布版本与来源"):
        st.caption("这里展示当前发布规则。历史运行使用的规则版本以各自冻结记录为准；当前内容不会回填为旧历史。")
        if not isinstance(service, BookingRulesPort):
            st.info("当前服务未提供规则查看能力，当前发布规则暂不可用。")
            return
        try:
            catalog = BookingRuleCatalog.model_validate(service.get_booking_rules())
        except CampusOpsError as exc:
            _show_error(exc.error)
            st.warning("当前发布规则查询未完成，版本、来源和正文尚未确认。")
            return
        except Exception:
            st.warning("当前发布规则暂不可读取，版本、来源和正文尚未确认。")
            return
        st.caption(f"当前目录共 {len(catalog.rules)} 条虚构规则；仅供 GearShift 器材故障场景，不提供编辑，也不放宽确定性硬约束。")
        st.html(booking_rules_html(catalog), unsafe_allow_javascript=False)


def _resources(controller: UiController, reset_target: Path | None):
    from campusops.security.streamlit_gate import touch_user_activity
    service = controller.service
    with st.spinner("读取器材与预约…"):
        equipment = service.list_equipment()
        reservations = service.list_reservations()
        incidents = service.list_incidents()
        runs = service.list_runs()
    all_equipment=equipment
    archived=frozenset(row.equipment.id for row in service.list_equipment_catalog(True) if row.archived) if isinstance(service,EquipmentCatalogPort) else frozenset()
    equipment=tuple(row for row in equipment if row.id not in archived)
    live=current_incidents(incidents,equipment,archived)
    latest = {}
    for run in sorted(runs, key=lambda row: (row.created_at, row.id), reverse=True):
        latest.setdefault(run.incident_id, run)
    waiting = [row for row in latest.values() if row.status == RunStatus.WAIT_APPROVAL]
    pending = [row for row in live if
               (row.id not in latest or latest[row.id].status in (RunStatus.FAILED, RunStatus.STALE, RunStatus.NEEDS_MANUAL))]
    if waiting:
        first = waiting[0]
        st.button(f"查看待审批方案（{len(waiting)} 项）", key="open_pending_approval",
                  on_click=_navigate, args=(PAGES[1], first.incident_id, first.id))
    registered = next((row for row in live if row.id == st.session_state.get("campusops_last_incident_id")), None)
    if registered is not None:
        with st.container(border=True):
            st.subheader("下一步：查看如何安排受影响预约")
            st.write(f"{equipment_name(registered.equipment_id, equipment)} · 当前仍受影响 {len(_affected(registered, reservations))} 条预约")
            st.caption(f"故障从 {local_time(registered.effective_at)} 北京时间生效。这里不会自动调用模型。")
            st.button("继续处理本次故障", type="primary", key="continue_incident", on_click=_navigate,
                      args=(PAGES[1], registered.id))
    elif pending:
        first = pending[0]
        st.button(f"继续处理：{equipment_name(first.equipment_id, equipment)}", key="continue_pending_incident",
                  on_click=_navigate, args=(PAGES[1], first.id))
    st.subheader("器材与预约工作台")
    st.caption("在“预约管理”中新增、修改或取消预约；故障处理与维修分别操作。修改方案须单独审批。")
    metrics = st.columns(4)
    metrics[0].metric("可用器材", sum(row.status == 'AVAILABLE' for row in equipment))
    metrics[1].metric("故障 / 维护", sum(row.status != 'AVAILABLE' for row in equipment))
    metrics[2].metric("待审批方案", len(waiting))
    metrics[3].metric("有效预约", sum(row.status == 'ACTIVE' for row in reservations))
    overview, bookings, registration, recovery, catalog = st.tabs(("器材概览", "预约管理", "登记故障", "维修恢复", "器材管理"),
        key="campusops_resources_tab",on_change=touch_user_activity)
    with overview:
        from campusops.ui.catalog import open_catalog
        st.button('新增或管理器材',key='open_equipment_catalog',on_click=open_catalog,args=('器材列表',),disabled=controller.read_only)
        search_column, filter_column = st.columns([2, 1])
        query = search_column.text_input("搜索器材", placeholder="输入器材名称或能力，例如：相机", key="equipment_search", on_change=touch_user_activity)
        status = filter_column.selectbox("器材状态", ("全部状态", "可用", "故障", "维护中"), key="equipment_filter", on_change=touch_user_activity)
        from campusops.ui.presentation import capabilities_label
        filtered = [row for row in equipment if (status == "全部状态" or EQUIPMENT_LABELS[row.status] == status)
                    and (not query.strip() or query.strip().casefold() in
                         (row.name + " " + capabilities_label(row.capabilities)).casefold())]
        st.caption(f"显示 {len(filtered)} / {len(equipment)} 件器材 · 故障处理请切换“登记故障”；维修后请切换“维修恢复”。")
        if filtered:
            st.html(equipment_cards_html(filtered), unsafe_allow_javascript=False)
        else:
            st.info("没有匹配的器材，请调整关键词或状态筛选。")
    with registration:
        st.html(task_steps_html(1), unsafe_allow_javascript=False)
        st.subheader("登记器材故障")
        st.caption("登记仅标记故障，预约不变；生成方案须另行点击。")
        if not equipment:
            st.info("暂无可登记的器材，请联系维护者检查本地虚构数据。")
        epoch = st.session_state.get("campusops_incident_form_epoch", 0)
        registration_attempted = controller.attempted(("register", f"form_{epoch}"))
        if registration_attempted:
            st.warning("本表单的登记请求已提交，结果未确认时不会重发。请先刷新并核对事故列表；不要把错误提示当作未保存。")
            st.button("已核对事故记录，准备新的登记", key=f"registration_retry_{epoch}",
                      disabled=controller.read_only, on_click=_advance_intent,
                      args=("campusops_incident_form_epoch", epoch))
        by_id = {row.id: row for row in equipment}
        now = datetime.now(DISPLAY_TZ)
        st.session_state.setdefault(f"incident_date_{epoch}", now.date())
        st.session_state.setdefault(f"incident_clock_{epoch}", now.time().replace(second=0, microsecond=0, tzinfo=None))
        with st.form(f"incident_form_{epoch}", enter_to_submit=False):
            equipment_column, date_column, time_column = st.columns([1.7, 1.2, 1])
            equipment_id = equipment_column.selectbox(
                "哪件器材发生故障？", tuple(by_id), key=f"incident_equipment_{epoch}",
                format_func=lambda key: f"{safe_text(by_id[key].name)} · {EQUIPMENT_LABELS[by_id[key].status]}",
            )
            day = date_column.date_input("故障日期（北京时间）", value=None, key=f"incident_date_{epoch}",
                                         help="北京时间：Asia/Shanghai，UTC+08:00。按所选日期和时间生效，原预约时间不变。")
            clock = time_column.time_input("开始时间（北京时间）", value=None, step=60, key=f"incident_clock_{epoch}",
                                           help="北京时间：Asia/Shanghai，UTC+08:00。提交时显式转换为含时区 UTC。")
            with st.expander("演示时段（虚构数据，需主动选择）"):
                st.caption("固定种子数据覆盖 2026年11月10日至11日；单预约演示使用 11月11日 08:00。只填写时间，不登记、不生成方案。")
                st.form_submit_button("填入演示时间 · 11月11日 08:00", key=f"demo_time_{epoch}",
                                      on_click=_set_incident_clock, args=(epoch, True))
                st.form_submit_button("改回当前北京时间", key=f"current_time_{epoch}",
                                      on_click=_set_incident_clock, args=(epoch, False))
            reason = st.text_area("发生了什么？", max_chars=500, height=68,
                                  placeholder="例如：相机无法开机，需要安排替代器材", key=f"incident_reason_{epoch}")
            confirmed = st.checkbox("我已确认器材故障；登记后保留原预约", key=f"incident_confirm_{epoch}")
            submitted = st.form_submit_button("登记故障", type="primary", width="stretch",
                                              disabled=not equipment or controller.read_only or registration_attempted,
                                              key=f"incident_submit_{epoch}")
        if submitted:
            effective_at = beijing_effective_at(day, clock)
            with st.spinner("保存事故并读取登记结果…"):
                incident = controller.register_failure(equipment_id, effective_at.isoformat(), reason, confirmed=confirmed,
                                                       submission_id=f"form_{epoch}")
            st.session_state["campusops_last_incident_id"] = incident.id
            st.session_state["campusops_incident_form_epoch"] = epoch + 1
            _flash("info", "故障已登记，预约尚未修改。下一步：继续处理本次故障。")
    with recovery:
        _recovery_panel(controller, equipment, incidents)
    with bookings:
        from campusops.ui.bookings import render_reservations
        render_reservations(controller, all_equipment, reservations)
    with catalog:
        from campusops.ui.catalog import render_catalog
        render_catalog(controller)
    _published_rules(service)
    with st.expander("技术台账 · 全部原始字段"):
        columns = st.columns(3)
        columns[0].metric("虚构器材", len(equipment))
        columns[1].metric("预约记录", len(reservations))
        columns[2].metric("事故记录", len(incidents))
        st.subheader("器材台账")
        _table(equipment_rows(equipment))
        st.subheader("预约记录")
        _table(reservation_rows(reservations))
        st.subheader("事故列表")
        _table([{
            "故障编号": row.id, "器材编号": row.equipment_id,
            "生效时间（北京时间）": local_time(row.effective_at), "原因": safe_text(row.reason), "状态": {"OPEN": "待处理", "RESOLVED": "已解决"}[row.status],
        } for row in incidents])
    with st.expander("高级设置 · 重置本地虚构演示数据"):
        if controller.read_only:
            st.info("只读回看不开放虚构库重置。")
            return
        if reset_target is None:
            st.info("当前服务目标未确认为本工作树的 var/data/campusops.db，界面不开放重置。")
            return
        st.text(f"重置目标：{reset_target}")
        st.caption("由服务备份后重建虚构数据；当前库的事故、方案与运行记录会从当前列表清空，备份保留。")
        if not st.session_state.get("campusops_reset_pending"):
            if st.button("申请重置虚构库", key="reset_request"):
                st.session_state["campusops_reset_pending"] = True
                st.rerun()
        else:
            st.warning("第二次确认：请核对上方目标，并输入确认短语。")
            phrase = st.text_input("输入 RESET_FICTIONAL_DEMO", key="reset_phrase")
            confirmed = st.checkbox("我确认备份并重置此虚构演示库", key="reset_confirm")
            if st.button(
                "确认备份并重置虚构库", key="reset_execute",
                disabled=not confirmed or phrase != RESET_CONFIRMATION,
            ):
                with st.spinner("备份并重置虚构库…"):
                    controller.reset_demo(phrase, second_confirmation=confirmed, target_allowed=True)
                st.session_state.pop("reset_phrase", None)
                st.session_state.pop("reset_confirm", None)
                _flash("info", "服务已备份并重置虚构演示库，已重新读取持久数据。")
            if st.button("取消重置", key="reset_cancel"):
                st.session_state.pop("campusops_reset_pending", None)
                st.session_state.pop("reset_phrase", None)
                st.session_state.pop("reset_confirm", None)
                st.rerun()



def _recovery_panel(controller, equipment, incidents):
    st.subheader("维修完成，恢复可用")
    st.caption("仅在实际维修和功能检查完成后使用；这一步不调用模型，也不会自动搬回已调整的预约。")
    if not isinstance(controller.service, EquipmentMaintenancePort):
        st.info("当前服务未提供维修恢复功能，请维护者更新服务。")
        return
    damaged = [row for row in equipment if row.status in ("FAULTED", "MAINTENANCE")]
    if not damaged:
        st.success("当前没有故障或维护中的器材。")
        return
    by_id = {row.id: row for row in damaged}
    selected = st.selectbox("已维修的器材", tuple(by_id), key="recovery_equipment",
                            format_func=lambda key: f"{safe_text(by_id[key].name)} · {EQUIPMENT_LABELS[by_id[key].status]}")
    row = by_id[selected]
    opened = sum(item.equipment_id == selected and item.status == "OPEN" for item in incidents)
    st.info(f"恢复后将关闭此器材的 {opened} 条未解决故障，并使现有待审批方案过期。预约保持当前分配。")
    with st.form(f"equipment_recovery_{selected}_{row.version}", enter_to_submit=False):
        actor = st.text_input("维修确认人匿名别名", value="管理员", max_chars=100, key=f"recovery_actor_{selected}_{row.version}")
        note = st.text_area("维修与检查说明", max_chars=500, height=80, key=f"recovery_note_{selected}_{row.version}",
                            placeholder="例如：已更换电池，开机、拍摄和存储测试正常")
        confirmed = st.checkbox("我已完成维修和功能检查，确认恢复为可用", key=f"recovery_confirm_{selected}_{row.version}")
        submitted = st.form_submit_button("确认恢复可用", type="primary", width="stretch",
            disabled=controller.read_only or controller.attempted(("recovery", selected, row.version)), key="recovery_submit")
    if submitted:
        with st.spinner("保存维修记录并核对器材状态…"):
            result = controller.recover_equipment(selected, row.version, actor, note, confirmed=confirmed)
        _flash("success", f"{safe_text(row.name)}已恢复可用；已关闭 {len(result.resolved_incident_ids)} 条故障，"
               f"{len(result.invalidated_proposal_ids)} 项待审批方案已过期。预约保持原分配。")


def _incident_view_changed():
    from campusops.security.streamlit_gate import touch_user_activity
    touch_user_activity()
    for key in ('incident_picker','campusops_incident_id','workflow_run_picker','campusops_run_id'):
        st.session_state.pop(key,None)


def _history_view():
    _incident_view_changed();st.session_state['incident_view']='历史记录'


def _workflow(controller: UiController):
    service = controller.service
    with st.spinner("读取事故与运行历史…"):
        incidents = sorted(service.list_incidents(), key=lambda row: (row.created_at, row.id), reverse=True)
        all_runs = service.list_runs()
        equipment = service.list_equipment()
    if not incidents:
        st.html(task_steps_html(1), unsafe_allow_javascript=False)
        st.info("尚无故障记录。先选择出问题的器材，登记生效时间，再查看调整方案。")
        st.button("去登记器材故障", type="primary", key="empty_register", on_click=_navigate, args=(PAGES[0],))
        return
    archived=frozenset(row.equipment.id for row in service.list_equipment_catalog(True) if row.archived) if isinstance(service,EquipmentCatalogPort) else frozenset()
    view=st.radio('故障记录范围',('当前待处理','历史记录'),horizontal=True,key='incident_view',on_change=_incident_view_changed)
    history=view=='历史记录'
    if not history:
        records=current_incidents(incidents,equipment,archived)
        st.caption(f'当前待处理 {len(records)} 条；历史共 {len(incidents)} 条。已解决记录不表示器材当前故障。')
        if not records:
            st.success('当前没有待处理故障。器材是否故障以当前器材状态为准。')
            st.button(f'查看历史故障记录（{len(incidents)}条）',key='show_incident_history',on_click=_history_view)
            return
    else:
        records=incidents
        controller=UiController(controller.service,controller.state,read_only=True)
        st.info('历史记录只读回看。已解决故障不会重新生成方案或执行审批；完整记录保留。')
    incidents=records
    incident = _selection(incidents, widget_key="incident_picker", selected_key="campusops_incident_id")
    reservations = service.list_reservations()
    affected = _affected(incident, reservations)
    runs = sorted(
        (row for row in all_runs if row.incident_id == incident.id),
        key=lambda row: (row.created_at, row.id), reverse=True,
    )
    selected = _selection(runs, widget_key="workflow_run_picker", selected_key="campusops_run_id")
    current = controller.read_decision(selected.id) if selected else None
    focus = not history and current is not None and can_decide(current.run, current.proposal)
    if focus:
        # A validated pending decision is the primary task. Background/history follows it.
        _proposal_panel(controller, current, incident, equipment, focused=True)
    _workflow_context(controller, incidents, incident, equipment, affected, runs, current,history=history,archived=archived)
    if current is None:
        st.info("这条历史记录没有已保存方案；只读回看，不会重新生成。" if history else "这次故障还没有方案。确认器材和时间后，点击“生成调整方案”；当前未触发模型。")
        return
    _run_status(current.run)
    _approval_record(service, current.run)
    if current.proposal is None:
        if current.run.result:
            _validation(current.run.result.validation)
        if current.run.status == RunStatus.WAIT_APPROVAL:
            st.error("待审批运行缺少已保存方案，无法审批。")
        return
    if not focus:
        _proposal_panel(controller, current, incident, equipment, focused=False)


def _workflow_context(controller, incidents, incident, equipment, affected, runs, current,*,history=False,archived=frozenset()):
    _pick(
        "历史故障记录（只读）" if history else "当前待处理故障", incidents, widget_key="incident_picker", selected_key="campusops_incident_id",
        formatter=lambda row: incident_label(row,equipment,archived),
    )
    st.subheader(equipment_name(incident.equipment_id, equipment))
    current_equipment=next((row for row in equipment if row.id==incident.equipment_id),None)
    state='已移除' if incident.equipment_id in archived else EQUIPMENT_LABELS.get(current_equipment.status,'未找到') if current_equipment else '未找到'
    st.caption('器材当前状态：'+state+' · 此次记录：'+('已解决' if incident.status=='RESOLVED' else '未解决')+' · 记录编号：'+incident.id[-8:])
    st.text(f"生效：{local_time(incident.effective_at)} 北京时间\n原因：{safe_text(incident.reason)}")
    if incident.status == "RESOLVED":
        st.success("此器材故障已解决，不能再为旧故障生成方案。已调整的预约保持当前分配。")
    if runs:
        with st.expander("查看这次故障的历史运行"):
            _pick(
                "选择历史运行", runs, widget_key="workflow_run_picker", selected_key="campusops_run_id",
                formatter=lambda row: f"{local_time(row.created_at)} · {STATUS_LABELS.get(row.status, row.status)}",
            )
    if history:
        st.caption('历史回看不计为当前故障，不提供重新生成或审批操作。')
        return
    active = any(row.status in ACTIVE_STATES for row in runs)
    complete = current is not None and current.run.status in (RunStatus.APPLIED, RunStatus.REJECTED, RunStatus.NO_IMPACT)
    st.html(task_steps_html(3 if complete else 2), unsafe_allow_javascript=False)
    st.write(f"当前仍受影响：{len(affected)} 条预约 · 故障登记本身未改变原预约")
    if current is None or (current.run.status not in (RunStatus.APPLIED, RunStatus.REJECTED)
                           and not can_decide(current.run, current.proposal)):
        st.info("本次处理的预约尚未修改。先生成并核对方案，只有你批准后才会更新器材分配；当前分配以读回记录为准。")
    generation_key = f"campusops_start_generation_{incident.id}"
    generation = st.session_state.get(generation_key, 0)
    previous_run_id = runs[0].id if runs else None
    logical_key = controller.workflow_key(incident.id, previous_run_id)
    generation_attempted = controller.attempted(("workflow", incident.id, generation))
    if generation_attempted:
        st.warning("本轮生成请求已发送。请先核对运行历史；结果不明时不自动重试，也不把记录相似当作本次成功。")
        st.button("已核对运行记录，准备新一轮生成", key=f"workflow_retry_{incident.id}_{generation}",
                  disabled=controller.read_only or active or incident.status != "OPEN", on_click=_advance_intent,
                  args=(generation_key, generation))
    if st.button(
        "重新生成调整方案" if runs else "生成调整方案", key=f"start_workflow_{logical_key}_{generation}",
        disabled=controller.read_only or active or incident.status != "OPEN" or generation_attempted,
        type="primary" if not active else "secondary",
        width="stretch",
    ):
        with st.spinner("执行工作流；状态与轨迹由服务保存…"):
            saved = controller.start_workflow(incident.id, generation=generation, previous_run_id=previous_run_id)
        st.session_state["campusops_run_id"] = saved.id
        st.session_state[generation_key] = generation + 1
        st.session_state.pop("workflow_run_picker", None)
        _flash("info", "运行仍在处理中，已读取同一运行的持久状态；不会自动重发模型。"
               if saved.result is None else "已读取此次运行的持久状态。结果如下。")
    if not isinstance(controller.service, IdempotentWorkflowPort):
        st.caption("当前服务未提供持久启动去重；界面保护仅限本会话，重开页面前须核对运行历史。")
    if active:
        st.caption("已有方案正在处理或等待审批。请先查看当前方案，无需重复生成。")
    else:
        st.caption("仅点击生成按钮才会请求已配置的模型；刷新、登记和换页都不会自动调用。")
    with st.expander(f"查看当前受影响的预约（{len(affected)} 条）"):
        if affected:
            st.html(reservation_cards_html(affected, equipment), unsafe_allow_javascript=False)
        else:
            st.info("当前没有仍占用故障器材的有效预约；可查看历史结果，或手动确认无影响。")
        _table(reservation_rows(affected))


def _proposal_panel(controller, current, incident, equipment, *, focused):
    proposal = current.proposal
    assert proposal is not None
    eligible = can_decide(current.run, proposal)
    st.subheader(f"本方案涉及 {len(proposal.changes)} 条预约")
    if focused:
        st.caption(f"待人工审批 · {MODE_LABELS[current.run.execution_mode]} · 已记录模型调用 {current.run.model_calls} 次")
        st.text(f"{equipment_name(incident.equipment_id, equipment)} · 故障生效 {local_time(incident.effective_at)} 北京时间")
        st.warning(f"预约尚未修改。批准将更新 {len(proposal.changes)} 条预约的器材，原时间不变；提交时仍会重新校验资源与版本。")
    st.caption("先看器材如何替换；原预约时间保持不变。当前器材来自服务的持久读回。")
    st.html(change_cards_html(proposal, current.reservations, equipment), unsafe_allow_javascript=False)
    st.write("硬约束校验：" + ("通过" if proposal.validation.passed else "未通过"))
    st.info(chinese_summary(safe_text(proposal.explanation)))
    if eligible and not focused:
        st.warning(f"待你确认：将调整 {len(proposal.changes)} 条预约的器材。预约尚未修改；批准时仍会重新校验资源与版本。")
    elif not eligible:
        st.caption("本记录当前不能审批。以持久运行和方案状态为准，历史结果不会再次执行。")
    decision_attempted = controller.decision_attempted(proposal.id)
    if eligible and decision_attempted:
        st.warning("此方案已有提交，结果尚未确认。先刷新核对持久决定和预约；批准与拒绝都不会直接重发。")
        if st.button("已核对持久状态，准备重新审批",
                     key=f"decision_retry_{proposal.id}_{controller.decision_generation(proposal.id)}",
                     disabled=controller.read_only):
            controller.prepare_decision_retry(current.run.id)
            st.rerun()
    actor_area, approve_area = st.columns(2, vertical_alignment="bottom") if focused else (st, st)
    actor = actor_area.text_input("操作人匿名别名", value="管理员", max_chars=100, key=f"actor_{proposal.id}")
    if approve_area.button(f"批准并更新 {len(proposal.changes)} 条预约", type="primary", width="stretch",
                           key=f"approve_{proposal.id}",
                           disabled=controller.read_only or decision_attempted or not eligible or not actor.strip()):
        with st.spinner("提交审批并重新读取实际预约…"):
            outcome = controller.approve(current.run.id, actor)
        result = outcome.result
        level = "success" if result.status == ApplyStatus.APPLIED else "warning" if result.status == ApplyStatus.STALE else "error"
        st.session_state["campusops_decision_feedback"] = (proposal.id, safe_text(result.message),
            f"{result.error.code} · {safe_text(result.error.message)}" if result.error else "")
        message = (f"已批准并读回 {len(proposal.changes)} 条预约，器材分配已更新，原时间不变。"
                   if result.status == ApplyStatus.APPLIED else "资源已变化，本次未更新预约。请核对最新状态。"
                   if result.status == ApplyStatus.STALE else "审批未确认完成，请先刷新持久状态核对，不要直接重复提交。")
        _flash(level, message)
    with st.expander("不采用此方案？填写拒绝原因"):
        rejection = st.text_area("拒绝原因", max_chars=500, key=f"reject_reason_{proposal.id}")
        if st.button("拒绝此方案，保留预约", key=f"reject_{proposal.id}",
                     disabled=controller.read_only or decision_attempted or not eligible or not actor.strip() or not rejection.strip()):
            with st.spinner("保存拒绝决定并刷新持久状态…"):
                outcome = controller.reject(current.run.id, actor, rejection)
            result = outcome.result
            level = "info" if result.status == ApplyStatus.REJECTED else "error"
            st.session_state["campusops_decision_feedback"] = (proposal.id, safe_text(result.message),
                f"{result.error.code} · {safe_text(result.error.message)}" if result.error else "")
            message = "方案已拒绝，原预约保留。操作人和拒绝原因已从持久记录读取。" if result.status == ApplyStatus.REJECTED else "拒绝结果未确认，请刷新后核对。"
            _flash(level, message)
    with st.expander("方案技术明细与完整校验证据"):
        st.caption("以下保留未经改写的原始说明和技术标识，便于核对历史证据。")
        st.text("原始方案说明：" + safe_text(proposal.explanation))
        st.text(f"事故：{incident.id}\n方案：{proposal.id}\n当前状态：{proposal.status}")
        feedback = st.session_state.get("campusops_decision_feedback")
        if feedback and feedback[0] == proposal.id:
            st.text(f"最近提交的服务反馈：{feedback[1]}\n{feedback[2]}")
        _table(change_rows(proposal, current.reservations))
        _validation(proposal.validation)
    st.button("查看本次运行轨迹与预算", key="open_current_trace", on_click=_navigate,
              args=(PAGES[2], incident.id, current.run.id))


def _monitor(controller: UiController):
    service = controller.service
    with st.spinner("读取持久运行轨迹与 智能体配置…"):
        runs = sorted(service.list_runs(), key=lambda row: (row.created_at, row.id), reverse=True)
        configs = service.list_agent_configs()
    st.subheader("处理记录与设置")
    st.caption("回看保存的处理结果。选择记录、展开详情和刷新都不会发起新的模型调用。")
    with st.expander(f"全部运行历史与技术字段（{len(runs)} 条）"):
        _table([{
            "运行编号": row.id, "故障编号": row.incident_id, "状态": status_label(row.status),
            "执行模式": MODE_LABELS[row.execution_mode], "模型调用次数": row.model_calls,
            "重规划次数": row.retry_count, "创建（北京时间）": local_time(row.created_at),
            "结束（北京时间）": local_time(row.finished_at) if row.finished_at else "未结束",
        } for row in runs])
    st.caption("执行模式由服务记录。离线替身、故障注入、演示夹具与确定性服务操作均不计作真实模型验收。")
    run = None
    if runs:
        selected = _pick(
            "查看运行轨迹", runs, widget_key="trace_run_picker", selected_key="campusops_run_id",
            formatter=lambda row: f"{local_time(row.created_at)} · {STATUS_LABELS.get(row.status, row.status)}",
        )
        run = service.get_run(selected.id)
        steps = service.list_run_steps(run.id)
        _run_status(run)
        _approval_record(service, run)
        columns = st.columns(3)
        columns[0].metric("已记录模型调用", run.model_calls)
        columns[1].metric("重规划次数（合同上限 2）", run.retry_count)
        elapsed = (run.finished_at - run.created_at).total_seconds() if run.finished_at else None
        columns[2].metric("运行耗时（秒）", f"{elapsed:.3f}" if elapsed is not None else "尚未结束")
        if run.result:
            _validation(run.result.validation)
        with st.expander("实际步骤 · 执行轨迹与反馈"):
            _table([{
                "序号": step.seq, "执行角色": safe_text(agent_label(step.agent_id or "应用服务")), "步骤": node_label(step.node),
                "工具": TOOL_LABELS.get(step.tool_name, "无" if not step.tool_name else "受限工具"), "执行说明": chinese_summary(safe_text(step.decision_reason)),
                "执行模式": MODE_LABELS[step.execution_mode], "耗时（毫秒）": step.duration_ms,
                "模型调用": step.model_calls, "重规划计数": step.retry_count,
                "时间（北京时间）": local_time(step.created_at),
            } for step in steps])
            selected_step = st.selectbox("查看步骤详情", steps, key=f"trace_step_picker_{run.id}",
                                         format_func=lambda step: f"步骤 {step.seq} · {node_label(step.node)}") if steps else None
            if selected_step is not None:
                step = selected_step
                st.text("原始步骤：" + safe_text(step.node) + "\n原始原因：" + safe_text(step.decision_reason))
                st.text("输入摘要：" + safe_text(step.input_digest or "未记录"))
                st.text("输出 / 验证反馈摘要：" + safe_text(step.output_digest or "未记录"))
                if step.error:
                    _show_error(step.error)
    else:
        st.info("暂无运行记录。先登记故障，再由你主动生成方案；这里不会自动创建演示结果。")
        st.button("去登记器材故障", key="history_empty_register", on_click=_navigate, args=(PAGES[0],))
    with st.expander("调用预算 · 权威快照"):
        _model_budget(service, run)
    _published_rules(service)
    with st.expander("高级设置 · 智能体配置"):
        _agent_settings(service, configs, read_only=controller.read_only)
    from campusops.contracts.ports import ReservationManagementPort
    if isinstance(service, ReservationManagementPort):
        with st.expander("预约修改 · 三角色执行记录"):
            from campusops.ui.bookings import render_change_history
            render_change_history(controller)


def _agent_settings(service: ApplicationPort, configs, *, read_only: bool):
    st.caption("模型切换与密钥由本地环境管理，页面不展示或保存密钥。配置仅影响新任务；已开始的运行保留冻结配置。")

    st.subheader("智能体配置")
    _table([{
        "角色编号": row.agent_id, "名称": ROLE_LABELS.get(row.role, "未知角色") + "智能体", "角色（只读）": ROLE_LABELS.get(row.role, "未知角色"),
        "能力（只读）": "、".join(SKILL_LABELS.get(skill, "其他已注册能力") for skill in row.skills), "当前允许工具": "、".join(TOOL_LABELS.get(tool, "其他受限工具") for tool in row.allowed_tools),
        "启用": "已启用" if row.enabled else "已停用", "提示词版本": safe_text(row.prompt_version), "更新（北京时间）": local_time(row.updated_at),
    } for row in configs])
    if not configs:
        return
    by_id = {row.agent_id: row for row in configs}
    agent_id = st.selectbox("选择智能体", tuple(by_id), key="agent_picker", format_func=lambda key: ROLE_LABELS.get(by_id[key].role, "未知角色") + "智能体")
    agent = by_id[agent_id]
    if agent.role not in ROLE_TOOL_LIMITS:
        st.error("当前 智能体角色不受支持，无法编辑权限。请维护者核对配置。")
        return
    tools = sorted(ROLE_TOOL_LIMITS[agent.role])
    unknown_tools = not set(agent.allowed_tools) <= set(tools)
    if agent.prompt_version not in SUPPORTED_PROMPT_VERSIONS:
        st.error("当前保存的提示词版本不受支持。请选择已发布的 v1/v2，不能使用未知版本。")
    if unknown_tools:
        st.error("当前保存的工具超出角色权限，保存已禁用；请维护者核对。")
    enabled_key = f"agent_enabled_{agent_id}"
    prompt_key = f"agent_prompt_{agent_id}"
    tools_key = f"agent_tools_{agent_id}"
    revision_key = f"campusops_agent_revision_{agent_id}"
    revision = (agent.enabled, agent.prompt_version, agent.allowed_tools, agent.updated_at)
    previous_revision = st.session_state.get(revision_key)
    stale_editor = previous_revision is not None and previous_revision != revision
    if (previous_revision != revision or enabled_key not in st.session_state
            or prompt_key not in st.session_state or tools_key not in st.session_state):
        st.session_state[enabled_key] = agent.enabled
        st.session_state[prompt_key] = agent.prompt_version if agent.prompt_version in SUPPORTED_PROMPT_VERSIONS else None
        st.session_state[tools_key] = [tool for tool in agent.allowed_tools if tool in tools]
        st.session_state[revision_key] = revision
    with st.form(f"agent_config_{agent_id}"):
        enabled = st.checkbox("启用此智能体", key=enabled_key, disabled=read_only)
        prompt_version = st.selectbox("提示词版本", SUPPORTED_PROMPT_VERSIONS, index=None, key=prompt_key, disabled=read_only)
        allowed_tools = st.multiselect("允许工具（角色权限内）", tools, key=tools_key, disabled=read_only, format_func=lambda tool: TOOL_LABELS.get(tool, "其他受限工具"),
                                       help="仅可选择此角色公布的资源只读或受限审计工具；工具不足时新任务可能明确阻断，不会自动补权限。")
        submitted = st.form_submit_button("保存智能体配置", key=f"agent_save_{agent_id}",
                                          disabled=read_only or unknown_tools)
    if submitted:
        if read_only:
            raise UiInputError("只读回看不允许修改 智能体配置。")
        if stale_editor:
            raise UiInputError("智能体配置已被其他会话更新，表单已刷新；请核对后再次保存。")
        if prompt_version not in SUPPORTED_PROMPT_VERSIONS or not set(allowed_tools) <= set(tools) or unknown_tools:
            raise UiInputError("提示词版本或工具权限不受支持，配置未提交。")
        with st.spinner("保存并重新读取 智能体配置…"):
            saved = service.update_agent_config(agent_id, AgentRuntimeConfigPatch(
                enabled=enabled, prompt_version=prompt_version, allowed_tools=tuple(allowed_tools),
            ))
            refreshed = next(row for row in service.list_agent_configs() if row.agent_id == agent_id)
        if (saved != refreshed or saved.enabled != enabled or saved.prompt_version != prompt_version
                or saved.allowed_tools != tuple(allowed_tools)):
            raise UiInputError("配置保存结果尚未核实，请刷新后检查。")
        _flash("info", "智能体配置已保存并从服务重新读取；后续运行将读取当前配置，实际路由以运行轨迹为准。")


def render_app(service: ApplicationPort, *, reset_target: Path | None = None, entry_notice: str | None = None,
               read_only: bool = False):
    """Render an explicit port; tests supply their fixtures through this seam only."""
    from campusops.security.streamlit_gate import require_ui_access, render_clear_button, touch_user_activity
    from campusops.security.service import protect_service
    access = require_ui_access()
    if access is None:
        return
    service = protect_service(service, access)
    st.set_page_config(page_title="CampusOps", page_icon="🎒", layout="wide", initial_sidebar_state="collapsed")
    st.markdown(APP_STYLES, unsafe_allow_html=True)
    brand, context = st.columns([3, 1], vertical_alignment="center")
    brand.title("CampusOps")
    with context:
        render_clear_button()
    brand.caption("使用独立演示数据")
    if entry_notice:
        st.warning(safe_text(entry_notice))
    if read_only:
        st.info("当前为只读回看：可查看保存的历史，登记、生成、审批、配置和重置均关闭；不会重跑模型。")
    navigation, refresh = st.columns([4, 1], vertical_alignment="center")
    page = navigation.radio("工作台", PAGES, key="ui_page", horizontal=True, label_visibility="collapsed", on_change=touch_user_activity)
    refresh.button("刷新状态", key="refresh", width="stretch", help="只重新读取服务，不生成方案、不重复审批。", on_click=touch_user_activity)
    notice = st.session_state.pop("campusops_flash", None)
    if notice:
        level, message = notice
        getattr(st, level)(safe_text(message))
    controller = UiController(service, st.session_state, read_only=read_only)
    try:
        _table_mode()
        if page == PAGES[0]:
            _resources(controller, reset_target)
        elif page == PAGES[1]:
            _workflow(controller)
        else:
            _monitor(controller)
    except CampusOpsError as exc:
        _show_error(exc.error)
    except UiInputError as exc:
        st.warning(safe_text(exc))
    except Exception as exc:
        # Raw exceptions can contain provider headers, keys, paths or personal data.
        _report_render_failure(exc, page)
        st.error("服务读取或操作未能完成，当前结果未确认。请刷新持久状态后核对。")
    with st.expander("使用前提与运行模式"):
        if read_only:
            st.caption("本次入口：只读回看。历史的真实 API 标签仅说明当时记录，不授权新请求。")
        elif os.environ.get("CAMPUSOPS_LLM_ENABLED", "").strip().lower() == "true":
            st.caption("本进程声明启用人工智能；客户端、配置和可用预算仍须服务核验，不能据此认定已连接或调用成功。")
        else:
            st.caption("本进程未声明启用人工智能，默认离线；有影响但模型不可用时明确失败，不会使用替身冒充成功。")
        st.markdown(
            "运行前提：**Windows x64**；先把便携包**完整解压到 D 盘**，再使用包内启动入口。"
            "桌面入口使用随包的 Python 运行环境，并通过 **Microsoft Edge** 打开工作台。\n\n"
            "操作顺序：登记故障 → 明确点击生成 → 核对方案 → 人工批准或拒绝。刷新、换页和回看历史只读，不会生成方案。\n\n"
            "真实 AI 需维护者明确启用并配置单一服务、密钥与调用预算；页面不检查或保存密钥。"
            "预算不可用时显示**未知**，不从历史调用次数推算余额；缺配置、失败或无受影响预约都不能当作模型成功。\n\n"
            "演示预约固定在 **2026年11月10日至11日**，不随今天滚动。"
            "要体验固定演示，主动点“填入演示时间”后重新确认；只填时间不会登记或调用模型。"
        )


def main():
    from campusops.reviewer import run_ui
    run_ui()

if __name__ == "__main__":
    main()
