// CampusOps Windows shell: starts the Main-owned bridge and an isolated Edge app.
// Business state and model configuration remain entirely with the Python service.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Windows.Forms;

namespace CampusOps.Desktop
{
    internal sealed class LaunchSettings
    {
        internal string Root;
        internal bool DevelopmentVenv;

        internal static LaunchSettings Parse(string[] arguments, string executableDirectory)
        {
            LaunchSettings result = new LaunchSettings();
            string root = executableDirectory;
            bool rootSeen = false;
            for (int index = 0; index < arguments.Length; index++)
            {
                if (arguments[index] == "--project-root" && !rootSeen && index + 1 < arguments.Length)
                {
                    root = arguments[++index];
                    rootSeen = true;
                }
                else if (arguments[index] == "--development-venv" && !result.DevelopmentVenv)
                    result.DevelopmentVenv = true;
                else throw new LauncherFailure("启动参数无效。仅支持 --project-root <D盘绝对目录> 和工程选项 --development-venv。");
            }
            if (String.IsNullOrWhiteSpace(root) || root.Length < 4 || Char.ToUpperInvariant(root[0]) != 'D' ||
                root[1] != ':' || (root[2] != '\\' && root[2] != '/'))
                throw new LauncherFailure("请将 CampusOps 放在 D 盘项目目录中，并提供绝对路径。");
            foreach (char character in root)
                if (Char.IsControl(character)) throw new LauncherFailure("项目路径包含无效字符。");
            try { result.Root = Path.GetFullPath(root).TrimEnd('\\', '/'); }
            catch (ArgumentException) { throw new LauncherFailure("项目路径无效。"); }
            catch (NotSupportedException) { throw new LauncherFailure("项目路径无效。"); }
            catch (PathTooLongException) { throw new LauncherFailure("项目路径过长。"); }
            if (result.Root.Length < 4) throw new LauncherFailure("请使用 D 盘中的 CampusOps 项目子目录。");
            return result;
        }

        internal string Python
        {
            // The console entry uses the existing hidden CREATE_NO_WINDOW launch path.
            get { return Path.Combine(Root, DevelopmentVenv ? @".venv\Scripts\python.exe" : @"runtime\python.exe"); }
        }

        internal string Bridge { get { return Path.Combine(Root, @"scripts\desktop_server.py"); } }

        internal void ValidateFiles()
        {
            if (!Directory.Exists(Root) || !File.Exists(Path.Combine(Root, "streamlit_app.py")) || !File.Exists(Bridge))
                throw new LauncherFailure("缺少 CampusOps 启动文件，请使用完整交付包。");
            RequireLocalPath(Root);
            RequireLocalPath(Path.Combine(Root, "streamlit_app.py"));
            RequireLocalPath(Bridge);
            if (!File.Exists(Python))
                throw new LauncherFailure(DevelopmentVenv ? "缺少本项目的工程 Python 环境。" : "缺少交付包 runtime/python.exe。");
            RequireLocalPath(Python);
        }

        internal static void RequireLocalPath(string path)
        {
            // Reject existing links/junctions that could redirect a D-drive path elsewhere.
            FileSystemInfo item = File.Exists(path) ? (FileSystemInfo)new FileInfo(path) : new DirectoryInfo(path);
            while (item != null)
            {
                if (item.Exists && (item.Attributes & FileAttributes.ReparsePoint) != 0)
                    throw new LauncherFailure("项目或会话路径不能通过链接指向其他位置。");
                DirectoryInfo directory = item as DirectoryInfo;
                item = directory != null ? directory.Parent : ((FileInfo)item).Directory;
            }
        }

        internal static string FindEdge()
        {
            foreach (Environment.SpecialFolder folder in new[] {
                Environment.SpecialFolder.ProgramFilesX86, Environment.SpecialFolder.ProgramFiles })
            {
                string baseDirectory = Environment.GetFolderPath(folder);
                if (String.IsNullOrEmpty(baseDirectory)) continue;
                string executable = Path.Combine(baseDirectory, @"Microsoft\Edge\Application\msedge.exe");
                if (File.Exists(executable)) return executable;
            }
            throw new LauncherFailure("未找到已安装的 Microsoft Edge，无法打开独立应用窗口。");
        }

