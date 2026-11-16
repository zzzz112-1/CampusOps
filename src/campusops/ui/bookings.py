"""Chinese reservation workspace. All persistence is through protected ports."""
from datetime import datetime, timedelta
import re
from uuid import uuid4

from pydantic import ValidationError
import streamlit as st

from campusops.contracts.models import ReservationCreate, ReservationChangeCreate, ReservationCancelCreate
from campusops.contracts.ports import ReservationManagementPort, EquipmentCatalogPort
from campusops.ui.booking_controller import BookingController
from campusops.ui.controller import UiInputError, beijing_effective_at
from campusops.ui.labels import borrower_label, ERROR_LABELS, node_label, agent_label, TOOL_LABELS, chinese_summary
from campusops.ui.presentation import (
    DISPLAY_TZ, EQUIPMENT_LABELS, MODE_LABELS, STATUS_LABELS, capabilities_label, equipment_name,
    reservation_cards_html, local_time, safe_text, record_table_html, html_text, time_window_label,
    CATEGORY_LABELS, CAPABILITY_LABELS,
)

ACTIONS = ("预约列表", "新增预约", "修改预约", "取消预约")
PAGE_SIZE = 10


def touch_user_activity():
    # Resolve the shared gate at call time, matching the original UI entry.
    # Do not capture a registry/test-injected callback when this module loads.
    from campusops.security import streamlit_gate
    streamlit_gate.touch_user_activity()


def _discard_view():
    for key in ("campusops_booking_preview", "campusops_booking_result", "campusops_booking_run_id",
                "campusops_booking_form_backup", "campusops_booking_edit_revision", "campusops_booking_new_edit",
                "booking_cancel_reason", "booking_cancel_actor", "booking_confirmation"):
        st.session_state.pop(key, None)
    st.session_state["campusops_booking_epoch"] = st.session_state.get("campusops_booking_epoch", 0) + 1


def _clear_view():
    touch_user_activity()
    _discard_view()


def _clear_selection():
    for key in ("campusops_booking_selected_id", "booking_selected", "booking_list_selected"):
        st.session_state.pop(key, None)
    st.session_state["campusops_booking_selection_expired"] = True


def _select_booking():
    st.session_state["campusops_booking_selected_id"] = st.session_state.get("booking_selected")
    st.session_state.pop("campusops_booking_selection_expired", None)
    _clear_view()


def _back():
    touch_user_activity()
    preview = st.session_state.pop("campusops_booking_preview", None)
    if preview is not None:
        st.session_state["campusops_booking_form_backup"] = preview
    st.session_state.pop("booking_confirmation", None)
    st.session_state["campusops_booking_epoch"] = st.session_state.get("campusops_booking_epoch", 0) + 1
    st.rerun()


def _new_action(action):
    _clear_view()
    st.session_state["booking_action"] = action


def _open_booking(action, reservation_id):
    _new_action(action)
    st.session_state["campusops_booking_selected_id"] = reservation_id
    st.session_state.pop("campusops_booking_selection_expired", None)


def _select_list_booking():
    touch_user_activity()
    st.session_state["campusops_booking_selected_id"] = st.session_state.get("booking_list_selected")
    st.session_state.pop("campusops_booking_selection_expired", None)


def _filter_bookings():
    touch_user_activity()
    st.session_state["booking_active_page"] = 1
    _discard_view()
    _clear_selection()


def _turn_page(key, offset, last_page, clear_selection):
    touch_user_activity()
    st.session_state[key] = max(1, min(last_page, st.session_state.get(key, 1) + offset))
    if clear_selection:
        _discard_view()
        _clear_selection()


def _booking_page(rows, *, history=False):
    """Local presentation only; the complete port result and history stay intact."""
    key = "booking_history_page" if history else "booking_active_page"
    count = len(rows)
    pages = max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE)
    requested = st.session_state.get(key, 1)
    page = max(1, min(pages, requested if type(requested) is int else 1))
    # Reconcile passive changes without extending the authenticated session.
    st.session_state[key] = page
    first = (page - 1) * PAGE_SIZE
    controls = st.columns([1, 3, 1], vertical_alignment="center")
    prefix = "booking_history" if history else "booking_active"
    controls[0].button("上一页", key=prefix + "_prev", disabled=page == 1,
                       on_click=_turn_page, args=(key, -1, pages, not history))
    controls[1].caption(f"第 {page} / {pages} 页 · 显示 {first + 1 if count else 0}–{min(first + PAGE_SIZE, count)} / {count} 条 · 每页 {PAGE_SIZE} 条")
    controls[2].button("下一页", key=prefix + "_next", disabled=page == pages,
                       on_click=_turn_page, args=(key, 1, pages, not history))
    return rows[first:first + PAGE_SIZE]


