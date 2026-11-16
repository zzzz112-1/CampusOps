# RC13 异机启动诊断 / 2026-11-14

本轮基线：main `a2934742c172382a4b5eeed2a53086f1a55eca8c`，程序 `0.2.0rc13`。
这是独立的只读诊断补充工具，不是已修复目标电脑问题的新部署包。
原 RC13、预约、审计、Google Authenticator 绑定和API账本保留。

## 问题与验收表

| 问题 | 当前确认情况 | 处理 | 验收条件 | 证据位置 |
| --- | --- | --- | --- | --- |
| 离线启动导入 `_sqlite3` 报“应用程序控制策略已阻止此文件” | 用户另一台电脑的真实照片已确认；具体被拦截二进制尚待事件确认 | 读取本包原生文件哈希/签名和匹配的 CodeIntegrity 事件 | 取得目标电脑文件名、策略和时间，随后针对该组件修复及复验 | 新工具的 `reports/*/report.json`、`摘要.txt` |
| 启用AI后启动超时 | 用户照片确认本地服务未就绪；原因未确认 | 汇总最近3个桌面会话的固定启动阶段及脱敏失败类型 | 能区分进程创建、服务就绪、浏览器创建；目标电脑复验仍待执行 | 同上 |
| 现有环境诊断没有显示具体拦截事件 | 代码确认仍存在 | 提供无需导入 sqlite3/ctypes/业务包的独立工具 | 隔离测试证明范围、关联、脱敏与失败处理；本机真实Windows运行 | 本轮JUnit与本机诊断收据 |

## 使用方法：在发生问题的那台电脑上

1. 两次失败后先不要再点启动；保留原解压目录及其中的 `var/desktop`。
2. 解压新的 `CampusOps-Startup-Diagnostics-1.0.0.zip`。把整个
   `CampusOps-StartCheck` 文件夹放在现有 `CampusOps` 文件夹旁边。

   ```text
   D:\CampusOps\CampusOps-GearShift-0.2.0rc13-Windows-Deploy\
       CampusOps\
           runtime\python.exe
           启动CampusOps.cmd
           var\desktop\...
       CampusOps-StartCheck\
           Check-CampusOps.cmd
           collect_windows_appcontrol.py
           使用说明.md
   ```

3. 双击 `CampusOps-StartCheck\Check-CampusOps.cmd`。使用普通Windows权限即可，
   不需要管理员、不需要输入验证码、不需要配置API、不需要停用安全软件。
   也可以将包含 `runtime` 的 `CampusOps` 文件夹拖到此命令文件上。
4. 等待约一分钟。在 `CampusOps-StartCheck\reports\startup-日期-随机号\摘要.txt`
   中复制“请回传下列摘要即可”到“摘要结束”之间的内容。
   重点是“被拦截文件”“策略名称”“启动阶段”和“启动诊断”。
5. 如系统连诊断使用的Python也拦截、没有生成报告，回传窗口错误文字即可。
   如读取状态为 `AccessDenied` 或 `Unavailable`，如实保留；不要自行提权或关闭防护。

此工具只读取本包8个原生组件的哈希/签名、与本包匹配的应用控制事件，
以及最新3个桌面会话的固定字段。报告仅在诊断文件夹中新建，不覆盖程序文件。
不读取 `.env`、验证码状态库、二维码、业务数据库、API账本或服务输出全文，
不发送网络/模型请求，不改变注册表、文件权限、证书信任、系统时间、安全策略或进程。
报告不包含电脑名、Windows用户名或其他项目的路径。

## 如何理解这两种提示

第一张照片明确报告应用控制拦截，不能当作电脑硬件坏了。
报错发生在导入 `_sqlite3` 时，具体被拦截的文件也可能是其依赖，不能只凭模块名确定。

第二张照片是本地服务启动超时。现有原生启动器等待自己创建的服务就绪最多30秒，
随后按原边界停止自己本次会话的进程并保留记录。
启动桥在启用AI时读取并校验本地配置及导入依赖；这些操作不发送模型请求。
因此此处超时不代表豆包模型响应超时，也不能直接归因于硬件性能或网络。
可能与组件加载拦截、初始化卡住或服务就绪检查有关，须以该次日志为准。

`3077` 是强制策略阻止文件的事件，`3076` 是审核模式的“原本会阻止”，
不能把后者写成实际拦截。`3089` 是可按活动ID关联的签名信息。
`3033` 需要结合其他事件解释，不能仅凭它确定具体策略。
查询只覆盖最近24小时中至多400条指定事件；没有匹配事件不表示安全策略必然没有拦截。

依据：[Microsoft App Control事件说明](https://learn.microsoft.com/en-us/windows/security/application-security/application-control/app-control-for-business/operations/event-id-explanations)、
[Microsoft应用控制故障诊断](https://learn.microsoft.com/en-us/windows/security/application-security/application-control/app-control-for-business/operations/appcontrol-debugging-and-troubleshooting)。

## 本机核对及边界

本轮已核对RC13原生文件：`sqlite3.dll`、`libffi-8.dll` 的原发行者签名有效，
而 `python.exe`、`python312.dll`、`_sqlite3.pyd`、`_ctypes.pyd` 未签名。
旧RC12只替换并验证两个DLL，没有宣称整个解释器和应用都已签名。
目标电脑可能使用不同的信誉或企业允许策略，不能把本机曾经运行成功当作其已通过。
即使换用有有效签名的组件，也不能保证符合所有企业策略。

先取得确切拦截文件再选择兼容的原发行者构建；不随意混装解释器组件、下载未知DLL、
自己移植签名或停用保护。尚未把任何组件替换到用户的部署目录。

本轮不操作真实TOTP绑定或业务数据。业务与API验收未运行。
另一台电脑的诊断与启动复验由用户现场执行；完成前状态保持NotRun/待回传。

## 本轮工具验证记录

Windows x64 / Python 3.12.16 / 锁定依赖；未更改程序版本或业务运行时。

```powershell
. .\scripts\dev-env.ps1
.\.venv\Scripts\python.exe -m pytest tests/integration/test_windows_appcontrol_diagnostics.py tests/integration/test_environment_diagnostics.py tests/integration/test_desktop_server.py --junitxml=var/logs/rc13-startcheck-final-20261114.xml
.\.venv\Scripts\python.exe -m pip check
```

实际结果：31 Passed，0 Failed，0 Skipped；pip check通过。不是本轮全仓回归数字。
其中19项新工具测试覆盖导入阻断、事件匹配与关联、隐私字段过滤、审核事件区别、
超时/读取失败、会话阶段筛选和报告不覆盖；其余12项回归原环境诊断与启动桥。

本机隔离RC13解压包实际执行：工具使用包内Python并取得8个组件的签名及哈希，
可读取最新3个桌面会话阶段。第一次受限执行的日志状态为AccessDenied，记录保留；
相同工具在当前用户的普通Windows权限下取得Read，最近24小时窗口内无匹配本包的强制拦截。
这不能代表用户另一台电脑没有拦截。

本机报告位置：`var/logs/rc13-startcheck-local-20261114/startup-20261114T004127Z-2fcad305`
及 `startup-20261114T004210Z-823e1804`；前者为受限环境的部分结果，后者为正常日志读取。
新工具的封装收据及ZIP外SHA256随独立诊断包保存，包内只有工具、启动命令、说明和文件清单。
