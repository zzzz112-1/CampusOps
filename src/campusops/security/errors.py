from campusops.contracts.errors import CampusOpsError
from campusops.contracts.models import ErrorCode


MESSAGES = {
    "NOT_CONFIGURED": "尚未由所有者配置访问验证，请联系所有者。",
    "MIGRATION_REQUIRED": "访问验证为旧格式，请关闭相关程序，由所有者使用私有迁移工具升级；原手机记录可继续使用。",
    "STATE_UNAVAILABLE": "访问验证状态不可读取或已损坏，请联系所有者。",
    "DECRYPT_FAILED": "本Windows用户无法解密访问验证，请保留原文件并联系所有者。",
    "INVALID_CODE": "验证码无效，请输入当前六位数字，并校准电脑和手机时间。",
    "REPLAYED": "本轮验证码已使用，请等待验证器下一轮验证码。",
    "COOLDOWN": "连续错误已触发冷却，请等待60秒后再试。",
    "LOCKED": "会话未解锁或已到期，请重新输入动态验证码。",
    "ALREADY_BOUND": "已存在绑定，不能自动覆盖；请使用所有者维护工具。",
    "CONFIRM_REQUIRED": "重新绑定须由所有者明确确认并保留加密备份。",
    "WINDOWS_REQUIRED": "访问验证需要Windows及DPAPI支持。",
}


class AccessDenied(CampusOpsError):
    def __init__(self, reason: str):
        self.reason = reason if reason in MESSAGES else "STATE_UNAVAILABLE"
        super().__init__(ErrorCode.INVALID_STATE, MESSAGES[self.reason])
