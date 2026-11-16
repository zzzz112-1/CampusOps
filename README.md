> **比赛无锁评审版：RC15-Review-1（0.2.0rc15.post1）**  
> 无需个人验证码、Owner工具或预先绑定，自动初始化独立虚构数据。  
> [评审分支](https://github.com/zzzz112-1/CampusOps/tree/reviewer-unlocked) · [固定源码提交](https://github.com/zzzz112-1/CampusOps/tree/58e684e465295a31ebf3cec1704e27d01b001231) · [Release与运行包](https://github.com/zzzz112-1/CampusOps/releases/tag/RC15-Review-1)  
> 固定提交：`58e684e465295a31ebf3cec1704e27d01b001231`。下方保留原加锁RC15的历史说明；评审请使用上述新入口。默认分支原运行代码仍保留TOTP。

# CampusOps / GearShift — 0.2.0rc15

奇点计划团队的 CampusOps 初赛源码与复现说明。

源码入口：[https://github.com/zzzz112-1/CampusOps](https://github.com/zzzz112-1/CampusOps)。仓库归属个人账号 **zzzz112-1**，公开可读、可克隆；本次未向其他用户授予写权限。

先阅读 [Reviewer_Entry.md](Reviewer_Entry.md)：支持 Windows x64、D盘和 Python 3.12，保留正常 TOTP 访问验证。新评委电脑需要所有者通过既有私有流程，在目标 Windows 用户下受控配置；Owner 工具、绑定状态、种子和真实模型密钥均不在仓库中。主办方接受安排及评委完整运行仍为 **NotVerified**。

来源冻结提交：`336688774851d9b3fe1ea3d98acbc6bbd76583d7`；比赛版本 **0.2.0rc15**。这份筛选快照使用独立首次提交，不包含原工程历史，实际发布提交请查看 [main 的提交记录](https://github.com/zzzz112-1/CampusOps/commits/main) 或运行 `git rev-parse HEAD`，不要把来源 SHA 当作发布 SHA。

61个项目运行源码文件保持与 RC15 证据一致；运行指纹 `fccc5276997ac74ff96d800ab8b7cf85e74cf0e1e2af2a99e66187e6e472f615`。精确范围和差异见 [Source_Snapshot.json](Source_Snapshot.json)。保留业务、Agent、正常认证、数据库迁移、虚构种子、锁定依赖与必要启动器源文件，排除 Owner 私有实现、真实 .env、安全库、个人数据、媒体和缓存。

按入口说明安装锁定依赖、正常解锁、显式初始化独立虚构业务库后启动。源码不会自动加载 .env；模型配置使用本机进程环境，明确点击生成按钮才请求模型。未配置 AI 时可操作已有离线业务功能，模型生成受控返回未配置，不伪造成功。

当前 RC15 机制视频使用明确标识的离线替身和故障注入；历史 RC6 真实模型记录不作为当前版本在线验收。仓库公开读取不等于软件独立运行通过，本轮没有请求云模型或重跑业务验收。

未新增开源许可证。原有权利及 [第三方说明](docs/third-party-notices.md)、[保留许可](docs/licenses/)维持原样。正常入口不提供免验证开关、万能码或公开种子。
