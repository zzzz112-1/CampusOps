"""Main-owned runtime composition. Run: python -m streamlit run streamlit_app.py."""

import os
from pathlib import Path

import streamlit as st

from campusops.bootstrap import create_configured_application
from campusops.contracts.errors import CampusOpsError
from campusops.ui.app import render_app, _show_error
from campusops.ui.presentation import safe_text
from campusops.security.streamlit_gate import require_ui_access
from campusops.services.desktop_data import initialize_desktop_data


def main():
    access = require_ui_access()
    if access is None:
        return
    try:
        root = Path(__file__).resolve().parent
        initialize_desktop_data(access, root)
        service = create_configured_application()
        expected = root / "var/data/campusops.db"
        configured = Path(os.environ.get("DATABASE_PATH") or Path.cwd() / "var/data/campusops.db")
        reset_target = expected if configured.resolve() == expected.resolve() else None
    except CampusOpsError as exc:
        st.set_page_config(page_title="CampusOps · GearShift")
        _show_error(exc.error)
        return
    except Exception:
        st.set_page_config(page_title="CampusOps · GearShift")
        st.error("应用服务未能启动，请检查本项目的本地配置。")
        return
    render_app(service, reset_target=reset_target)


if __name__ == "__main__":
    main()
