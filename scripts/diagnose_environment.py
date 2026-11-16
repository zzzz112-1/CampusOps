"""Chinese, local-only package diagnostics. Never reads keys or opens a business DB."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
from importlib import metadata, util
from importlib import import_module
import json
import os
from pathlib import Path
import platform
import struct
import sys
import tomllib
from uuid import uuid4


def collect(root: Path, *, development: bool = False) -> dict:
    root = root.resolve()
    rows = []

    def check(code, label, passed, detail):
        rows.append({"code": code, "label": label, "status": "PASS" if passed else "FAIL", "detail": detail})

    check("windows", "Windows 64位", os.name == "nt" and struct.calcsize("P") == 8,
          f"{platform.system()} {platform.release()} / {struct.calcsize('P') * 8}位解释器")
    check("drive", "D盘独立目录", root.drive.upper() == "D:", str(root))
    check("python", "Python 3.12", sys.version_info[:2] == (3, 12), platform.python_version())
    for native_name, native_label in (('ctypes', 'Windows原生调用组件'), ('sqlite3', 'SQLite数据库组件')):
        try:
            native_module = import_module(native_name)
            check('native_' + native_name, native_label, True,
                  '已加载' + (' / SQLite ' + native_module.sqlite_version if native_name == 'sqlite3' else ''))
        except Exception:
            check('native_' + native_name, native_label, False,
                  '原生组件无法加载；请检查Windows智能应用控制的拦截记录，并使用完整的RC12或后续修复包。不要关闭防护。')
    markers = all((root / name).is_file() for name in ("AGENTS.md", "pyproject.toml", "docs/project-spec-v2.md"))
    check("project", "完整项目文件", markers, "已找到项目标记" if markers else "请完整解压软件包，保留目录结构")
    expected_python = root / (".venv/Scripts/python.exe" if development else "runtime/python.exe")
    check("interpreter", "本目录运行时", Path(sys.executable).resolve() == expected_python.resolve(),
          "应使用本树.venv" if development else "应使用包内runtime/python.exe，无需另装Python")
    expected = {}
    if markers:
        try:
            version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
            expected = {line.split("==")[0]: line.split("==")[1]
                        for line in (root / "requirements.lock.txt").read_text(encoding="utf-8").splitlines()
                        if line.strip() and not line.startswith("#") and "==" in line}
            expected["campusops"] = version
            drift = []
            for name, wanted in expected.items():
                try:
                    actual = metadata.version(name)
                except metadata.PackageNotFoundError:
                    actual = "未安装"
                if actual != wanted:
                    drift.append({"package": name, "expected": wanted, "actual": actual})
            check("dependencies", "版本与锁定依赖", not drift,
                  drift if drift else f"{len(expected)}项版本一致")
        except (OSError, KeyError, ValueError):
            version = "UNKNOWN"
            check("dependencies", "版本与锁定依赖", False, "依赖清单无法读取；请使用完整交付包")
    else:
        version = "UNKNOWN"
    module = util.find_spec("campusops")
    expected_module = root / ("src/campusops/__init__.py" if development else "runtime/Lib/site-packages/campusops/__init__.py")
    actual_module = Path(module.origin).resolve() if module and module.origin else None
    check("module_scope", "应用加载位置", actual_module == expected_module.resolve(),
          "应用应从本目录源码加载" if development else "应用应从包内site-packages加载")
    edge_paths = [Path(value) / "Microsoft/Edge/Application/msedge.exe"
                  for name in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")
                  if (value := os.environ.get(name))]
    check("edge", "Microsoft Edge", any(path.is_file() for path in edge_paths),
          "需要已安装的Microsoft Edge，不能把其他浏览器窗口当成本软件会话")
    framework = Path(os.environ.get("WINDIR", "C:/Windows")) / "Microsoft.NET/Framework64/v4.0.30319/clr.dll"
    check("dotnet", ".NET Framework 4", framework.is_file(), "原生启动器需要Windows的.NET Framework 4")
    catalog = None
    if markers and root.drive.upper() == "D:" and actual_module == expected_module.resolve():
        try:
            from campusops.domain.rules import load_booking_rules
            loaded = load_booking_rules()
            catalog = {"id": loaded.ruleset_id, "version": loaded.version, "fingerprint": loaded.fingerprint,
                       "rules": len(loaded.rules), "fictional": loaded.fictional}
            check("rules", "虚构规则目录", True, catalog)
        except Exception:
            check("rules", "虚构规则目录", False, "规则文件缺失或无效，不能生成或批准方案")
    else:
        check("rules", "虚构规则目录", False, "先确认完整目录和正确运行时")
    if not development:
        check("launcher", "桌面启动文件", (root / "CampusOps.exe").is_file(),
              "先双击CampusOps.exe或启动CampusOps.cmd，再点击“启动应用”")
    return {
        "schema_version": 1, "mode": "LOCAL_DIAGNOSTICS_NO_MODEL_REQUEST",
        "status": "PASS" if all(row["status"] == "PASS" for row in rows) else "NEEDS_ATTENTION",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(), "project_root": str(root),
        "package_version": version, "development": development,
        "host_fingerprint": sha256(platform.node().encode("utf-8")).hexdigest(),
        "cross_machine_acceptance": "NotRun; this diagnostic alone is not an independent-machine or workflow test",
        "checks": rows, "model_requests": 0,
        "env_file_present": (root / ".env").is_file(), "env_content_read": False,
        "business_database_opened": False, "budget_ledger_read_or_reset": False,
        "mode_help": {
            "offline": "启动默认不启用模型；有影响但未配置时明确不可用，不伪造AI方案",
            "real_ai": "需手动配置和明确启用，并确认免费额度与账户保护；密钥存在不等于授权请求",
            "history": "历史回放只展示原记录；不能代替当前版本真实执行或再次批准",
        },
        "demo_time": "固定预约为2026-11-10至11-11；单预约演示请主动填入2026-11-11 08:00北京时间",
    }


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--development", action="store_true")
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = collect(args.project_root, development=args.development)
    print("CampusOps 环境诊断（不连接模型，不读取密钥或业务库）")
    for row in report["checks"]:
        state = "通过" if row["status"] == "PASS" else "需处理"
        detail = json.dumps(row["detail"], ensure_ascii=False) if isinstance(row["detail"], (list, dict)) else row["detail"]
        print(f"[{state}] {row['label']}：{detail}")
    print(report["demo_time"])
    if args.save or args.output:
        root = args.project_root.resolve()
        target = (args.output or root / "var/logs" / ("environment-" + uuid4().hex[:8] + ".json")).resolve()
        if root.drive.upper() != "D:" or not target.is_relative_to(root) or target.suffix != ".json":
            print("报告只允许保存到本项目D盘目录内的全新JSON文件。")
            return 2
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
        except FileExistsError:
            print("同名报告已存在，已保留；请使用新文件名。")
            return 2
        print(f"诊断报告：{target}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
