"""Review branch filesystem context; no TOTP or authenticated session."""
import streamlit as st
from campusops.contracts.errors import CampusOpsError
from campusops.reviewer import ReviewerContext, get_context

def require_ui_access() -> ReviewerContext | None:
    st.set_page_config(page_title="CampusOps", page_icon="🎒", layout="wide",
                       initial_sidebar_state="collapsed")
    try:
        return get_context()
    except CampusOpsError as error:
        st.error(error.error.message)
        return None

def touch_user_activity():
    get_context().require()

def clear_current_operation():
    get_context().require()
    st.session_state.clear()

def render_clear_button():
    if st.button("清空当前操作", key="reviewer_clear_operation",
                 help="清空当前窗口的选择和表单，保留预约、审批记录及模型预算。"):
        clear_current_operation()
        st.rerun()
