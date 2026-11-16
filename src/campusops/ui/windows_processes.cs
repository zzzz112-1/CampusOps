// Windows desktop process ownership. No shell, process-name kill, or breakaway.
// Compile with windows_launcher.cs using the .NET Framework 4 compiler.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;

namespace CampusOps.Desktop
{
    internal enum FailurePhase { Other, CreateProcess, AssignJob, ResumeChild }

    internal sealed class LauncherFailure : Exception
    {
        internal readonly bool ChildCleanupConfirmed;
        internal readonly FailurePhase Phase;

        internal LauncherFailure(string safeMessage) : base(safeMessage)
        {
            ChildCleanupConfirmed = true;
        }

        internal LauncherFailure(string safeMessage, int code, bool childStopped)
            : this(safeMessage, code, childStopped, FailurePhase.Other) { }

        internal LauncherFailure(string safeMessage, int code, bool childStopped, FailurePhase phase)
            : base(safeMessage + " (Windows " + code + ")" +
                (childStopped ? "" : "；未能确认挂起子进程已停止。"))
        {
            ChildCleanupConfirmed = childStopped;
            Phase = phase;
        }
    }

    internal sealed class KernelHandle : SafeHandleZeroOrMinusOneIsInvalid
    {
        internal KernelHandle(IntPtr value) : base(true) { SetHandle(value); }
        protected override bool ReleaseHandle() { return Native.CloseHandle(handle); }
    }

    internal sealed class OwnedChild : IDisposable
    {
        internal readonly KernelHandle Handle;
        internal readonly uint Id;

        internal OwnedChild(IntPtr handle, uint id)
        {
            Handle = new KernelHandle(handle);
            Id = id;
        }

        internal bool Wait(int milliseconds)
        {
            uint result = Native.WaitForSingleObject(Handle, (uint)milliseconds);
            if (result == 0) return true;
            if (result == 258) return false;
            throw new LauncherFailure("无法确认本次子进程状态。", Marshal.GetLastWin32Error(), false);
        }

        public void Dispose() { Handle.Dispose(); }
    }

    internal sealed class OwnedJob : IDisposable
    {
        private readonly KernelHandle handle;

        internal OwnedJob()
        {
            handle = new KernelHandle(Native.CreateJobObject(IntPtr.Zero, null));
            if (handle.IsInvalid)
                throw new LauncherFailure("无法创建会话进程隔离。", Marshal.GetLastWin32Error(), true);
            Native.ExtendedLimits limits = new Native.ExtendedLimits();
            limits.Basic.LimitFlags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if (!Native.SetInformationJobObject(handle, 9, ref limits,
                (uint)Marshal.SizeOf(typeof(Native.ExtendedLimits))))
            {
                int code = Marshal.GetLastWin32Error();
                handle.Dispose();
                throw new LauncherFailure("无法启用会话退出保护。", code, true);
            }
        }

        internal OwnedChild Start(string executable, IList<string> arguments, string directory, bool hidden)
        {
            return StartSuspended(handle.DangerousGetHandle(), executable, arguments, directory, hidden);
        }

        // The invalid-handle branch is exercised only by the TEST_INJECTED probe.
        // Production always supplies the live, private job created above.
        internal static OwnedChild StartSuspended(IntPtr job, string executable,
            IList<string> arguments, string directory, bool hidden)
        {
            StringBuilder command = new StringBuilder(WindowsArguments.Quote(executable));
            foreach (string argument in arguments) command.Append(" ").Append(WindowsArguments.Quote(argument));
            Native.StartupInfo startup = new Native.StartupInfo();
            startup.Size = (uint)Marshal.SizeOf(typeof(Native.StartupInfo));
            if (hidden) startup.Flags = 1; // STARTF_USESHOWWINDOW, SW_HIDE (0)
            Native.ProcessInfo process;
            uint flags = 0x4U | (hidden ? 0x08000000U : 0U); // suspended, optionally no console
            if (!Native.CreateProcess(executable, command, IntPtr.Zero, IntPtr.Zero, false,
                flags, IntPtr.Zero, directory, ref startup, out process))
                throw new LauncherFailure("无法启动本次子进程。", Marshal.GetLastWin32Error(), true, FailurePhase.CreateProcess);
            OwnedChild child = new OwnedChild(process.Process, process.ProcessId);
            using (KernelHandle thread = new KernelHandle(process.Thread))
            {
                string failure = null;
                FailurePhase phase = FailurePhase.Other;
                int code = 0;
                if (!Native.AssignProcessToJobObject(job, child.Handle))
                {
                    code = Marshal.GetLastWin32Error();
                    failure = "子进程隔离失败，未恢复执行。";
                    phase = FailurePhase.AssignJob;
                }
                else if (Native.ResumeThread(thread) == UInt32.MaxValue)
                {
                    code = Marshal.GetLastWin32Error();
                    failure = "无法恢复已隔离的子进程。";
                    phase = FailurePhase.ResumeChild;
                }
                if (failure != null)
                {
                    Native.TerminateProcess(child.Handle, 1);
                    bool stopped = Native.WaitForSingleObject(child.Handle, 2000) == 0;
                    child.Dispose();
                    throw new LauncherFailure(failure, code, stopped, phase);
                }
            }
            return child;
        }

