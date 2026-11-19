# CampusOps

CampusOps 用于演示学生社团共享器材的异常调度。系统把影响分析、调度规划和结果核验交给三个独立角色。预约变更经人工批准，在同一事务中重验规则并写入数据库。

## Windows 运行

使用 Windows x64、D 盘和 Microsoft Edge。将便携包完整解压到 D 盘的一个新文件夹，例如 `D:\FutureCup\CampusOps`，然后双击 **启动CampusOps.cmd**。包内附带 Python 和运行依赖。控制台会显示本机地址并打开 Edge。页面没有自动打开时，复制控制台显示的 `http://127.0.0.1:端口` 到浏览器。按 Ctrl+C 停止服务。

首次启动会导入 10 件器材、20 条预约、6 个匿名借用者及 3 个角色配置。所有业务记录均为虚构示例。数据库、日志及模型预算位于程序目录的 `var/reviewer`，重启后可继续使用。“清空当前操作”只清理当前窗口的表单与选择。 工作台意外退出后，再次启动会标记仍在规划的中断任务。预约和待审批方案保留，可按界面提示重新生成故障方案或提交改期。

## 演示流程

1. 在资源页登记演示相机 A 故障，将生效时间设为北京时间 **2026 年 11 月 10 日 08:00**。基础种子中的两个受影响预约都在 09:00 至 10:00。
2. 配置模型后点击生成方案，查看三角色轨迹及工具记录，再核对拟替换器材和原预约时段。
3. 手动批准或拒绝。批准后在预约页核对执行结果。基础台账、登记、预约新增和取消可直接操作。

真实模型默认关闭。点击生成时如果配置缺失，工作台会明确显示模型未就绪。启动、刷新和查看历史不会发起模型请求。离线回归中的 `TEST_INJECTED` 表示测试夹具生成的模型响应。

## 可选模型配置

仅在使用方已有获准模型凭据时配置。停止已有服务，在程序目录打开 PowerShell，按提示输入兼容 HTTPS 接口、模型 ID 和凭据。

```powershell
$key = Read-Host '模型 API Key' -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($key)
try {
    $env:CAMPUSOPS_REVIEW_MODEL_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
}
$env:CAMPUSOPS_REVIEW_MODEL_BASE_URL = Read-Host 'HTTPS 兼容 API 地址'
$env:CAMPUSOPS_REVIEW_MODEL_ID = Read-Host '模型 ID'
$env:CAMPUSOPS_REVIEW_MODEL_THINKING = 'default'
$env:CAMPUSOPS_REVIEW_LLM_ENABLED = 'true'
& '.\启动CampusOps.cmd'
```

源码入口使用同一组环境变量。默认限额为每次运行 6 个请求、每 UTC 日 30 个请求、最多重规划 2 次。密钥只在本机输入。若启用后工作台显示模型未就绪，先退出服务，将 `CAMPUSOPS_REVIEW_LLM_ENABLED` 设为 `false`，再启动。

## 从源码运行

在 D 盘新目录检出提交材料指定的固定提交，于仓库根目录运行以下命令。安装环节需要可用的依赖下载源或预置依赖。使用便携包时直接按 Windows 运行步骤操作。

```powershell
. .\scripts\dev-env.ps1
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
.\.venv\Scripts\python.exe scripts/reviewer_server.py
```

离线回归使用 `.\.venv\Scripts\python.exe -m pytest`。测试用数据独立于程序的演示数据，真实 API 测试默认跳过。原始项目设计和历史版本资料见 [docs](docs/)，公开契约见 [接口与数据约束](docs/contracts.md)。

第三方许可证与说明见 [docs/licenses](docs/licenses/) 和 [第三方组件声明](docs/third-party-notices.md)。