        internal List<string> BridgeArguments(int port, string session, bool enableModel, bool accountProtected)
        {
            if (enableModel && !accountProtected)
                throw new LauncherFailure("启用真实 AI 前，请确认服务商免费额度和账单硬停止保护。");
            List<string> result = new List<string> { Bridge, "--port", port.ToString(CultureInfo.InvariantCulture),
                "--session-dir", session };
            if (enableModel) { result.Add("--enable-model"); result.Add("--account-protected"); }
            return result;
        }
    }

    internal sealed class WindowLifetime
    {
        internal bool SeenWindow;
        private long missingSince = -1;

        // Monotonic time; the original Edge PID is deliberately not an input.
        internal bool Observe(bool visible, long milliseconds)
        {
            if (visible) { SeenWindow = true; missingSince = -1; return false; }
            if (!SeenWindow) return false;
            if (missingSince < 0) missingSince = milliseconds;
            return milliseconds - missingSince >= 1200;
        }
    }

    internal enum SessionStage { WaitingService, WaitingWindow, Running, WindowClosed }

    internal sealed class CleanupResult
    {
        internal bool ServiceConfirmed, BrowserConfirmed, SuspendedChildConfirmed = true;
        internal bool Confirmed { get { return ServiceConfirmed && BrowserConfirmed && SuspendedChildConfirmed; } }
        internal string Message
        {
            get { return Confirmed ? "本次会话进程已确认停止；会话日志和数据保留。" :
                "未能确认本次所有进程已停止。请保留会话目录交给维护者检查。"; }
        }
    }

    internal sealed class DesktopSession : IDisposable
    {
        internal readonly string DirectoryPath;
        internal SessionStage Stage = SessionStage.WaitingService;
        internal string Status = "正在启动本地服务……";
        private readonly LaunchSettings settings;
        private readonly string edge;
        private readonly int port;
        private readonly OwnedJob serverJob, browserJob;
        private readonly OwnedChild server;
        private OwnedChild browser;
        private readonly Stopwatch clock = Stopwatch.StartNew();
        private long stageStarted;
        private readonly WindowLifetime windows = new WindowLifetime();
        private readonly StreamWriter log;
        private bool browserRootExitLogged, logFailed, disposed;
        private CleanupResult cleanup;

        internal DesktopSession(LaunchSettings configuration, bool enableModel, bool accountProtected)
        {
            settings = configuration;
            settings.ValidateFiles();
            edge = LaunchSettings.FindEdge();
            if (enableModel && !accountProtected)
                throw new LauncherFailure("启用真实 AI 需要同时确认服务商账户保护。");
            string parent = Path.Combine(settings.Root, @"var\desktop");
            LaunchSettings.RequireLocalPath(parent);
            Directory.CreateDirectory(parent);
            DirectoryPath = Path.Combine(parent, DateTime.UtcNow.ToString("yyyyMMddTHHmmssZ", CultureInfo.InvariantCulture) +
                "-" + Guid.NewGuid().ToString("N"));
            if (Directory.Exists(DirectoryPath) || File.Exists(DirectoryPath)) throw new LauncherFailure("会话目录已存在，未覆盖。");
            Directory.CreateDirectory(DirectoryPath);
            LaunchSettings.RequireLocalPath(DirectoryPath);
            log = new StreamWriter(new FileStream(Path.Combine(DirectoryPath, "launcher.log"),
                FileMode.CreateNew, FileAccess.Write, FileShare.Read), new UTF8Encoding(false));
            log.AutoFlush = true;
            try
            {
                Log(enableModel ? "mode=real_ai_explicitly_confirmed; startup_model_requests=0" : "mode=offline; startup_model_requests=0");
                serverJob = new OwnedJob();
                browserJob = new OwnedJob();
                TcpListener reservation = new TcpListener(IPAddress.Loopback, 0);
                reservation.Server.ExclusiveAddressUse = true;
                try
                {
                    reservation.Start();
                    port = ((IPEndPoint)reservation.LocalEndpoint).Port;
                    if (port == 8501) throw new LauncherFailure("未选择独立会话端口，已停止启动。");
                    // The bridge binds immediately after this release. Ownership is checked
                    // again before HTTP readiness; no existing listener can be adopted.
                }
                finally { reservation.Stop(); }
                server = serverJob.Start(settings.Python,
                    settings.BridgeArguments(port, DirectoryPath, enableModel, accountProtected), settings.Root, true);
                Log("server_started; pid=" + server.Id + "; port=" + port);
            }
            catch (LauncherFailure failure)
            {
                Stop(failure.ChildCleanupConfirmed);
                Dispose();
                throw new LauncherFailure(failure.Message + "\n" + cleanup.Message + "\n会话目录：" + DirectoryPath);
            }
            catch
            {
                Stop(true);
                Dispose();
                throw new LauncherFailure("会话启动失败。\n" + cleanup.Message + "\n会话目录：" + DirectoryPath);
            }
        }

