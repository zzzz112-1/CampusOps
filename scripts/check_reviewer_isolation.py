"""Offline access audit for the real reviewer UI; not a runtime success switch."""
import argparse
import json
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).absolute().parents[1]
if (ROOT / "src/campusops/reviewer.py").is_file():
    sys.path.insert(0, str(ROOT / "src"))
attempts = []
external = []
forbidden = ("d:/campusops/var/security", "d:/campusops/var/data", "d:/campusops/var/model-budget", "d:/campusops/.env")


def audit(event, args):
    if event in ("open", "os.listdir", "os.scandir", "sqlite3.connect") and args:
        value = args[0]
        if isinstance(value, (str, bytes, Path)):
            text = str(value).replace("\\", "/").casefold().replace("file:///", "")
            if any(text == path or text.startswith(path + "/") or text.startswith(path + "?") for path in forbidden):
                attempts.append({"event": event, "category": "OriginalPrivateState"})
                raise RuntimeError("Original application state access forbidden")
    if event == "socket.connect":
        address = args[1]
        if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1"):
            external.append("ExternalNetworkAttemptBlocked")
            raise RuntimeError("External network forbidden during review audit")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--locked-source", type=Path)
    args = parser.parse_args()
    if args.locked_source:
        sys.path.insert(0, str(args.locked_source / "src"))
    sys.addaudithook(audit)
    from streamlit.testing.v1 import AppTest
    if args.locked_source:
        # Only this old-version negative control uses an isolated missing store.
        # It grants no authentication; no real binding is inspected.
        from campusops.security import streamlit_gate
        from campusops.security.sessions import SessionRegistry
        from campusops.security.state import SecurityStore
        isolated = args.output.parent / "missing-original-test-state.sqlite3"
        streamlit_gate.get_registry = lambda: SessionRegistry(SecurityStore(isolated))
        app = AppTest.from_file(str(args.locked_source / "streamlit_app.py"), default_timeout=30).run()
        assert not app.exception and not app.radio
        assert any("所有者" in item.value for item in app.error)
        checks = {"original_locked_negative_control": "Passed", "identity_fixture": "Isolated missing state, no grant"}
    else:
        app = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=30).run()
        assert not app.exception and any("无锁评审版" in item.value for item in app.title)
        for page in ("异常调度与审批", "处理记录与设置", "资源与预约"):
            app.radio(key="ui_page").set_value(page).run()
            assert not app.exception
        app.button(key="refresh").click().run()
        assert not app.exception
        assert "campusops.security.sessions" not in sys.modules
        assert "campusops.security.state" not in sys.modules
        from campusops.reviewer import get_context
        context = get_context()
        checks = {"actual_review_ui": "Passed", "normal_auth_injection": False,
                  "database": str(context.database), "budget": str(context.budget)}
    assert not attempts and not external
    checks.update(forbidden_state_access_attempts=attempts, external_network_attempts=external,
                  actual_cloud_requests=0, mode="OFFLINE_ACCESS_AUDIT")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(checks, indent=2) + "\n").encode())
    print(json.dumps(checks))


if __name__ == "__main__":
    main()