def _preview_card(title, booking, equipment):
    st.html('<article class="campusops-card"><h4>' + html_text(title) + '</h4><p><strong>' +
        html_text(equipment_name(booking.equipment_id, equipment)) + '</strong> · ' + html_text(borrower_label(booking.borrower_alias)) +
        '</p><p>' + html_text(time_window_label(booking.starts_at, booking.ends_at)) + '</p><p class="muted">能力需求：' +
        html_text(capabilities_label(booking.required_capabilities)) + '</p></article>', unsafe_allow_javascript=False)


def _draft_card(request, equipment, *, change=False, title=None, before=None):
    original=next((row for row in equipment if before and row.id==before.equipment_id),None)
    automatic="自动匹配同类" + CATEGORY_LABELS.get(original.category,"器材") if original else "自动匹配同类器材"
    name = equipment_name(request.requested_equipment_id, equipment) if change and request.requested_equipment_id else (
        automatic if change else equipment_name(request.equipment_id, equipment))
    heading=title or ("请核对本次修改请求" if change else "请核对本次新预约")
    st.html('<article class="campusops-card"><h4>' + html_text(heading) + '</h4><p><strong>' +
        html_text(name) + '</strong> · ' + html_text(borrower_label(request.borrower_alias)) + '</p><p>' +
        html_text(time_window_label(request.starts_at, request.ends_at)) + '</p><p class="muted">能力需求：' +
        html_text(capabilities_label(request.required_capabilities)) + '</p></article>', unsafe_allow_javascript=False)


def _resume_change(request, *, automatic=False):
    _clear_view()
    st.session_state["campusops_booking_form_backup"]=request.model_copy(update={"requested_equipment_id":None}) if automatic else request
    st.session_state["campusops_booking_new_edit"]=True


def _failure_reason(value):
    text=safe_text(chinese_summary(value))
    for key,label in CAPABILITY_LABELS.items():
        text=re.sub(r"(?<![A-Za-z0-9_])"+re.escape(key)+r"(?![A-Za-z0-9_])",label,text)
    return text


