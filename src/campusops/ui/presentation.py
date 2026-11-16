"""Read-only display helpers. Times are always labelled Asia/Shanghai."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from html import escape
import os
import re
from zoneinfo import ZoneInfo

from campusops.contracts.models import BookingRuleCatalog, ExecutionMode, RunStatus
from campusops.ui.labels import borrower_label

DISPLAY_TZ = ZoneInfo("Asia/Shanghai")

STATUS_LABELS = {
    RunStatus.CREATED: "准备", RunStatus.SCOPING: "影响分析", RunStatus.PLANNING: "规划",
    RunStatus.VERIFYING: "校验", RunStatus.WAIT_APPROVAL: "待人工审批", RunStatus.APPLYING: "提交中",
    RunStatus.APPLIED: "已执行", RunStatus.NO_IMPACT: "无受影响预约", RunStatus.REPLANNING: "反馈重规划",
    RunStatus.NEEDS_MANUAL: "需人工处理", RunStatus.REJECTED: "已拒绝", RunStatus.STALE: "方案已过期",
    RunStatus.FAILED: "运行失败",
}
MODE_LABELS = {
    ExecutionMode.SYSTEM: "程序规则处理（无模型）",
    ExecutionMode.REAL_API: "真实云端模型模式",
    ExecutionMode.OFFLINE_MOCK_TEST: "离线替身测试（非真实模型）",
    ExecutionMode.FAILURE_INJECTION_TEST: "故障注入测试（非自然发生）",
    ExecutionMode.DEMO_FIXTURE: "虚构演示数据（非真实模型）",
}
EQUIPMENT_LABELS = {"AVAILABLE": "可用", "FAULTED": "故障", "MAINTENANCE": "维护中"}
CATEGORY_LABELS = {"camera": "相机", "light": "灯光", "microphone": "麦克风", "projector": "投影仪", "tripod": "三脚架"}
CAPABILITY_LABELS = {
    "photo": "拍照", "video": "录像", "4k": "4K 高清录像", "audio": "收音",
    "camera_mount": "相机支撑", "lighting": "照明", "presentation": "投影展示",
}


def html_text(value) -> str:
    return escape(safe_text(value), quote=True)


def capabilities_label(values) -> str:
    return "、".join(CAPABILITY_LABELS.get(value, safe_text(value)) for value in values) or "未记录能力"


def equipment_name(equipment_id, equipment) -> str:
    row = next((item for item in equipment if item.id == equipment_id), None)
    return safe_text(row.name) if row is not None else "未找到对应器材（详见技术记录）"


def task_steps_html(current: int) -> str:
    steps = (("登记故障", "选择器材与生效时间"), ("查看调整方案", "主动生成，核对影响"), ("确认处理结果", "批准后才更新预约"))
    return '<div class="campusops-steps" aria-label="故障处理步骤">' + "".join(
        f'<div class="campusops-step{" is-current" if index == current else ""}"'
        + (' aria-current="step"' if index == current else '') + '>'
        f'{index} · {title}<small>{subtitle}</small></div>'
        for index, (title, subtitle) in enumerate(steps, 1)
    ) + "</div>"


def equipment_cards_html(equipment) -> str:
    return '<div class="campusops-cards" aria-label="器材概览">' + "".join(
        '<article class="campusops-card">'
        f'<h4>{html_text(row.name)}</h4><p class="muted">{html_text(CATEGORY_LABELS.get(row.category, row.category))}</p>'
        f'<p>{html_text(capabilities_label(row.capabilities))}</p>'
        f'<span class="campusops-badge status-{row.status.lower()}">{html_text(EQUIPMENT_LABELS[row.status])}</span></article>'
        for row in equipment
    ) + "</div>"


def reservation_cards_html(reservations, equipment, *, label="预约概览") -> str:
    return '<div class="campusops-record-list" role="region" tabindex="0" aria-label="' + html_text(label) + '">' + "".join(
        f'<article class="campusops-card" data-reservation-id="{html_text(row.id)}">'
        f'<h4>{html_text(equipment_name(row.equipment_id, equipment))} · {html_text(borrower_label(row.borrower_alias))}</h4>'
        f'<p>{html_text(time_window_label(row.starts_at, row.ends_at))}</p>'
        f'<p class="muted">需求：{html_text(capabilities_label(row.required_capabilities))} · '
        f'{html_text({"ACTIVE": "有效预约", "CANCELLED": "已取消"}.get(row.status, row.status))}</p></article>'
        for row in reservations
    ) + "</div>"


def change_cards_html(proposal, reservations, equipment) -> str:
    current = {row.id: row for row in reservations}
    cards = []
    for item in proposal.changes:
        row = current.get(item.reservation_id)
        borrower = borrower_label(row.borrower_alias) if row else "未读到当前预约"
        actual = equipment_name(row.equipment_id, equipment) if row else "未知，请刷新核对"
        cards.append(
            '<article class="campusops-card">'
            f'<h4>{html_text(borrower)}</h4><div class="campusops-change">'
            f'<div><p class="muted">原器材</p><strong>{html_text(equipment_name(item.from_equipment_id, equipment))}</strong></div>'
            '<span aria-label="调整为">→</span>'
            f'<div><p class="muted">方案器材</p><strong>{html_text(equipment_name(item.to_equipment_id, equipment))}</strong></div></div>'
            f'<p>{html_text(time_window_label(item.starts_at, item.ends_at))} · 时间不变</p>'
            f'<p class="muted">目前预约使用：{html_text(actual)}</p></article>'
        )
    return '<div class="campusops-record-list" role="region" tabindex="0" aria-label="器材调整对照">' + "".join(cards) + "</div>"


def booking_rules_html(catalog: BookingRuleCatalog) -> str:
    """All current service-provided content is plain escaped text, including sources."""
    header = (
        '<article class="campusops-card">'
        f'<h4>当前发布规则集：{html_text(catalog.ruleset_id)} · 版本 {html_text(catalog.version)}</h4>'
        f'<p>来源：{html_text(catalog.source)}</p><p>{"虚构演示规则" if catalog.fictional else "未标记为虚构，请核对来源"}</p>'
        f'<p class="muted">目录指纹：{html_text(catalog.fingerprint)}</p></article>'
    )
    rules = "".join(
        '<article class="campusops-card">'
        f'<h4>{html_text(rule.rule_id)} · 版本 {html_text(rule.version)}</h4>'
        f'<p>{html_text(rule.content).replace(chr(10), "<br>")}</p>'
        f'<p class="muted">来源：{html_text(rule.source)} · {"虚构演示规则" if rule.fictional else "需核对来源"} · '
        f'场景：器材故障调度</p></article>'
        for rule in catalog.rules
    )
    return '<div class="campusops-record-list" role="region" tabindex="0" aria-label="当前发布借用规则">' + header + rules + "</div>"


def safe_text(value) -> str:
    text = str(value)
    configured_key = os.environ.get("MODEL_API_KEY")
    if configured_key:
        text = text.replace(configured_key, "[已隐藏密钥]")
    text = re.sub(r"(?i)\b(?:sk|sess)-[A-Za-z0-9_-]+", "[已隐藏密钥]", text)
    text = re.sub(
        r"(?i)\b(authorization|api[_-]?key|access[_-]?token)[\"']?\s*[:=]\s*[\"']?(?:bearer\s+)?[^\s,;}\"']+",
        r"\1=[已隐藏凭据]", text,
    )
    text = re.sub(r"(?i)\bbearer\s+[^\s,;]+", "Bearer [已隐藏凭据]", text)
    return text


def record_table_html(rows: Sequence[Mapping[str, object]]) -> str:
    """Complete read-only records, using only escaped text and fixed markup."""
    columns = list(dict.fromkeys(column for row in rows for column in row))

    def cell(value: object) -> str:
        return escape(safe_text(value) if value is not None else "未提供", quote=True)

    headers = "".join(f'<th scope="col">{cell(column)}</th>' for column in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell(row.get(column))}</td>" for column in columns) + "</tr>"
        for row in rows
    )
    return (
        """<style>
