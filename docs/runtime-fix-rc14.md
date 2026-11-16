# RC14 异机原生扩展加载修复

本轮基线为RC13及Main `c16ad86e9ba8b662112aed334615f2135b77752f`，没有回退业务或验证码加固。
用户2026年11月14日从另一台真实电脑回传诊断，日志读取状态Read：
`_sqlite3.pyd` 在北京时间08:23:41出现3033事件，`_uuid.pyd` 在08:22至08:52多次出现3033。
两次启用AI会话记录了进程创建，未记录服务就绪；离线会话记录过窗口已确认。
这些是旧RC13的失败证据，不作为RC14已在另一台电脑运行成功的证明。

| 问题 | 当前情况 | 最小修复 | 验收与证据 |
| --- | --- | --- | --- |
| `_sqlite3.pyd` 导入被拒绝 | 真实远端3033和页面报错对应 | 在新运行时中使用固定哈希的原发行者签名扩展，保留SQLite3.53.1 | 哈希/签名门禁、真实SQLite事务及完整离线回归 |
| `_uuid.pyd` 加载被拒绝，AI启动未就绪 | 真实远端3033；超时与该事件的完整因果仍需目标电脑复验 | 同样采用固定哈希的签名扩展，不盲目增加30秒启动期限 | 原生导入、桌面离线与启用AI的零请求启动 |
| 无法确定策略名称 | 远端摘要没有3077和策略名称 | 明确保留未知，不声称已确定为某个企业策略或硬件故障 | 原回传摘要SHA及隔离修复收据 |

只更换两个新增的CPython原生扩展；RC12已核验的两个签名DLL继续保留。
解释器主程序仍为3.12.16，SQLite仍为3.53.1，Python依赖锁与业务/契约不变。
四个组件的哈希、来源、原证书、构建版本与许可见`inputs/native-components-rc14.json`。
新增扩展来自已配置的CodexPrimaryRuntime内的开源CPython构建，不读取任何其他项目。
组件保留原发行者签名，CampusOps没有据此获得背书或自身代码签名。

Python官方说明同一小版本的ABI兼容取决于平台及编译方式；私有API不享有同样保证。
因此本轮不仅比较版本号，还以真实3.12.16解释器验证这两个3.12.14扩展、数据库操作及项目回归。
依据：[CPython C API稳定性](https://docs.python.org/3.12/c-api/stable.html)。
3033既可能由应用控制也可能由CIG或签名问题触发，不能仅凭3033填入未知策略名。
依据：[Microsoft事件说明](https://learn.microsoft.com/en-us/windows/security/application-security/application-control/app-control-for-business/operations/event-id-explanations)。

## 新包使用与数据保护

保留原RC12/RC13目录和ZIP，将RC14完整解压到D盘的**新目录**，从新目录启动。
不覆盖正在使用的目录；本轮不自动迁移或清空原有预约、审计、运行日志、API账本。
Google Authenticator安全状态仍使用原来的稳定D盘位置和格式2；
已经完成RC13升级的绑定不需要因RC14重新扫码或迁移。
若仍是RC12旧格式，仍须按`totp-upgrade-rc13.md`由所有者受控保种子升级，
本次构建不会替用户操作真实绑定。跨Windows用户/电脑不能直接拷贝DPAPI状态。
新公开包不带Owner维护工具、真实绑定、二维码、密钥或测试数据。

先在目标电脑不启用AI启动并确认锁定页，再启用AI确认同样可到达锁定页。
启用AI的启动准备只校验本地配置和导入依赖，不会请求模型；
需要生成方案时仍须用户明确操作并受现有限额约束。本轮没有模型请求授权，不会进行真实API验收。
目标电脑如仍受策略限制，保留新会话并运行只读诊断；签名有效不保证符合所有允许策略。
不关闭安全软件、导入证书、更改应用控制、提权或下载来源不明的DLL。

## 验证状态

开发环境全套离线回归实际命令：
`.venv\Scripts\python.exe -m pytest --junitxml=var/logs/rc14-development-full-20261114.xml`。
结果767 Passed、0 Failed、1 Skipped（未授权真实API），70.63秒；pip check通过。
44项定向检查见`var/logs/rc14-native-targeted-20261114.xml`，全部Passed。
这些是本轮源代码测试；新的便携运行时与桌面验收单独记录，不用历史数字替代。

代码、运行时、Windows桌面与分发检查的本轮实际收据保存于新的RC14交付目录。
最终结果以实际JUnit、staging-manifest、release-manifest、桌面验收收据和包外SHA256为准。
真实模型请求：NotRun。另一台电脑的RC14启动复验：NotRun，等待用户现场执行。
真实所有者绑定读取/迁移及安全目录权限调整：NotRun，本轮不执行。
