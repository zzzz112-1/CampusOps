"""Reservation UI intents and readback, using only Main's optional public port."""
from uuid import uuid4

from campusops.contracts.errors import CampusOpsError
from campusops.contracts.models import ErrorCode
from campusops.ui.controller import UiController, UiInputError


class BookingController(UiController):
    def _dispatch(self, kind, request):
        intent = (kind, request.idempotency_key)
        self._begin(intent)
        self.state["campusops_booking_pending"] = (kind, request)
        try:
            result = (self.service.create_reservation(request) if kind == "create" else
                      self.service.cancel_reservation(request) if kind == "cancel" else
                      self.service.plan_reservation_change(request))
        except CampusOpsError:
            # These new Main commands guarantee rollback/pre-write rejection on
            # a structured error. Unknown/lost responses keep the intent locked.
            self.state.pop("campusops_booking_pending", None)
            attempts = dict(self.state.get("campusops_command_attempts", {}));attempts.pop(intent, None)
            self.state["campusops_command_attempts"] = attempts
            raise
        if kind == "change":
            observed = self.service.get_reservation_change(result.id)
            if observed.id != result.id or observed.context.request.reservation_id != request.reservation_id:
                raise UiInputError("修改请求保存结果尚未核实，请查询已发送请求，不要重复生成。")
        else:
            observed = self.service.find_reservation_command(request.idempotency_key)
            if observed != result:
                raise UiInputError("操作已发送，保存结果尚未核实，请查询结果，不要重复提交。")
        self._confirmed(intent)
        self.state.pop("campusops_booking_pending", None)
        return observed

    def create(self, request):
        return self._dispatch("create", request)

    def cancel(self, request):
        return self._dispatch("cancel", request)

    def plan_change(self, request):
        return self._dispatch("change", request)

    def recover_pending(self):
        kind, request = self.state["campusops_booking_pending"]
        if kind == "change":
            result = next((row for row in self.service.list_reservation_changes(request.reservation_id)
                           if row.context.request.idempotency_key == request.idempotency_key), None)
        else:
            result = self.service.find_reservation_command(request.idempotency_key)
        if result is not None:
            self._confirmed((kind, request.idempotency_key))
            self.state.pop("campusops_booking_pending", None)
        return kind, result

    def decide_change(self, run, actor, *, reject_reason=None):
        self._write_allowed()
        actor = self._actor(actor)
        saved = self.service.get_reservation_change(run.id)
        if saved.status != "WAIT_APPROVAL" or saved.result is None or saved.result.after is None:
            raise UiInputError("当前修改请求不允许审批，请刷新核对。")
        if reject_reason is not None and not reject_reason.strip():
            raise UiInputError("请填写拒绝原因。")
        generation = self.state.get("campusops_booking_decision_generations", {}).get(run.id, 0)
        intent = ("booking_change_decision", run.id, generation)
        self._begin(intent)
        if reject_reason is None:
            keys = dict(self.state.get("campusops_booking_decision_keys", {}))
            keys.setdefault(run.id, "booking_approve_" + uuid4().hex)
            self.state["campusops_booking_decision_keys"] = keys
            result = self.service.approve_reservation_change(run.id, actor, keys[run.id])
        else:
            if not reject_reason.strip():
                raise UiInputError("请填写拒绝原因。")
            result = self.service.reject_reservation_change(run.id, actor, reject_reason.strip())
        current = self.service.get_reservation_change(run.id)
        if current.decision != result or current.status != result.status:
            raise UiInputError("审批返回结果尚未核实，请刷新读取持久决定，不要重复提交。")
        if result.status == "APPLIED":
            booking = next((row for row in self.service.list_reservations() if row.id == run.context.before.id), None)
            if booking != result.reservation:
                raise CampusOpsError(ErrorCode.INVALID_STATE, "修改已提交，但最新预约尚未核实，请刷新核对。")
        self._confirmed(intent)
        return current

    def change_decision_attempted(self, change_id):
        generation = self.state.get("campusops_booking_decision_generations", {}).get(change_id, 0)
        return self.attempted(("booking_change_decision", change_id, generation))

    def prepare_change_retry(self, change_id):
        self._write_allowed()
        run = self.service.get_reservation_change(change_id)
        if run.status != "WAIT_APPROVAL" or run.decision is not None or not self.change_decision_attempted(change_id):
            raise UiInputError("请先核对当前持久状态，只有仍待审批且无决定的请求才能重新准备。")
        generations = dict(self.state.get("campusops_booking_decision_generations", {}))
        generations[change_id] = generations.get(change_id, 0) + 1
        self.state["campusops_booking_decision_generations"] = generations
