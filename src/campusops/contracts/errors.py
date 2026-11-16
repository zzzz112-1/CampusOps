from campusops.contracts.models import ErrorCode, ServiceError


class CampusOpsError(Exception):
    """Stable public exception for failed commands/lookups; never raw provider errors."""

    def __init__(self, code: ErrorCode, message: str):
        super().__init__(message)
        self.error = ServiceError(code=code, message=message)