.campusops-records {max-height:24rem;max-width:100%;overflow:auto;}
.campusops-records table {border-collapse:collapse;width:100%;min-width:52rem;}
.campusops-records th,.campusops-records td {
  border:1px solid rgba(128,128,128,.35);padding:.35rem .5rem;
  text-align:left;vertical-align:top;white-space:pre-wrap;overflow-wrap:anywhere;max-width:28rem;
}
.campusops-records thead th {position:sticky;top:0;background:var(--background-color,Canvas);z-index:1;}
.campusops-records caption {text-align:left;padding:.35rem 0;}
</style>"""
        '<div class="campusops-records" role="region" aria-label="完整记录表格" tabindex="0">'
        f"<table><caption>完整记录 · {len(rows)} 条</caption><thead><tr>{headers}</tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def local_time(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Display requires an aware datetime")
    return value.astimezone(DISPLAY_TZ).strftime("%Y-%m-%d %H:%M:%S %z")


def time_window_label(start: datetime, end: datetime) -> str:
    first, last = start.astimezone(DISPLAY_TZ), end.astimezone(DISPLAY_TZ)
    end_format = "%H:%M" if first.date() == last.date() else "%Y年%m月%d日 %H:%M"
    return first.strftime("%Y年%m月%d日 %H:%M") + " — " + last.strftime(end_format) + " · 北京时间"


def status_label(status) -> str:
    return STATUS_LABELS.get(status, "未知状态，请核对原始记录")


def equipment_rows(equipment):
    return [{
        "器材编号": row.id, "名称": safe_text(row.name), "类别": safe_text(CATEGORY_LABELS.get(row.category, row.category)),
        "能力": capabilities_label(row.capabilities),
        "状态": EQUIPMENT_LABELS[row.status], "版本": row.version,
    } for row in equipment]


def reservation_rows(reservations):
    return [{
        "预约编号": row.id, "器材编号": row.equipment_id, "匿名借用者": safe_text(borrower_label(row.borrower_alias)),
        "开始（北京时间）": local_time(row.starts_at), "结束（北京时间）": local_time(row.ends_at),
        "能力要求": capabilities_label(row.required_capabilities), "状态": {"ACTIVE": "有效", "CANCELLED": "已取消"}[row.status], "版本": row.version,
    } for row in reservations]


def change_rows(proposal, reservations):
    current = {row.id: row for row in reservations}
    return [{
        "预约编号": item.reservation_id, "原器材": item.from_equipment_id, "拟用器材": item.to_equipment_id,
        "当前持久器材": current[item.reservation_id].equipment_id if item.reservation_id in current else "未读到",
        "开始（北京时间）": local_time(item.starts_at), "结束（北京时间）": local_time(item.ends_at),
        "当前开始（北京时间）": local_time(current[item.reservation_id].starts_at) if item.reservation_id in current else "未读到",
        "当前结束（北京时间）": local_time(current[item.reservation_id].ends_at) if item.reservation_id in current else "未读到",
        "规划时预约版本": item.expected_reservation_version,
        "当前预约版本": current[item.reservation_id].version if item.reservation_id in current else None,
    } for item in proposal.changes]
