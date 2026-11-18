# CampusOps · 无锁评审版

交付标识 **RC15-Review-1**；程序版本 **0.2.0rc15.post1**。
本分支来自公开加锁版 `083e7722cbaf6b1f782562a81d4f5b2217661bff`，原始工程来源为 `336688774851d9b3fe1ea3d98acbc6bbd76583d7`。本版是独立修改后的评审交付，不声称与原RC15字节相同。

- [评审源码分支](https://github.com/zzzz112-1/CampusOps/tree/reviewer-unlocked)
- [RC15-Review-1 Release与运行包](https://github.com/zzzz112-1/CampusOps/releases/tag/RC15-Review-1)
- [原加锁版固定源码](https://github.com/zzzz112-1/CampusOps/tree/083e7722cbaf6b1f782562a81d4f5b2217661bff)

## 环境与启动

Windows x64、有D盘、Microsoft Edge。完整便携包自带Python及锁定依赖，无需Owner工具、手机验证码或已有绑定。
将运行包完整解压到D盘的**新目录**，双击 **启动CampusOps评审版.cmd**。控制台显示本机地址并打开Edge；服务仅监听127.0.0.1，保留CORS和XSRF保护。退出服务请在该控制台按Ctrl+C；再次启动保留演示操作。

首次启动自动迁移并导入10件器材、20条基础预约、6个匿名借用者与3个角色配置。业务库、日志、缓存及模型预算均在本包的 `var/reviewer` 下，后续启动不自动重置。已存在但缺少来源标记、初始化中断或损坏的文件会明确停止并保留，不自动修复或覆盖。请保留原目录另行核对，首次运行可选择完整新解压目录。

页面提示“无锁评审版 · 使用独立演示数据”。“清空当前操作”只清空当前窗口选择和表单；数据库、审批和预算保留。多窗口均可直接进入，不存在原版会话过期后的验证码要求。

## 基础操作及故障复现

可查看、新增、取消预约，维护器材、登记故障及维修；原冲突校验、人工审批、幂等、事务、审计与预算规则保留。

基础20条记录的演示业务日期为**2026年11月10日至11日，北京时间**。在资源页登记“演示相机A”于 **2026-11-10 08:00** 故障，将影响 booking-01 和 booking-02 的09:00—10:00预约；登记本身不改变预约。

已明确配置AI后，点击生成→查看Impact、Planner、Verifier及工具轨迹→核对方案→人工批准或拒绝→刷新预约及审计读回。故障替代保持预约时间。未涉及的记录不变，批准前也不改预约。资源或预约在批准前变化会重新校验并可能使旧方案失效。

未配置AI时，智能生成明确返回未配置/服务不可用，0模型请求，不能生成模拟成功。启动、初始化、换页、刷新与查看历史不自动请求模型。

此前视频的21条场景是另行新增一条合法预约之后的操作，不是此基础20条种子。原RC15截图、视频和测试仍属于其原版本；离线测试中的 TEST_INJECTED 三角色轨迹不等于真实云模型验收。不要修改系统时钟。

## 可选：使用方在本机明确配置真实模型

不读取任何原版.env，不继承原版MODEL_API_KEY、启用开关、业务库或预算路径。下面变量专用于评审版。关闭原评审服务后，在本机PowerShell进入评审包根目录，执行：

```powershell
$reviewKey = Read-Host '评审方自己的API Key，仅在本机输入' -AsSecureString
$reviewPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($reviewKey)
try { $env:CAMPUSOPS_REVIEW_MODEL_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($reviewPointer) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($reviewPointer) }
$env:CAMPUSOPS_REVIEW_MODEL_BASE_URL = Read-Host '获准的HTTPS兼容接口地址'
$env:CAMPUSOPS_REVIEW_MODEL_ID = Read-Host '确切模型标识'
$env:CAMPUSOPS_REVIEW_MODEL_THINKING = 'default'
$env:CAMPUSOPS_REVIEW_LLM_ENABLED = 'true'
& '.\启动CampusOps评审版.cmd'
```

仅在已确认兼容的Ark短JSON角色需要关闭思考时，将思考参数显式设为disabled。默认每次运行最多6次模型调用、每天30次、最多2次重规划，保持原预算机制；请由使用方确认服务、费用与账户硬停止。源码运行同样读取这些专用变量。密钥只存在本机进程环境，勿放入截图、聊天或公开文件。

## 源码运行

在D盘新目录取得reviewer-unlocked分支，并核对Release记录的固定提交。仅在本分支根目录创建自己的环境：

```powershell
. .\scripts\dev-env.ps1
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
.\.venv\Scripts\python.exe scripts/reviewer_server.py
```

也支持 `python -m streamlit run streamlit_app.py --server.address 127.0.0.1` 和 `python -m streamlit run src/campusops/ui/app.py --server.address 127.0.0.1`，首次数据初始化由评审入口完成，不需先运行旧版CLI。`python -m campusops.cli status`、init-db与seed-demo也只操作本目录的已知评审数据；--db不能指向原版或其他路径。

### Linux 云端源码运行

在 Linux 兼容分支的项目根目录，用 Python 3.12 和 `requirements.lock.txt` 准备 `.venv`，再启动回环服务。

```bash
CAMPUSOPS_LLM_ENABLED=false CAMPUSOPS_REVIEW_LLM_ENABLED=false \
  .venv/bin/python scripts/reviewer_server.py --no-browser --port 8501
```

规则从当前源码项目内读取。评审入口仍使用项目内的 `var/reviewer/data/review.db`，首次导入虚构数据，之后按实例标记验证并保留预约历史。路径和种子归属校验失败时，服务会停止。

定向离线测试：`python -m pytest -q tests/integration/test_reviewer_entry.py`。Windows Streamlit测试需要本机回环通信；所有外部网络在测试中禁止。正式运行不自动注入离线模型替身。

可选重建便携包使用 `scripts/build_reviewer_package.py --runtime-zip <已封存RC15日常包>`，脚本校验其固定哈希并复用第三方运行时，仅替换评审版第一方wheel，不下载依赖。

未新增开源许可证；原有权利与docs/third-party-notices.md及docs/licenses/保留。Owner私有实现、真实认证状态、个人数据库、API密钥、证明材料均不在本版交付中。

GitHub更新不自动替换已提交的比赛ZIP或PDF中的旧固定提交。请在平台允许时，使用同步后的材料替换提交；本任务不代交、不撤销旧提交。
