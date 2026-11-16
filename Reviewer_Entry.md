> **比赛无锁评审版：RC15-Review-1（0.2.0rc15.post1）**  
> 无需个人验证码、Owner工具或预先绑定，自动初始化独立虚构数据。  
> [评审分支](https://github.com/zzzz112-1/CampusOps/tree/reviewer-unlocked) · [固定源码提交](https://github.com/zzzz112-1/CampusOps/tree/58e684e465295a31ebf3cec1704e27d01b001231) · [Release与运行包](https://github.com/zzzz112-1/CampusOps/releases/tag/RC15-Review-1)  
> 固定提交：`58e684e465295a31ebf3cec1704e27d01b001231`。下方保留原加锁RC15的历史说明；评审请使用上述新入口。默认分支原运行代码仍保留TOTP。

# CampusOps / GearShift — Reviewer Entry

## 入口及权限状态

| 项目 | 实际值 |
|---|---|
| 初赛第四项所选路线 | Demo源码仓库路线；未选择在线环境，不要求另交一整套Windows包 |
| 实际仓库地址 | [https://github.com/zzzz112-1/CampusOps](https://github.com/zzzz112-1/CampusOps)；目标为zzzz112-1个人账号的CampusOps公开仓库 |
| 在线环境地址 | NotSelected；localhost、D盘目录和录像均不作为替代入口 |
| 评委取得源码的权限 | 公开可读；首次推送及无登录访问检查见独立交付回执。未向其他用户授予写权限 |
| 评委完整运行 | **NotVerified**；新电脑正常TOTP受控配置及模型访问安排尚未落实 |
| 所有者同意现场解锁 | 已确认，只是所有者一方的同意，不代表主办方/评委接受 |
| 主办方接受及实际复现 | NotVerified；尚无接受记录和评委执行回执 |
| 当前交付性质 | RC15筛选源码仓库；取得源码与完整运行分别核对，完整运行仍为NotVerified |

本说明按用户已确认的初赛要求整理：源码仓库地址或在线环境二选一。没有重新查询或改写官方赛题、PDF、PPT、视频或总包。
所有者已明确授权在zzzz112-1个人账号下公开发布这份筛选源码，未授权公开Owner实现或秘密、未添加新的开源许可证。源码公开不代表主办方已接受受控运行安排。

## 冻结版本与必要差异

- 材料/当前软件：**0.2.0rc15**。所选源码提交：`336688774851d9b3fe1ea3d98acbc6bbd76583d7`。
- 原工程RC15 tag：无。发布仓库使用独立首次提交，实际检出SHA请运行`git rev-parse HEAD`并与GitHub提交页核对；独立交付回执记录完整发布SHA。不移动或创建tag，也不把来源SHA当作发布SHA。
- 同版本桌面包构建源码：`9687da64e8a8cb6878531e5a07dbac0b485baf0a`。到所选Main提交只有验收文档及合并记录，无项目运行源码差异。
- 项目运行指纹：`fccc5276997ac74ff96d800ab8b7cf85e74cf0e1e2af2a99e66187e6e472f615`。口径是61个项目`.py/.sql/.json`文件的相对路径/文件SHA排序、以LF拼接再SHA-256，不是整个解释器。
- 本仓库复用既有冻结源码导出，仅建立该筛选快照的新历史，不带原工程`.git`或历史。它**不是原提交的完整Git树**：Owner实现、维护测试和历史媒体等被排除；完整业务、Agent、正常验证、安全消费、CLI、迁移、规则和启动器源码保留。
- `scripts/dev-env.ps1`在既有导出中为CRLF、当前干净检出为LF；只有换行表示差异。本筛选工作目录保留原导出字节；Git按既有.gitattributes规范化文本blob，检出.ps1恢复CRLF。运行源码指纹另行核对，不改Main工作树。
- 新增本入口说明、简短README和Source_Snapshot.json是交付说明，不能据此宣称发布commit等于上述原始提交。
- 原根README混有RC13及更早历史段落。本仓库README指向本页；原工程未修改，历史文字不作为当前版本事实。

## 评委获取源码后需要的环境

源码运行路线支持Windows x64、有D盘、CPython **3.12**（既有验证3.12.16）及浏览器。建议沿用项目已验证的Edge。
Windows CurrentUser DPAPI依赖目标Windows用户；Linux上的逻辑替身不能代替此运行环境。
源码入口不需要先编译CampusOps.exe；.NET Framework 4仅在选择编译原生启动器时需要。

从[https://github.com/zzzz112-1/CampusOps](https://github.com/zzzz112-1/CampusOps)取得源码。在D盘尚不存在的目录克隆后进入含pyproject.toml的项目根目录，例如`git clone https://github.com/zzzz112-1/CampusOps.git D:\CampusOps-Review`；已有目录不得覆盖。查看`git rev-parse HEAD`核对独立发布提交。随后打开**交互式PowerShell**。
以下均是供评委执行的既有命令，本轮没有安装依赖、启动或实测它们。

```powershell
. .\scripts\dev-env.ps1
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
```

首次安装需要能访问获准的依赖源，或使用已准备的离线wheel缓存；本源码视图不捆绑虚拟环境、整个Python运行时或wheelhouse。
依赖精确锁定在requirements.lock.txt，不在复现时擅自升级。第三方说明及已保留许可证在docs/third-party-notices.md和docs/licenses/。
用户已授权公开可读及本轮源码发布；本次未选择或添加任何新的开源许可证。原有权利与第三方许可保持原样，公开仓库不等于额外授予某种开源许可。

## 新评委电脑的正常TOTP安排——必须先落实

当前实现固定读取`D:\CampusOps\var\security\totp-state.sqlite3`，与源码工作目录、业务库和模型账本分离；不能靠改环境变量换成测试状态。
新电脑没有状态、状态损坏或无法解密时，正常入口保持锁定。这是已知访问前提，不是交一份锁定页就算可运行。

既有受支持方式如下，均由所有者控制，不把Owner工具上传仓库：

1. 在双方约定的目标电脑和**今后实际运行的Windows用户**下，由所有者使用现有RC15私有完整部署助手进行受控配置；若已有有效绑定则沿用。助手选择它随包的完整CampusOps程序目录（含CampusOps.exe及runtime），不能选择缺少这些文件的源码目录充当完整程序目录。
2. 新电脑需导入原手机记录时，所有者在私有工具本机输入设置密钥和当前验证码，工具在目标用户下重新CurrentUser DPAPI保护。源设置密钥只能通过既有验证原码的私有流程在本机取得，不能经聊天或公开仓库传递。
3. 私有随包程序与源码的正常验证实现都读取同一固定安全路径，同一目标Windows用户下完成配置后，源码使用自己的独立业务库即可。私有助手写在其程序目录的.env不会自动成为源码进程配置，源码模型按下文环境变量方式配置。
4. 不直接复制另一台电脑的加密状态库，不在本轮操作作者现有绑定，不公开Owner实现/执行工具或个人种子。配置完成后收回维护工具，评委只使用正常日常源码入口。所有者私下使用既有工具不等于比赛必须另交Windows运行包。
5. 初始化、CLI及新UI会话都会消费有效时间步。下一个命令/窗口要等待下一轮正常码；不复用已消费验证码，不提供万能码。
6. 所有者按已确认意愿亲自正常解锁后，评委在安排好的有效会话内按步骤操作；空闲/绝对超时后再次正常解锁。需要的目标电脑、用户、配置方式与时段须在交付前明确，并获得主办方接受。

若初赛要求无人值守、异步自主复现，而主办方不接受上述受控方式，目前安排就仍然阻塞；不能以一句“现场找作者”取代可用访问。
本轮只记录依赖和交付方式，没有执行迁移、生成新真实绑定、调整正式ACL或增加免验证入口。

## 初始化隔离虚构业务库并启动

所有者已在目标Windows用户完成正常访问配置后，在源码根目录的新PowerShell中执行。此例选用新的业务文件，避免覆盖已有演示或正式库。

```powershell
. .\scripts\dev-env.ps1
$env:DATABASE_PATH = Join-Path (Get-Location) 'var\data\reviewer-fictional.db'
if (Test-Path -LiteralPath $env:DATABASE_PATH) {
    throw '目标业务库已存在：先核对或另选新文件，不覆盖、不重置。'
}
.\.venv\Scripts\python.exe -m campusops.cli init-db --db $env:DATABASE_PATH
if ($LASTEXITCODE -ne 0) { throw '初始化未完成，停止后续命令。' }
# CLI会在本机不回显地要求正常验证码。下一条命令等待手机下一轮码。
.\.venv\Scripts\python.exe -m campusops.cli seed-demo --db $env:DATABASE_PATH --seed .\assets\demo_seed.json
if ($LASTEXITCODE -ne 0) { throw '虚构数据导入未完成，停止后续命令。' }
$env:CAMPUSOPS_LLM_ENABLED = 'false'
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8501
```

init-db通过包内`src/campusops/storage/migrations/001_initial.sql`显式迁移，seed-demo再导入已知虚构种子；不能省去迁移，也不调用重置真实数据的命令。
源码入口**不会自动初始化业务数据**。桌面会话有另一套已认证初始化逻辑，不能把其行为套到源码入口。
新浏览器窗口使用下一轮正常验证码。以上localhost只表示评委在自己电脑上运行后的本地服务地址，绝不是本轮已交付的远程评审入口。
规则来自`src/campusops/domain/data/gearshift-rules-v1.json`。初始10件器材、20条预约、6个匿名借用者和3个角色配置，预约业务日期为2026年11月10—11日（北京时间）。TOTP仍按真实系统时间，不能改系统时钟迎合演示日期。

## 模型配置与请求边界

源码入口`create_configured_application()`从**进程环境**读取配置，不自动加载.env。`.env.example`只是空密钥模板，复制它不等于已生效。
需要真实生成时，在目标电脑由获准人员设置以下变量后，从同一个PowerShell启动应用；API Key只在本机输入，不写进命令截图、源码或说明。

| 变量 | 非敏感含义/现有默认 |
|---|---|
| MODEL_API_KEY | 本机秘密，本文不给值 |
| MODEL_BASE_URL | 账号获准的HTTPS OpenAI兼容接口地址 |
| MODEL_ID | 该账号确实可用且获准的模型标识，不能自动换服务商 |
| CAMPUSOPS_LLM_ENABLED | 明确设true才创建真实客户端；默认false |
| MODEL_THINKING | default省略供应商扩展；既有Ark兼容短JSON角色可显式disabled，不能推断其他模型支持该字段 |
| MODEL_CALLS_PER_RUN_MAX / MODEL_DAILY_CALLS_MAX | 代码默认6/30；不得高于已有更低授权与账户保护限制 |
| REPLAN_MAX | 默认2，仍受总请求预算约束 |
| MODEL_TIMEOUT_SECONDS / MODEL_MAX_OUTPUT_TOKENS | 默认30秒/1600；不把更改上限当成调用授权 |
| LANGSMITH_TRACING / LANGCHAIN_TRACING_V2 | false |

只有模型使用方已授权并落实费用硬停止后，才在本机配置。先正常退出自己启动的源码服务，再在同一个已设好DATABASE_PATH的PowerShell中执行：

```powershell
$reviewerKey = Read-Host 'MODEL_API_KEY（仅在本机输入）' -AsSecureString
$reviewerKeyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($reviewerKey)
try {
    $env:MODEL_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($reviewerKeyPointer)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($reviewerKeyPointer)
}
$env:MODEL_BASE_URL = Read-Host '该账号获准的HTTPS兼容接口地址'
$env:MODEL_ID = Read-Host '该账号获准的确切model_id'
$env:MODEL_THINKING = Read-Host '思考参数（通用填default，已确认兼容的Ark可填disabled）'
$env:LANGSMITH_TRACING = 'false'
$env:LANGCHAIN_TRACING_V2 = 'false'
$env:CAMPUSOPS_LLM_ENABLED = 'true'
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8501
```

这只设置当前进程环境并启动本地服务；不自动增加预算，不把Key放进命令行参数或打印出来。不要使用聊天或公开.env交付秘密。
实际服务商、模型权限、网络连通及费用硬停止由评审使用方落实；本轮没有读取现用.env、发送探测或新增云调用。
启动、解锁、登记故障、换页、刷新、批准和维修不自动请求模型；明确点击“生成调整方案”或单预约修改的“生成修改方案”才进入相应模型工作流。
未启用/未配置AI时，可查看、创建、取消预约、管理器材、登记及维修；受影响预约的三角色生成会受控报未配置，不使用生产mock伪造成功。

## 故障→生成→验证→批准→读回

最小源码案例用初始20条虚构预约：相机A在2026-11-10 08:00（北京时间）故障，影响booking-01与booking-02的09:00—10:00预约；相机B的11:00—12:00占用不冲突。

1. “资源与预约”核对原记录，登记相机A与上述故障生效时间；预约应保持不变。
2. 在“异常调度与审批”明确点击生成一次。启用真实AI时才产生真实角色请求；以实际运行ID、调用计数和状态为准。
3. 成功分支应有影响分析、候选查询分发、确定性组合校验、规划和独立验证，最后WAIT_APPROVAL。候选查询并行不等于三个模型并行推理。
4. 核对原记录与目标器材、起止时间及校验结果。此时未修改预约。若出现FAILED/NEEDS_MANUAL，不把失败解释成成功，不连续重试消耗额度。
5. 在正常会话内由人填匿名操作别名并批准。Main在同一事务中重验版本与资源约束，成功后才写预约及审计。
6. 刷新并回预约列表/持久运行记录读回：相同预约ID、时间不变、器材按批准方案改变；未涉及的记录不变。也可用正常授权的CLI status核对计数，但它不替代逐预约UI读回。

若要按当前RC15离线视频的21条数据复现，必须先通过正常“新增预约”创建相机A在11月11日10:00—11:00的第21条合法预约，再于11月11日08:00登记故障，影响当天09:00—10:00及10:00—11:00两条。
此21条视频场景与上面的基础20条案例不是同一次运行，不能靠改标题混称一致。默认自动选择的其他可行器材也不应被硬写为模型必然选B。

## 视频、历史真实记录与当前在线功能的区别

- 当前RC15约4分钟成片是明确OFFLINE/TEST_INJECTED的机制抽帧与停帧剪辑，使用独立虚构库及替身；不证明当前云模型表现或完整未剪辑耗时。
- 历史RC6真实记录有真实模型请求、批准与读回，但源码、界面及安全时期不同；不作为当前RC15完整真实链或评委访问通过的替代证明。
- 当前正式源码支持配置真实兼容服务，能否在评委账号/网络下完整运行本轮为**NotVerified**。源码测试中的替身/测试上下文不是日常免验证入口。
- 本轮不调用模型、不重录视频、不重做截图或测试，也不把旧收据改成对本筛选源码视图的新验收。

## 必要构建与已有收据

源码运行不需要Windows便携包重建。若需构建Python wheel，已有入口为：

```powershell
.\.venv\Scripts\python.exe -m pip wheel --no-deps --no-build-isolation --wheel-dir .\var\dist .
```

原生C#启动器两个源文件保留在src/campusops/ui/，完整封装说明见scripts/build_windows_package.py与docs/windows-package.md；它还需要已审核的便携运行时及组件来源，不能只编译一个exe就说能独立运行。
已有同版本运行包可作可选附件，未重建也不是本次初赛第四项必须同时上传的文件。

已有完整私有源码导出上的48项定向收据及同版本包内测试可作为历史验证参考；本次Prepared_Repository是排除Owner后的筛选子集，不能继承为“本轮启动通过”。
如评委需要离线逻辑复核，可在已安装锁依赖后选用现有入口（本轮未执行）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/core/test_solver.py tests/integration/test_integrated_runtime.py tests/integration/test_reservation_lifecycle.py
```

该命令为隔离离线测试，不是三角色真实云验收或正常UI解锁证明；完整Owner维护测试仅留在私有原工程，本仓库不冒称含原全量套件。
另一台真实电脑、正式绑定迁移、正式目录ACL、完整桌面关闭重启及评委本人完整运行均未在本轮执行，保持NotVerified/原NotRun；仓库匿名访问单独核对，不代替这些验收。

仓库源码入口统一使用本页列出的GitHub地址。公开读取核对与正常TOTP、模型配置、主办方接受及评委实际运行分别记录；没有比赛平台回执，不声称已提交比赛。