        internal HashSet<uint> ProcessIds()
        {
            int capacity = 64;
            for (int attempt = 0; attempt < 5; attempt++)
            {
                int size = checked(8 + capacity * IntPtr.Size);
                IntPtr buffer = Marshal.AllocHGlobal(size);
                try
                {
                    uint returned;
                    if (!Native.QueryInformationJobObject(handle, 3, buffer, (uint)size, out returned))
                    {
                        int code = Marshal.GetLastWin32Error();
                        if (code == 234) { capacity *= 4; continue; } // ERROR_MORE_DATA
                        throw new LauncherFailure("无法查询会话进程归属。", code, true);
                    }
                    int assigned = Marshal.ReadInt32(buffer, 0);
                    int count = Marshal.ReadInt32(buffer, 4);
                    if (assigned > count) { capacity = Math.Max(capacity * 4, assigned); continue; }
                    if (count < 0 || count > capacity) throw new LauncherFailure("会话进程列表不可确认。");
                    HashSet<uint> ids = new HashSet<uint>();
                    for (int index = 0; index < count; index++)
                        ids.Add(unchecked((uint)Marshal.ReadIntPtr(buffer, 8 + index * IntPtr.Size).ToInt64()));
                    return ids;
                }
                finally { Marshal.FreeHGlobal(buffer); }
            }
            throw new LauncherFailure("会话进程列表持续变化，无法确认。");
        }

        internal bool Owns(uint processId)
        {
            using (KernelHandle process = new KernelHandle(Native.OpenProcess(0x1000, false, processId)))
            {
                if (process.IsInvalid)
                {
                    int code = Marshal.GetLastWin32Error();
                    if (code == 87 || code == 1168) return false; // the process has exited
                    throw new LauncherFailure("无法复核窗口或服务的进程归属。", code, true);
                }
                bool owned;
                if (!Native.IsProcessInJob(process, handle, out owned))
                    throw new LauncherFailure("无法复核会话进程归属。", Marshal.GetLastWin32Error(), true);
                return owned;
            }
        }

        internal bool HasVisibleEdgeWindow()
        {
            HashSet<uint> ids = ProcessIds();
            bool found = false;
            Exception failure = null;
            Native.EnumWindow callback = delegate(IntPtr window, IntPtr unused)
            {
                try
                {
                    uint id;
                    Native.GetWindowThreadProcessId(window, out id);
                    if (ids.Contains(id) && Native.IsWindowVisible(window))
                    {
                        StringBuilder className = new StringBuilder(128);
                        if (Native.GetClassName(window, className, className.Capacity) == 0)
                            return true; // a window may disappear during enumeration
                        if (className.ToString().StartsWith("Chrome_WidgetWin_", StringComparison.Ordinal) && Owns(id))
                            found = true;
                    }
                    return true;
                }
                catch (Exception error) { failure = error; return false; }
            };
            bool enumerated = Native.EnumWindows(callback, IntPtr.Zero);
            GC.KeepAlive(callback);
            if (failure != null) throw failure;
            if (!enumerated) throw new LauncherFailure("无法检查本次应用窗口。");
            return found;
        }

        internal bool StopAndConfirm()
        {
            if (handle.IsClosed) return false;
            try
            {
                Native.TerminateJobObject(handle, 0);
                Stopwatch deadline = Stopwatch.StartNew();
                do
                {
                    if (ProcessIds().Count == 0) return true;
                    Thread.Sleep(25);
                } while (deadline.ElapsedMilliseconds < 2000);
                return false;
            }
            catch { return false; } // an observation failure must never become a success claim
        }

        public void Dispose() { handle.Dispose(); }

