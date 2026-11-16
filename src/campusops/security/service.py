"""Reviewer destination checks; business and approval validation are unchanged."""
from campusops.reviewer import ReviewerContext

MUTATIONS = frozenset({"register_equipment_failure", "start_workflow", "start_workflow_once",
                       "approve_proposal", "reject_proposal", "update_agent_config", "reset_demo_data", "recover_equipment",
                       "create_reservation", "cancel_reservation", "plan_reservation_change", "approve_reservation_change", "reject_reservation_change",
                       "create_equipment","update_equipment","archive_equipment","restore_equipment"})


def protect_service(service, access: ReviewerContext):
    if not isinstance(access, ReviewerContext):
        raise TypeError("ReviewerContext required")
    access.require()
    if service._database.path != access.database:
        raise ValueError("Reviewer database destination mismatch")
    # Explicit methods preserve runtime-checkable optional-port detection.
    from campusops.contracts import ports
    names = set()
    for name in ("ApplicationPort", "IdempotentWorkflowPort", "BookingRulesPort", "EquipmentMaintenancePort", "ReservationManagementPort", "EquipmentCatalogPort", "DecisionsAndBudgetPort"):
        protocol = getattr(ports, name, None)
        if protocol is not None:
            names.update(k for k, v in vars(protocol).items() if not k.startswith("_") and callable(v))
    # Include all concrete optional public methods, but never private attributes.
    names.update(k for k in dir(type(service)) if not k.startswith("_") and callable(getattr(type(service), k)))

    def method(name):
        target = getattr(service, name)

        def checked(self, *args, **kwargs):
            access.require(activity=name in MUTATIONS)
            return target(*args, **kwargs)
        checked.__name__ = name
        return checked

    methods = {name: method(name) for name in names if callable(getattr(service, name, None))}
    protected = type("ProtectedApplication", (), methods)()
    access.require()
    return protected
