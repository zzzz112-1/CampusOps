# RC12：保留安全防护的启动修复

2026年11月13日，RC11的本地服务启动被Windows智能应用控制拦截。实际CodeIntegrity 3033/3077事件指向未签名libffi-8.dll和sqlite3.dll，造成ctypes/SQLite加载失败，工作台尚未打开便退出。旧文件与RC11发布哈希一致；这次没有通过删除数据或重新绑定验证码处理依赖问题。

RC12只替换这两个原生DLL，保留原发行者的有效Authenticode签名。组件来自本机配置的OpenAI CodexPrimaryRuntime 26.1112.11011开源运行库；SHA256、原始未签名版本哈希、签名者/证书指纹和许可证见inputs/native-components-rc12.json。签名属于原组件发行者，不表示CampusOps获得OpenAI背书，也不表示CampusOps全部文件都已签名。

CPython仍为3.12.16，SQLite仍为3.53.1，Python标准库、_ctypes/_sqlite3扩展与锁定依赖不降级、不批量升级。libffi采用MIT许可证，SQLite为公有领域；对应说明随包保留。构建强制检查两个DLL的固定哈希、有效Windows签名及发行者证书。缺失、被替换或不能验证时停止打包，不静默使用旧文件。

本轮没有关闭智能应用控制、Defender或XSRF，没有添加排除项、导入信任证书、修改注册表安全状态或更改系统策略。签名验证不等于每一台电脑的任意应用控制策略都会允许；最终收据只记录实际验证的Windows电脑，独立异机需另行执行。

## 启动与原数据

本机使用D:\CampusOps\启动CampusOps.cmd，先正常关闭旧版窗口，避免新旧目录同时操作。旧包和旧数据目录保留，新本机副本通过只读SQLite backup复制并逐表核对，不seed/reset已有库。私有.env只在本机保留，不加入公开ZIP。

Google Authenticator的本人CampusOps记录继续使用原D盘独立安全状态目录；此次不读取、导出或重新生成你的真实绑定，不消费你的动态码。原模型预算账本保持独立，不因新运行目录或重置演示库清零。

公开包完整解压到D盘后进入CampusOps，双击启动CampusOps.cmd；Windows x64、Edge及.NET Framework4前提不变，不要求另装Python或Codex。新电脑未受控导入本人绑定前保持锁定，所有者维护工具仍单独私下保管。

## 诊断与证据

“诊断环境.cmd”现在实际检查ctypes和SQLite能否加载。若仍失败，保留会话startup-error.json和环境诊断；错误按阶段和固定原因记录，不输出密钥、验证码或供应商异常正文。不要为运行本软件关闭系统防护。

测试绑定、虚构数据和离线模型替身都明确标注TEST_INJECTED，不能当作你的真人绑定或真实云端调用。正式启动、锁定、隔离解锁、业务交互、关闭重开，以及系统策略前后状态以本轮实际收据为准；旧RC11的651项数字不能冒充RC12结果。