def _booking_fields(controller, equipment, selected=None):
    change = selected is not None
    mode = "change" if change else "create"
    epoch = st.session_state.get("campusops_booking_epoch", 0)
    prefix = f"booking_{mode}_{epoch}"
    backup = st.session_state.get("campusops_booking_form_backup")
    if backup is not None and not isinstance(backup, ReservationChangeCreate if change else ReservationCreate):
        backup = None
    if change:
        revision = st.session_state.setdefault("campusops_booking_edit_revision", (selected.id, selected.version))
        if revision != (selected.id, selected.version):
            st.warning("原预约已被其他操作更新。当前填写内容未提交，请载入最新预约后重新填写。")
            st.button("载入最新预约，重新填写", key="booking_reload_latest", on_click=_clear_view)
            return
    source=next((row for row in equipment if change and row.id==selected.equipment_id),None)
    archived={row.equipment.id for row in controller.service.list_equipment_catalog(True) if row.archived} if isinstance(controller.service,EquipmentCatalogPort) else set()
    options = (None, *(row.id for row in equipment if row.id not in archived and source and row.category==source.category)) if change else tuple(row.id for row in equipment if row.id not in archived and row.status == "AVAILABLE")
    if change and backup and backup.requested_equipment_id and backup.requested_equipment_id not in options:
        # Preserve an older invalid target until the user explicitly changes it.
        options=(*options,backup.requested_equipment_id)
    if not options:
        st.info("当前没有可用器材，请先检查器材状态或完成维修。")
        return
    initial_id = (backup.requested_equipment_id if change else backup.equipment_id) if backup else None if change else options[0]
    automatic_label="自动匹配同类" + CATEGORY_LABELS.get(source.category,"器材") + "（推荐）" if source else "自动匹配同类器材（推荐）"
    def equipment_selector():
        return st.selectbox("希望使用的器材" if change else "预约器材", options,
            index=options.index(initial_id) if initial_id in options else 0, key=prefix + "_equipment",
            format_func=lambda key: automatic_label if key is None else equipment_name(key, equipment) + " · " +
                next((EQUIPMENT_LABELS[row.status] for row in equipment if row.id==key),"未找到"),
            on_change=None if change else touch_user_activity)
    wanted = initial_id if change else equipment_selector()
    available = next((row for row in equipment if row.id == wanted), None)
    if change:
        st.caption("可只更换同类器材、只调整时间，或同时修改两者。仅换器材时保留下面的原预约时间。")
        st.caption("默认自动匹配同类器材并排除故障、维护、能力不足和冲突。选择具体器材表示仅使用这一台，不会静默替换。")
    caps = sorted({cap for row in equipment for cap in row.capabilities})
    initial = backup or selected
    now = datetime.now(DISPLAY_TZ).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    start = initial.starts_at.astimezone(DISPLAY_TZ) if initial else now
    end = initial.ends_at.astimezone(DISPLAY_TZ) if initial else start + timedelta(hours=1)
    defaults = list(initial.required_capabilities) if initial else list(available.capabilities if available else ())
    st.caption("时间统一为北京时间。只保存匿名借用者信息，请勿填写真实姓名、学号或联系方式。")
    with st.form(prefix + "_form", enter_to_submit=False):
        if change:wanted=equipment_selector()
        alias_col, actor_col = st.columns(2)
        alias = alias_col.text_input("借用者匿名别名", value=initial.borrower_alias if initial else "", max_chars=100,
                                     placeholder="例如：匿名借用者 07", key=prefix + "_borrower")
        actor = actor_col.text_input("登记人匿名别名", value=backup.actor_alias if backup else "管理员", max_chars=100, key=prefix + "_actor")
        first, second = st.columns(2)
        with first:
            day1 = st.date_input("开始日期（北京时间）", value=start.date(), key=prefix + "_start_date", format="YYYY-MM-DD")
            clock1 = st.time_input("开始时间（北京时间）", value=start.time().replace(tzinfo=None), step=60, key=prefix + "_start_time")
        with second:
            day2 = st.date_input("结束日期（北京时间）", value=end.date(), key=prefix + "_end_date", format="YYYY-MM-DD")
            clock2 = st.time_input("结束时间（北京时间）", value=end.time().replace(tzinfo=None), step=60, key=prefix + "_end_time")
        requirements = st.multiselect("所需器材能力", caps, default=[cap for cap in defaults if cap in caps],
                                      format_func=lambda cap: capabilities_label((cap,)), key=prefix + "_capabilities")
        note = st.text_area("修改原因" if change else "用途说明（可选）", value=(backup.reason if change else backup.note) if backup else "",
                            max_chars=500, height=80, key=prefix + "_note")
        submitted = st.form_submit_button("预览修改请求" if change else "预览新预约", type="primary", width="stretch",
                                          key="booking_preview_submit", disabled=controller.read_only, on_click=touch_user_activity)
    if submitted:
        try:
            begin = beijing_effective_at(day1, clock1);finish = beijing_effective_at(day2, clock2)
            shared = dict(borrower_alias=alias.strip(), starts_at=begin, ends_at=finish, required_capabilities=tuple(requirements),
                          actor_alias=actor.strip(), idempotency_key="ui_booking_" + uuid4().hex)
            if change:
                request = ReservationChangeCreate(**shared, reservation_id=selected.id, expected_reservation_version=selected.version,
                                                  requested_equipment_id=wanted, reason=note.strip())
            else:
                request = ReservationCreate(**shared, equipment_id=wanted, expected_equipment_version=available.version, note=note.strip())
        except (ValidationError, UiInputError):
            st.warning("请填写匿名别名、登记人，至少选择一项能力，并确认结束时间晚于开始时间；修改预约还需填写原因。填写内容已保留。")
            return
        st.session_state["campusops_booking_preview"] = request
        st.session_state.pop("booking_confirmation", None)
        st.rerun()


