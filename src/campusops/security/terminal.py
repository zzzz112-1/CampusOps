"""One authenticated local maintenance command; no persistent terminal login."""
import getpass
import sys

from .errors import AccessDenied
from .state import SecurityStore


def authorize_terminal():
    store = SecurityStore()
    store.configuration_id()  # Missing/damaged binding fails before asking.
    if not sys.stdin.isatty():
        raise AccessDenied("LOCKED")
    try:
        code = getpass.getpass("CampusOps：请输入验证器当前六位验证码（不回显）：")
    except (EOFError, KeyboardInterrupt):
        raise AccessDenied("LOCKED") from None
    store.verify_and_consume(code)