        internal bool OwnsLoopbackListener(int port, uint expectedProcessId)
        {
            // Inspect ownership of this one port, never adopt an unrelated server.
            int size = 0;
            uint result = Native.GetExtendedTcpTable(IntPtr.Zero, ref size, false, 2, 3, 0);
            if (result != 122 || size < 4) throw new LauncherFailure("无法确认本地服务端口归属。");
            IntPtr buffer = Marshal.AllocHGlobal(size);
            try
            {
                result = Native.GetExtendedTcpTable(buffer, ref size, false, 2, 3, 0);
                if (result == 122) return false; // table changed; wait for the next readiness observation
                if (result != 0) throw new LauncherFailure("无法查询本地服务端口归属。");
                int count = Marshal.ReadInt32(buffer);
                if (count < 0 || count > (size - 4) / 24) throw new LauncherFailure("本地端口列表不可确认。");
                for (int index = 0; index < count; index++)
                {
                    int offset = 4 + index * 24;
                    uint address = unchecked((uint)Marshal.ReadInt32(buffer, offset + 4));
                    uint rawPort = unchecked((uint)Marshal.ReadInt32(buffer, offset + 8));
                    int localPort = (int)(((rawPort & 255) << 8) | ((rawPort >> 8) & 255));
                    if (address == 0x0100007f && localPort == port)
                    {
                        uint owner = unchecked((uint)Marshal.ReadInt32(buffer, offset + 20));
                        if (owner != expectedProcessId || !Owns(owner))
                            throw new LauncherFailure("本地端口属于其他进程，未连接或复用该服务。");
                        return true;
                    }
                }
                return false;
            }
            finally { Marshal.FreeHGlobal(buffer); }
        }
    }

    internal static class WindowsArguments
    {
        internal static string Quote(string value)
        {
            if (value == null || value.IndexOf('\0') >= 0) throw new LauncherFailure("启动参数无效。");
            StringBuilder result = new StringBuilder("\"");
            int slashes = 0;
            foreach (char character in value)
            {
                if (character == '\\') { slashes++; continue; }
                if (character == '"') result.Append('\\', slashes * 2 + 1).Append('"');
                else result.Append('\\', slashes).Append(character);
                slashes = 0;
            }
            return result.Append('\\', slashes * 2).Append('"').ToString();
        }
    }

    internal static class Native
    {
        [StructLayout(LayoutKind.Sequential)]
        internal struct BasicLimits
        {
            internal long PerProcessTime, PerJobTime;
            internal uint LimitFlags;
            internal UIntPtr MinimumWorkingSet, MaximumWorkingSet;
            internal uint ActiveProcessLimit;
            internal UIntPtr Affinity;
            internal uint PriorityClass, SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        internal struct IoCounters
        {
            internal ulong ReadOperations, WriteOperations, OtherOperations;
            internal ulong ReadBytes, WriteBytes, OtherBytes;
        }

        [StructLayout(LayoutKind.Sequential)]
        internal struct ExtendedLimits
        {
            internal BasicLimits Basic;
            internal IoCounters Io;
            internal UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        internal struct StartupInfo
        {
            internal uint Size;
            internal string Reserved, Desktop, Title;
            internal uint X, Y, XSize, YSize, XChars, YChars, FillAttribute, Flags;
            internal short ShowWindow, ReservedSize;
            internal IntPtr ReservedBytes, Input, Output, Error;
        }

        [StructLayout(LayoutKind.Sequential)]
        internal struct ProcessInfo
        {
            internal IntPtr Process, Thread;
            internal uint ProcessId, ThreadId;
        }

        internal delegate bool EnumWindow(IntPtr window, IntPtr parameter);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        internal static extern IntPtr CreateJobObject(IntPtr attributes, string name);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool SetInformationJobObject(KernelHandle job, int kind, ref ExtendedLimits limits, uint size);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool AssignProcessToJobObject(IntPtr job, KernelHandle process);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool QueryInformationJobObject(KernelHandle job, int kind, IntPtr data, uint size, out uint returned);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool IsProcessInJob(KernelHandle process, KernelHandle job, out bool result);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool TerminateJobObject(KernelHandle job, uint code);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll", EntryPoint = "CreateProcessW", CharSet = CharSet.Unicode, SetLastError = true)]
        internal static extern bool CreateProcess(string executable, StringBuilder command, IntPtr processAttributes,
            IntPtr threadAttributes, bool inheritHandles, uint flags, IntPtr environment, string directory,
            ref StartupInfo startup, out ProcessInfo process);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern uint ResumeThread(KernelHandle thread);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool TerminateProcess(KernelHandle process, uint code);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern uint WaitForSingleObject(KernelHandle handle, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern IntPtr OpenProcess(uint access, bool inherit, uint processId);
        [DllImport("user32.dll", SetLastError = true)]
        internal static extern bool EnumWindows(EnumWindow callback, IntPtr parameter);
        [DllImport("user32.dll")]
        internal static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
        [DllImport("user32.dll")]
        internal static extern bool IsWindowVisible(IntPtr window);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        internal static extern int GetClassName(IntPtr window, StringBuilder className, int capacity);
        [DllImport("iphlpapi.dll", SetLastError = true)]
        internal static extern uint GetExtendedTcpTable(IntPtr table, ref int size, bool sorted,
            uint family, int tableClass, uint reserved);
    }
}