        private void Log(string entry)
        {
            // Only fixed lifecycle facts and our own PIDs/port. No raw child output,
            // command/environment dump, provider exception, request, key, or DB read.
            try { log.WriteLine(DateTime.UtcNow.ToString("O", CultureInfo.InvariantCulture) + " " + entry); }
            catch (IOException) { logFailed = true; }
            catch (UnauthorizedAccessException) { logFailed = true; }
        }

        internal void Tick()
        {
            if (cleanup != null) return;
            if (logFailed) throw new LauncherFailure("会话日志无法写入，已停止继续启动。");
            if (server.Wait(0)) throw new LauncherFailure("本次本地服务已退出，请检查会话目录中的启动诊断。");
            if (Stage == SessionStage.WaitingService)
            {
                if (clock.ElapsedMilliseconds - stageStarted > 30000)
                    throw new LauncherFailure("本地服务启动超时。请保留会话目录检查，不会自动重试。");
                if (!serverJob.OwnsLoopbackListener(port, server.Id) || !Healthy()) return;
                string profile = Path.Combine(DirectoryPath, "browser-profile");
                Directory.CreateDirectory(profile);
                LaunchSettings.RequireLocalPath(profile);
                browser = browserJob.Start(edge, new List<string> {
                    "--app=http://127.0.0.1:" + port.ToString(CultureInfo.InvariantCulture),
                    "--user-data-dir=" + profile, "--no-first-run", "--no-default-browser-check",
                    "--disable-background-mode", "--disable-background-networking"
                }, settings.Root, false);
                Stage = SessionStage.WaitingWindow;
                Status = "本地服务已就绪，正在确认独立应用窗口……";
                stageStarted = clock.ElapsedMilliseconds;
                Log("owned_listener_healthy; browser_started; pid=" + browser.Id);
                return;
            }
            if (browser != null && !browserRootExitLogged && browser.Wait(0))
            {
                browserRootExitLogged = true;
                Log("browser_initial_process_exited; observing_owned_windows_and_descendants");
            }
            bool visible = browserJob.HasVisibleEdgeWindow();
            if (windows.Observe(visible, clock.ElapsedMilliseconds))
            {
                Stage = SessionStage.WindowClosed;
                Log("last_owned_application_window_closed");
            }
            else if (visible && Stage != SessionStage.Running)
            {
                Stage = SessionStage.Running;
                Status = "应用正在运行；关闭最后一个应用窗口将结束本次会话。";
                Log("owned_application_window_confirmed");
            }
            else if (!windows.SeenWindow && (browserJob.ProcessIds().Count == 0 ||
                clock.ElapsedMilliseconds - stageStarted > 30000))
                throw new LauncherFailure("未能确认本次 Edge 应用窗口归属，已停止启动。未接管其他浏览器窗口。");
        }