def _show_change(controller, run, equipment):
    st.subheader("预约修改：" + STATUS_LABELS.get(run.status, "请核对当前状态"))
    st.caption(f"{MODE_LABELS[run.execution_mode]} · 已记录模型调用 {run.model_calls} 次 · 原预约编号保留")
    request=run.context.request
    st.caption("器材选择方式："+("自动匹配同类器材" if request.requested_equipment_id is None else "只使用指定的 "+equipment_name(request.requested_equipment_id,equipment)))
    original, proposed = st.columns(2)
    with original:_preview_card("原预约", run.context.before, equipment)
    with proposed:
        if run.result and run.result.after is not None:
            title="已保存的修改结果" if run.status=="APPLIED" else "历史规划目标（未执行）" if run.status in ("STALE","REJECTED") else "拟调整为（待批准）"
            _preview_card(title, run.result.after, equipment)
        else:_draft_card(request,equipment,change=True,title="本次请求目标（未执行）",before=run.context.before)
    st.caption("本次修改原因："+safe_text(request.reason))
    if run.result is None:
        st.info("修改请求已保存，尚未取得最终方案。请刷新状态核对，不要重复生成。")
        return
    if run.result.after is None:
        st.warning("没有可执行方案；以上请求目标尚未执行，原预约保持不变。")
        st.markdown("**未能完成的原因**")
        if run.error:st.error(ERROR_LABELS.get(run.error.code,"本次修改规划未完成，原预约保持不变。"))
        for issue in dict.fromkeys(run.result.validation.issues):st.write("• "+_failure_reason(issue))
        if not any(item.startswith("change-diagnostics:") for item in run.result.validation.evidence_ids):
            st.caption("该记录未保存当时逐台器材的排除详情；以下仅为当前读回状态，不补造历史原因。")
            target=next((row for row in equipment if row.id==request.requested_equipment_id),None)
            if target:st.info("当前读回：你指定的"+equipment_name(target.id,equipment)+"为"+EQUIPMENT_LABELS[target.status]+"。指定一台时不会自动替换；可改选自动匹配同类器材。")
        if run.status in ("FAILED","NEEDS_MANUAL","STALE") and request.requested_equipment_id is not None:
            if st.button("改用自动匹配同类器材",key="booking_change_auto_retry",disabled=controller.read_only):
                _resume_change(request,automatic=True);st.rerun()
        st.caption("下一步：调整器材、时间或能力要求后先预览，再明确生成；这里的按钮不会自动重试模型。")
    else:
        st.write(safe_text(run.result.explanation))
        if run.status=="STALE":st.warning("该方案已过期；目标内容仅供核对，不能直接批准，需重新规划。")
    eligible = run.status == "WAIT_APPROVAL" and run.result.after is not None
    if eligible:
        st.warning("原预约尚未修改。批准后才会保存；提交时还会重新检查资源状态、时间冲突和预约版本。")
        attempted = controller.change_decision_attempted(run.id)
        actor, approve = st.columns([1, 1], vertical_alignment="bottom")
        alias = actor.text_input("审批人匿名别名", value="管理员", max_chars=100, key="booking_change_actor_" + run.id, on_change=touch_user_activity)
        if approve.button("批准并更新原预约", type="primary", width="stretch", key="booking_change_approve",
                          disabled=controller.read_only or attempted or not alias.strip()):
            with st.spinner("重新校验并提交预约修改…"):
                saved = controller.decide_change(run, alias)
            st.session_state["campusops_booking_run_id"] = saved.id
            st.rerun()
        with st.expander("不采用此修改方案"):
            reason = st.text_area("拒绝修改的原因", max_chars=500, key="booking_change_reject_reason_" + run.id, on_change=touch_user_activity)
            if st.button("拒绝修改，保留原预约", key="booking_change_reject", disabled=controller.read_only or attempted or not reason.strip() or not alias.strip()):
                controller.decide_change(run, alias, reject_reason=reason)
                st.rerun()
        if attempted:
            st.info("审批请求已发送。请刷新核对持久决定和预约记录；不会自动重复提交。")
            if st.button("已核对持久状态，重新准备审批", key="booking_change_prepare_retry", disabled=controller.read_only):
                controller.prepare_change_retry(run.id);st.rerun()
    elif run.decision is not None:
        getattr(st, "success" if run.decision.status == "APPLIED" else "info")(safe_text(run.decision.message))
        st.caption(f"确认人：{safe_text(run.decision.actor_alias)} · {local_time(run.decision.decided_at)} 北京时间")
    with st.expander("三角色执行步骤与技术明细"):
        st.text(f"修改请求编号：{run.id}\n原预约编号：{run.context.before.id}")
        if run.steps:
            st.html(record_table_html([{"顺序": row.seq, "角色": agent_label(row.agent_id or "应用服务"), "步骤": node_label(row.node),
                "工具": TOOL_LABELS.get(row.tool_name, "无"), "模型调用": row.model_calls, "重规划": row.retry_count} for row in run.steps]), unsafe_allow_javascript=False)
        budget = controller.service.get_reservation_change_budget(run.id)
        if budget.status == "AVAILABLE":
            st.caption(f"本任务剩余可预留请求 {budget.run_remaining} 次；统一账本当日剩余 {budget.daily_remaining} 次。")
        else:st.caption("权威预算暂不可读，剩余额度未知；不会从历史次数推算余额。")


