"""Review-only destination validation; no personal TOTP dependency."""
from campusops.reviewer import get_context
def authorize_terminal():
    return get_context()