        private bool Healthy()
        {
            HttpWebRequest request = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" +
                port.ToString(CultureInfo.InvariantCulture) + "/_stcore/health");
            request.Proxy = null;
            request.AllowAutoRedirect = false;
            request.Timeout = 400;
            request.ReadWriteTimeout = 400;
            request.KeepAlive = false;
            request.UseDefaultCredentials = false;
            try
            {
                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                    return response.StatusCode == HttpStatusCode.OK && serverJob.OwnsLoopbackListener(port, server.Id);
            }
            catch (WebException failure)
            {
                if (failure.Response != null) failure.Response.Close();
                return false; // bounded readiness observations, never workflow/model retries
            }
        }

        internal CleanupResult Stop(bool suspendedChildConfirmed)
        {
            if (cleanup != null) return cleanup;
            cleanup = new CleanupResult();
            cleanup.SuspendedChildConfirmed = suspendedChildConfirmed;
            cleanup.BrowserConfirmed = browserJob == null || browserJob.StopAndConfirm();
            cleanup.ServiceConfirmed = serverJob == null || serverJob.StopAndConfirm();
            Log("cleanup; server_confirmed=" + cleanup.ServiceConfirmed + "; browser_confirmed=" +
                cleanup.BrowserConfirmed + "; suspended_child_confirmed=" + cleanup.SuspendedChildConfirmed);
            // Kill-on-close is also retained if an observation/termination could not be confirmed.
            if (browserJob != null) browserJob.Dispose();
            if (serverJob != null) serverJob.Dispose();
            return cleanup;
        }

