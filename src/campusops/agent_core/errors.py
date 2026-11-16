"""Only controlled error text may cross the provider/tool boundary."""

from campusops.contracts.errors import CampusOpsError
from campusops.contracts.models import ErrorCode


class CoreFailure(CampusOpsError):
    def __init__(self, code: ErrorCode, reason: str, *, attempted: bool = False):
        super().__init__(code, reason)
        self.attempted = attempted


def invalid(reason: str) -> CoreFailure:
    return CoreFailure(ErrorCode.INVALID_INPUT, reason)
