"""Deterministic booking lifecycle constraints; no persistence or model access."""
from zoneinfo import ZoneInfo
from campusops.contracts.models import (
    ErrorCode, ProposalItem, Reservation, ReservationChangeContext, ResourceSnapshot,
    ValidationIssue, ValidationResult,
)
from campusops.domain.validation import overlaps


# Independent additional guard; the frozen public ReservationChangePolicy model
# and its older defaults remain compatible. This descriptor is in the snapshot.
CHANGE_CANDIDATE_POLICY = {"id": "same-category-reservation-change", "version": "1",
    "scope": "只允许原器材同类候选；指定器材不静默替换；时间只使用用户明确请求值"}
CATEGORY_NAMES = {"camera": "相机", "light": "灯光", "microphone": "麦克风", "projector": "投影仪", "tripod": "三脚架"}


def _window(booking):
    zone=ZoneInfo("Asia/Shanghai")
    return booking.starts_at.astimezone(zone).strftime("%Y年%m月%d日 %H:%M") + "—" + booking.ends_at.astimezone(zone).strftime("%Y年%m月%d日 %H:%M") + " 北京时间"


def requested_booking(context: ReservationChangeContext, equipment_id: str) -> Reservation:
    request = context.request
    return Reservation(**{**context.before.model_dump(), "equipment_id": equipment_id,
        "borrower_alias": request.borrower_alias.strip(), "starts_at": request.starts_at,
        "ends_at": request.ends_at, "required_capabilities": request.required_capabilities})


def validate_booking(snapshot: ResourceSnapshot, booking: Reservation) -> ValidationResult:
    violations = []
    def issue(code, message, entity_id=None):
        violations.append(ValidationIssue(code=code, message=message, entity_id=entity_id or booking.equipment_id))
    equipment = next((row for row in snapshot.equipment if row.id == booking.equipment_id), None)
    if equipment is None:
        issue(ErrorCode.NOT_FOUND, "未找到请求的目标器材：" + booking.equipment_id + "。")
    else:
        missing=sorted(set(booking.required_capabilities)-set(equipment.capabilities))
        if missing:
            issue(ErrorCode.CAPABILITY_MISMATCH, equipment.name + "缺少能力：" + "、".join(missing) + "。")
        if equipment.status != "AVAILABLE":
            status={"FAULTED":"故障","MAINTENANCE":"维护中"}.get(equipment.status,"不可用")
            issue(ErrorCode.RESOURCE_UNAVAILABLE, equipment.name + "在本次资源快照中为" + status + "状态，不能分配。")
        for incident in snapshot.incidents:
            if incident.status=="OPEN" and incident.equipment_id==equipment.id and booking.ends_at>incident.effective_at:
                issue(ErrorCode.RESOURCE_UNAVAILABLE, equipment.name + "仍受未解决故障影响，故障编号：" + incident.id + "。")
    for other in snapshot.reservations:
        if other.id != booking.id and other.status == "ACTIVE" and other.equipment_id == booking.equipment_id and overlaps(booking, other):
            name=equipment.name if equipment else booking.equipment_id
            issue(ErrorCode.RESERVATION_OVERLAP, name + "与有效预约 " + other.id + "（" + _window(other) + "）时间冲突。",other.id)
    return ValidationResult(passed=not violations, issues=tuple(row.message for row in violations),
        violations=tuple(violations), evidence_ids=("snapshot:" + snapshot.fingerprint, "reservation:" + booking.id))


def validate_change(snapshot, context: ReservationChangeContext, after: Reservation) -> ValidationResult:
    current = next((row for row in snapshot.reservations if row.id == context.before.id), None)
    violations = []
    if current != context.before or current is None or current.status != "ACTIVE":
        violations.append(ValidationIssue(code=ErrorCode.SNAPSHOT_CONFLICT, message="原预约已变化或已取消。", entity_id=context.before.id))
    if after == context.before:
        violations.append(ValidationIssue(code=ErrorCode.INVALID_PROPOSAL, message="保持原器材且所有内容与原预约相同，无需执行修改。",entity_id=after.equipment_id))
    if (after != requested_booking(context, after.equipment_id)
            or context.request.requested_equipment_id is not None and after.equipment_id != context.request.requested_equipment_id):
        violations.append(ValidationIssue(code=ErrorCode.INVALID_PROPOSAL, message="方案不符合用户明确请求的修改内容。", entity_id=context.before.id))
    original=next((row for row in snapshot.equipment if row.id==context.before.equipment_id),None)
    target=next((row for row in snapshot.equipment if row.id==after.equipment_id),None)
    if original is not None and target is not None and original.category!=target.category:
        violations.append(ValidationIssue(code=ErrorCode.CAPABILITY_MISMATCH,
            message="仅允许同类器材调换：原器材类别为" + CATEGORY_NAMES.get(original.category,original.category) +
                    "，" + target.name + "属于" + CATEGORY_NAMES.get(target.category,target.category) + "。",entity_id=target.id))
    result = validate_booking(snapshot, after)
    violations.extend(result.violations)
    return ValidationResult(passed=not violations, issues=tuple(row.message for row in violations),
        violations=tuple(violations), evidence_ids=result.evidence_ids + ("change:" + context.id,))


def change_candidate_diagnostics(snapshot, context: ReservationChangeContext) -> ValidationResult:
    """Explain the frozen request, never silently relax its exact target/time."""
    target=context.request.requested_equipment_id
    original=next((row for row in snapshot.equipment if row.id==context.before.equipment_id),None)
    scope=(target,) if target is not None else tuple(row.id for row in snapshot.equipment if original and row.category==original.category)
    checks=tuple(validate_change(snapshot,context,requested_booking(context,equipment_id)) for equipment_id in scope)
    evidence=("change-diagnostics:same-category-v1","snapshot:"+snapshot.fingerprint,"change:"+context.id)
    if any(result.passed for result in checks):
        return ValidationResult(passed=True,evidence_ids=evidence)
    violations=[row for result in checks for row in result.violations]
    if not scope:
        violations.append(ValidationIssue(code=ErrorCode.NOT_FOUND,message="未找到原器材所属类别的候选器材。",entity_id=context.before.equipment_id))
    if target is not None and original is not None:
        automatic=context.model_copy(update={"request":context.request.model_copy(update={"requested_equipment_id":None})})
        alternatives=[row.name for row in snapshot.equipment if row.category==original.category and row.id!=target
                      and validate_change(snapshot,automatic,requested_booking(automatic,row.id)).passed]
        if alternatives:
            violations.append(ValidationIssue(code=ErrorCode.RESOURCE_UNAVAILABLE,entity_id=target,
                message="同类器材中还有符合本次时间与能力要求的选项：" + "、".join(alternatives) +
                        "。当前请求仅指定一台器材，没有自动替换；可改选“自动匹配同类器材”后重新生成。"))
    return ValidationResult(passed=False,issues=tuple(dict.fromkeys(row.message for row in violations)),
                            violations=tuple(violations),evidence_ids=evidence)


def change_items(context: ReservationChangeContext, equipment_id: str) -> tuple[ProposalItem, ...]:
    return (ProposalItem(reservation_id=context.before.id, from_equipment_id=context.before.equipment_id,
        to_equipment_id=equipment_id, starts_at=context.request.starts_at, ends_at=context.request.ends_at,
        expected_reservation_version=context.before.version),)