        public void Dispose()
        {
            if (disposed) return;
            disposed = true;
            Stop(true);
            if (browser != null) browser.Dispose();
            if (server != null) server.Dispose();
            log.Dispose();
        }
    }

    internal sealed class LauncherForm : Form
    {
        private readonly LaunchSettings settings;
        private readonly CheckBox model = new CheckBox();
        private readonly CheckBox protectedAccount = new CheckBox();
        private readonly Button start = new Button();
        private readonly Label status = new Label();
        private readonly Label sessionPath = new Label();
        private readonly NotifyIcon tray = new NotifyIcon();
        private readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
        private DesktopSession session;
        private bool stopped, hidForApp;

        internal LauncherForm(LaunchSettings configuration)
        {
            settings = configuration;
            Text = "CampusOps · GearShift";
            Font = new Font("Microsoft YaHei UI", 10);
            ClientSize = new Size(610, 330);
            MinimumSize = new Size(550, 360);
            StartPosition = FormStartPosition.CenterScreen;
            MaximizeBox = false;
            Label intro = new Label { Left = 22, Top = 18, Width = 560, Height = 44,
                Text = "登记器材故障、查看调整方案并完成审批。\n默认离线启动；真实 AI 需在下方明确启用。" };
            model.SetBounds(22, 75, 560, 28);
            model.Text = "启用真实AI（消耗额度）";
            protectedAccount.SetBounds(22, 110, 560, 44);
            protectedAccount.Text = "我已确认服务商免费额度，并启用账户账单硬停止保护";
            protectedAccount.Enabled = false;
            model.CheckedChanged += delegate { protectedAccount.Enabled = model.Checked;
                if (!model.Checked) protectedAccount.Checked = false; };
            start.SetBounds(22, 166, 128, 34);
            start.Text = "启动应用";
            start.Click += StartSession;
            Button exit = new Button { Left = 164, Top = 166, Width = 110, Height = 34, Text = "退出" };
            exit.Click += delegate { Close(); };
            status.SetBounds(22, 216, 560, 45);
            status.Text = "准备就绪。启动本身不发送模型请求。";
            sessionPath.SetBounds(22, 266, 560, 54);
            sessionPath.AutoEllipsis = true;
            Controls.AddRange(new Control[] { intro, model, protectedAccount, start, exit, status, sessionPath });
            tray.Icon = SystemIcons.Application;
            tray.Text = "CampusOps · GearShift";
            ContextMenuStrip menu = new ContextMenuStrip();
            menu.Items.Add("显示启动器", null, delegate { ShowLauncher(); });
            menu.Items.Add("退出本次会话", null, delegate { Close(); });
            tray.ContextMenuStrip = menu;
            tray.DoubleClick += delegate { ShowLauncher(); };
            timer.Interval = 400;
            timer.Tick += PollSession;
        }

        private void StartSession(object sender, EventArgs arguments)
        {
            if (model.Checked && !protectedAccount.Checked)
            {
                status.Text = "请先确认服务商免费额度和账户账单硬停止保护。";
                return;
            }
            start.Enabled = model.Enabled = protectedAccount.Enabled = false;
            try
            {
                session = new DesktopSession(settings, model.Checked, protectedAccount.Checked);
                sessionPath.Text = "本次会话：" + session.DirectoryPath;
                status.Text = session.Status;
                tray.Visible = true;
                timer.Start();
            }
            catch (LauncherFailure failure) { status.Text = failure.Message; ShowFailure(failure.Message); }
            catch { status.Text = "启动未完成。请检查完整交付包及目录权限。"; ShowFailure(status.Text); }
        }

        private void PollSession(object sender, EventArgs arguments)
        {
            try
            {
                session.Tick();
                status.Text = session.Status;
                if (session.Stage == SessionStage.WindowClosed)
                {
                    CleanupResult result = StopSession(true);
                    if (result.Confirmed) Close();
                    else { ShowLauncher(); ShowFailure(result.Message); }
                }
                else if (session.Stage == SessionStage.Running && !hidForApp)
                {
                    hidForApp = true;
                    Hide();
                }
            }
            catch (LauncherFailure failure)
            {
                CleanupResult result = StopSession(failure.ChildCleanupConfirmed);
                ShowLauncher();
                status.Text = failure.Message;
                ShowFailure(failure.Message + "\n" + result.Message);
            }
            catch
            {
                CleanupResult result = StopSession(true);
                ShowLauncher();
                status.Text = "会话状态无法确认，已停止继续操作。";
                ShowFailure(status.Text + "\n" + result.Message);
            }
        }

        private CleanupResult StopSession(bool childConfirmed)
        {
            timer.Stop();
            stopped = true;
            CleanupResult result = session.Stop(childConfirmed);
            status.Text = result.Message;
            return result;
        }

        private void ShowLauncher() { Show(); WindowState = FormWindowState.Normal; Activate(); }
        private void ShowFailure(string safeMessage)
        {
            MessageBox.Show(this, safeMessage, "CampusOps", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }

        protected override void OnFormClosing(FormClosingEventArgs arguments)
        {
            if (session != null && !stopped)
            {
                CleanupResult result = StopSession(true);
                if (!result.Confirmed) ShowFailure(result.Message + "\n" + session.DirectoryPath);
            }
            base.OnFormClosing(arguments);
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                timer.Dispose();
                tray.Visible = false;
                if (tray.ContextMenuStrip != null) tray.ContextMenuStrip.Dispose();
                tray.Dispose();
                if (session != null) session.Dispose();
            }
            base.Dispose(disposing);
        }
    }

    internal static class LauncherProgram
    {
        [STAThread]
        private static int Main(string[] arguments)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            try
            {
                LaunchSettings settings = LaunchSettings.Parse(arguments, AppDomain.CurrentDomain.BaseDirectory);
                using (LauncherForm form = new LauncherForm(settings)) Application.Run(form);
                return 0;
            }
            catch (LauncherFailure failure)
            {
                MessageBox.Show(failure.Message, "CampusOps", MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
            catch
            {
                MessageBox.Show("启动器未能完成操作。请检查完整交付包和目录权限。", "CampusOps",
                    MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
            return 1;
        }
    }
}