def render_reservations(parent, equipment, reservations):
    controller = BookingController(parent.service, parent.state, read_only=parent.read_only)
    st.subheader("预约管理")
    action = st.radio("预约操作", ACTIONS, horizontal=True, key="booking_action", on_change=_clear_view)
    st.caption("新增和取消不调用模型；修改预约先生成三角色方案，人工批准后更新原记录。")
    supported = isinstance(controller.service, ReservationManagementPort)
    pending = st.session_state.get("campusops_booking_pending")
    if pending:
        st.warning("上次请求已发送，但结果尚未确认。先查询保存结果，避免重复提交。")
        if st.button("查询已发送请求的保存结果", key="booking_recover_response"):
            kind, result = controller.recover_pending()
            if result is not None:
                st.session_state["campusops_booking_run_id" if kind == "change" else "campusops_booking_result"] = result.id if kind == "change" else result
                st.session_state.pop("campusops_booking_preview", None)
                st.rerun()
            st.info("暂未找到结果，不能据此认定操作失败。请刷新并核对预约列表。")
        return
    result = st.session_state.get("campusops_booking_result")
    if result is not None:
        current = next((row for row in reservations if row.id == result.reservation.id), None)
        st.success(safe_text(result.message))
        _preview_card("保存结果（最新读回）", current or result.reservation, equipment)
        st.caption("预约编号：" + result.reservation.id + (" · 当前已取消，历史保留" if current and current.status == "CANCELLED" else ""))
        if result.invalidated_proposal_ids:st.info(f"资源快照已变化，{len(result.invalidated_proposal_ids)} 项待审批方案已过期。")
        st.button("返回预约列表", key="booking_done_list", on_click=_new_action, args=(ACTIONS[0],))
        if current and current.status == "ACTIVE":
            st.button("修改这条预约", key="booking_edit_saved", on_click=_open_booking, args=(ACTIONS[2], current.id), disabled=controller.read_only)
        if action == ACTIONS[1]:st.button("继续新增另一条预约", key="booking_create_another", on_click=_new_action, args=(ACTIONS[1],))
        return
    active_reservations = tuple(row for row in reservations if row.status == "ACTIVE")
    cancelled_reservations = tuple(row for row in reservations if row.status == "CANCELLED")
    selected_id = st.session_state.get("campusops_booking_selected_id")
    if selected_id is not None and selected_id not in {row.id for row in active_reservations}:
        # Passive readback must not extend the authenticated session's idle time.
        # An unknown in-flight request is handled above and is never discarded here.
        _discard_view()
        _clear_selection()
    if st.session_state.get("campusops_booking_selection_expired"):
        st.info("预约状态或显示范围已变化，已清除选择和旧预览。请重新选择有效预约。")
    if action == ACTIONS[0]:
        st.subheader("有效预约")
        query = st.text_input("搜索预约", placeholder="输入器材、匿名借用者或预约编号", key="reservation_search", on_change=_filter_bookings)
        found = [row for row in active_reservations if
                 not query.strip() or query.strip().casefold() in (row.id + " " + equipment_name(row.equipment_id, equipment) + " " + borrower_label(row.borrower_alias)).casefold()]
        st.caption(f"筛选结果 {len(found)} / {len(active_reservations)} 条有效预约 · 以下时间均为北京时间。")
        page_rows = _booking_page(found)
        if supported and found:
            ids = tuple(row.id for row in page_rows)
            selected = st.session_state.get("campusops_booking_selected_id")
            if selected is not None and selected not in ids:
                _clear_selection()
                selected = None
            if selected is None and not st.session_state.get("campusops_booking_selection_expired"):
                selected = ids[0]
            if selected is not None:
                st.session_state["campusops_booking_selected_id"] = selected
            st.session_state["booking_list_selected"] = selected
            selected_id = st.selectbox("选择预约后操作", ids, index=None,
                key="booking_list_selected", format_func=lambda key: next(borrower_label(row.borrower_alias) + " · " +
                    equipment_name(row.equipment_id, equipment) + " · " + time_window_label(row.starts_at, row.ends_at) for row in page_rows if row.id == key),
                on_change=_select_list_booking)
            current = next((row for row in found if row.id == selected_id), None)
            edit, cancel = st.columns(2)
            edit.button("修改所选预约", key="booking_edit_selected", on_click=_open_booking, args=(ACTIONS[2], selected_id), disabled=controller.read_only or current is None)
            cancel.button("取消所选预约", key="booking_cancel_selected", on_click=_open_booking, args=(ACTIONS[3], selected_id), disabled=controller.read_only or current is None)
        elif st.session_state.get("campusops_booking_selected_id") is not None:
            _clear_selection()
        if found:
            st.html(reservation_cards_html(page_rows, equipment, label="有效预约"), unsafe_allow_javascript=False)
        else:
            st.info("没有匹配的有效预约，请调整搜索条件或新增预约。")
            st.button("去新增预约", key="booking_empty_create", on_click=_new_action, args=(ACTIONS[1],), disabled=controller.read_only)
        with st.expander(f"已取消预约历史（{len(cancelled_reservations)} 条）"):
            st.caption("仅回看已取消记录；原预约、修改记录和审计均保留，不能再次修改或取消。")
            history_rows = _booking_page(cancelled_reservations, history=True)
            if cancelled_reservations:
                st.html(reservation_cards_html(history_rows, equipment, label="已取消预约历史"), unsafe_allow_javascript=False)
            else:
                st.info("暂无已取消预约。")
        return
    if not supported:
        st.info("当前服务尚未提供预约管理入口，请维护者更新服务。")
        return
    preview = st.session_state.get("campusops_booking_preview")
    if preview is not None:
        if isinstance(preview, ReservationCancelCreate):
            row = next((row for row in reservations if row.id == preview.reservation_id), None)
            if row is not None:_preview_card("即将取消的预约", row, equipment)
            st.warning("确认后此预约将标记为已取消并释放时段，历史记录与审计保留。")
            st.write("取消原因：" + safe_text(preview.reason))
        else:_draft_card(preview, equipment, change=isinstance(preview, ReservationChangeCreate))
        st.caption("请按以上预览确认。需要改动时先返回修改；当前预览不会随未提交输入改变。")
        confirmed = st.checkbox("我已核对并确认本次操作", key="booking_confirmation", on_change=touch_user_activity)
        change = isinstance(preview, ReservationChangeCreate)
        label = "生成修改方案" if change else "确认取消预约" if isinstance(preview, ReservationCancelCreate) else "确认创建预约"
        if st.button(label, type="primary", width="stretch", key="booking_commit", disabled=controller.read_only or not confirmed):
            with st.spinner("执行三角色规划，原预约暂不修改…" if change else "校验并保存预约操作…"):
                saved = controller.plan_change(preview) if change else controller.cancel(preview) if isinstance(preview, ReservationCancelCreate) else controller.create(preview)
            st.session_state["campusops_booking_run_id" if change else "campusops_booking_result"] = saved.id if change else saved
            st.session_state.pop("campusops_booking_preview", None)
            st.session_state.pop("campusops_booking_new_edit", None)
            st.rerun()
        if st.button("返回修改", key="booking_back", disabled=controller.read_only):_back()
        return
    if action == ACTIONS[1]:
        _booking_fields(controller, equipment)
        return
    if not active_reservations:
        st.info("当前没有有效预约可修改或取消；可以新增预约，已取消记录在预约列表的历史区保留。")
        st.button("去新增预约", key="booking_empty_create", on_click=_new_action, args=(ACTIONS[1],), disabled=controller.read_only)
        st.button("查看预约列表与历史", key="booking_empty_history", on_click=_new_action, args=(ACTIONS[0],))
        return
    by_id = {row.id: row for row in active_reservations}
    ids = tuple(by_id)
    saved_selection = st.session_state.get("campusops_booking_selected_id")
    saved_selection = saved_selection if saved_selection in ids else None
    if saved_selection is None and not st.session_state.get("campusops_booking_selection_expired"):
        saved_selection = ids[0]
    if saved_selection is not None:
        st.session_state["campusops_booking_selected_id"] = saved_selection
    # Widget state can be removed while a preview is shown, or arrive stale
    # after a browser rerun. Only an explicit selection callback changes the
    # independent selected ID. Rehydrate the widget before mounting it.
    st.session_state["booking_selected"] = saved_selection
    selected_id = st.selectbox("选择要操作的预约", tuple(by_id), key="booking_selected",
        index=None,
        format_func=lambda key: borrower_label(by_id[key].borrower_alias) + " · " + equipment_name(by_id[key].equipment_id, equipment) +
            " · " + time_window_label(by_id[key].starts_at, by_id[key].ends_at) + " · " + key[-8:], on_change=_select_booking)
    if selected_id is None:
        st.info("请先选择一条有效预约，再继续操作。")
        return
    selected = by_id[selected_id]
    if action == ACTIONS[2]:
        changes = controller.service.list_reservation_changes(selected.id)
        selected_run = st.session_state.get("campusops_booking_run_id")
        active = next((row for row in changes if row.id == selected_run), None) or (changes[0] if changes else None)
        if active is not None:
            active = controller.service.get_reservation_change(active.id)
            terminal=active.status in ("APPLIED", "REJECTED", "STALE", "FAILED", "NEEDS_MANUAL")
            editing=terminal and st.session_state.get("campusops_booking_new_edit")
            if editing:
                with st.expander("上一次修改结果（只读回看）"):
                    _show_change(BookingController(controller.service,controller.state,read_only=True),active,equipment)
            else:_show_change(controller, active, equipment)
            if not terminal:
                return
            if not editing:
                if st.button("重新填写修改请求", key="booking_change_again", disabled=controller.read_only or selected.status != "ACTIVE"):
                    _resume_change(active.context.request);st.rerun()
                return
        if selected.status != "ACTIVE":
            st.info("此预约已取消，不能修改。取消历史仍可在预约列表查询。")
            return
        _preview_card("当前预约", selected, equipment)
        _booking_fields(controller, equipment, selected)
    else:
        if selected.status != "ACTIVE":
            st.info("此预约已经取消，无需重复操作。")
            return
        _preview_card("当前预约", selected, equipment)
        backup = st.session_state.get("campusops_booking_form_backup")
        backup = backup if isinstance(backup, ReservationCancelCreate) else None
        with st.form("booking_cancel_form", enter_to_submit=False):
            actor = st.text_input("取消确认人匿名别名", value=backup.actor_alias if backup else "管理员", max_chars=100, key="booking_cancel_actor")
            reason = st.text_area("取消原因", value=backup.reason if backup else "", max_chars=500, key="booking_cancel_reason")
            submitted = st.form_submit_button("预览取消", key="booking_cancel_preview", disabled=controller.read_only, on_click=touch_user_activity)
        if submitted:
            try:
                request = ReservationCancelCreate(reservation_id=selected.id, expected_reservation_version=selected.version,
                    actor_alias=actor.strip(), reason=reason.strip(), idempotency_key="ui_cancel_" + uuid4().hex)
            except ValidationError:
                st.warning("请填写取消确认人匿名别名和取消原因。")
                return
            st.session_state["campusops_booking_preview"] = request
            st.session_state.pop("booking_confirmation", None)
            st.rerun()


def render_change_history(parent):
    service = parent.service
    runs = service.list_reservation_changes()
    if not runs:
        st.info("暂无预约修改记录。此处只回看，不生成方案或发出模型请求。")
        return
    selected = st.selectbox("查看预约修改记录", tuple(row.id for row in runs), key="booking_change_history",
        format_func=lambda key: next(local_time(row.created_at) + " · " + STATUS_LABELS[row.status] + " · " + row.context.before.id[-8:]
                                    for row in runs if row.id == key), on_change=touch_user_activity)
    _show_change(BookingController(service, parent.state, read_only=True), service.get_reservation_change(selected), service.list_equipment())
