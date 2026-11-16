"""The same backend gate is used by both Streamlit entry points and callbacks."""
from functools import lru_cache

import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx

from .errors import AccessDenied
from .sessions import AccessContext, SessionRegistry

AUTH_KEY = "_campusops_access_token"


@lru_cache(maxsize=1)
def get_registry() -> SessionRegistry:
    # A registry of distinct session-bound capabilities, never one shared login.
    return SessionRegistry()


def session_id() -> str:
    context = get_script_run_ctx()
    if context is None or not context.session_id:
        raise AccessDenied("LOCKED")
    return context.session_id


def current_access(*, activity=False) -> AccessContext:
    token = st.session_state.get(AUTH_KEY, "")
    if not isinstance(token, str):
        raise AccessDenied("LOCKED")
    context = AccessContext(get_registry(), token, session_id(), session_id)
    context.require(activity=activity)
    return context


def lock_session():
    get_registry().revoke(st.session_state.get(AUTH_KEY, ""))
    # Clear business selections, cached views, pending widget intents and codes.
    st.session_state.clear()


def touch_user_activity():
    """Only explicit widget actions/mutations call this; reads/reruns do not."""
    try:
        current_access(activity=True)
    except AccessDenied:
        lock_session()


def _submit_code():
    code = st.session_state.get("_campusops_totp_input", "")
    try:
        token = get_registry().unlock(session_id(), code)
    except AccessDenied as error:
        st.session_state.pop("_campusops_totp_input", None)
        st.session_state["_campusops_gate_notice"] = error.error.message
    else:
        st.session_state.clear()
        st.session_state[AUTH_KEY] = token
        st.session_state["_campusops_just_unlocked"] = True


def require_ui_access() -> AccessContext | None:
    st.set_page_config(page_title="CampusOps · GearShift", page_icon="🎒", layout="wide",
                       initial_sidebar_state="collapsed")
    try:
        context = current_access()
    except AccessDenied:
        if st.session_state.get(AUTH_KEY):
            lock_session()
        context = None
    if context is not None:
        if st.session_state.pop("_campusops_just_unlocked", False):
            # Discard the authentication submission before rendering any old
            # business widget event; reauthentication never replays an action.
            st.rerun()
        return context
    st.title("CampusOps")
    try:
        get_registry().store.configuration_id()
        remaining = get_registry().store.cooldown_seconds()
    except AccessDenied as error:
        st.error(error.error.message)
        return None
    st.write("请输入Google Authenticator当前六位验证码")
    if remaining:
        st.warning(f"连续错误已触发冷却，请在{remaining}秒后再试。")
    with st.form("campusops_unlock_form", clear_on_submit=True):
        st.text_input("六位动态验证码", type="password", max_chars=6,
                      key="_campusops_totp_input", autocomplete="off")
        st.form_submit_button("解锁", on_click=_submit_code, type="primary")
    notice = st.session_state.pop("_campusops_gate_notice", None)
    if notice:
        st.error(notice)
    st.caption("请校准电脑和手机时间。已用验证码需等待下一轮；新窗口需要单独解锁。")
    return None


def render_lock_button():
    if st.button("锁定 / 退出", key="campusops_lock", help="仅退出当前会话，不删除业务数据或重置额度。"):
        lock_session()
        st.rerun()
