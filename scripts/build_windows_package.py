"""Build a new, project-local Windows folder package. Never launch it or load keys."""

import argparse
import importlib.util
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import tomllib
from uuid import uuid4
import zipfile

ROOT = Path(__file__).resolve().parents[1]
COMPILER = Path("C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe")
ACCEPTED_LAUNCHER = {
    "source_commit": "4e2045acf00f2cabf677a4df53542ae1e16f7204",
    "archive_sha256": "0f1407cfbd291848c3160c7c1f0bae64460faf2f1b6814e26f71d78678623ed5",
    "exe_sha256": "fecea063a9473a37c2c911c053b33f719cb603d268a52cb64f802692f782cd23",
    "source_hashes": {
        "src/campusops/ui/windows_launcher.cs": "5dd5c5612618e1955aa6b76f16646ee5c92e2f3ba3282f2affce6de05018a5c8",
        "src/campusops/ui/windows_processes.cs": "0245ab8e61fcfc554363e7f7bf0ee8498cd26666897ee7f75bd7760b6925726b",
    },
}


def accepted_launcher(release: Path, source_hashes: dict[str, str]) -> tuple[bytes, dict]:
    """Reuse only the pinned, unchanged launcher; no compilation fallback or trust changes."""
    release = release.resolve()
    if not release.is_relative_to((ROOT / "var/releases").resolve()):
        raise ValueError("Launcher artifact must stay in the independent project releases")
    manifest = json.loads((release / "release-manifest.json").read_text(encoding="utf-8"))
    archive = release / "CampusOps-Windows-Engineering.zip"
    if (source_hashes != ACCEPTED_LAUNCHER["source_hashes"]
            or manifest["native_sources"] != source_hashes
            or manifest["source_commit"] != ACCEPTED_LAUNCHER["source_commit"]
            or sha256(archive.read_bytes()).hexdigest() != ACCEPTED_LAUNCHER["archive_sha256"]):
        raise ValueError("Launcher source or pinned accepted archive mismatch; preserve inputs")
    with zipfile.ZipFile(archive) as bundle:
        binary = bundle.read("CampusOps/CampusOps.exe")
    if (sha256(binary).hexdigest() != ACCEPTED_LAUNCHER["exe_sha256"]
            or manifest["files"]["CampusOps.exe"] != ACCEPTED_LAUNCHER["exe_sha256"]):
        raise ValueError("Pinned accepted launcher bytes mismatch; preserve inputs")
    return binary, {
        "mode": "reused_accepted_unchanged_source_artifact",
        "artifact_source_commit": manifest["source_commit"],
        "artifact_archive_sha256": ACCEPTED_LAUNCHER["archive_sha256"],
        "exe_sha256": ACCEPTED_LAUNCHER["exe_sha256"],
        "current_native_sources_match": True,
        "policy_or_signature_modified": False,
    }


def build(runtime_source: Path, launcher_release: Path | None = None) -> dict:
    runtime_source = runtime_source.resolve()
    if (ROOT.drive.upper() != "D:" or Path.cwd().resolve() != ROOT
            or not runtime_source.is_relative_to(ROOT / "var/releases")
            or not (runtime_source / "python.exe").is_file()):
        raise ValueError("Use the reviewed project-local redistributable runtime")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT).strip():
        raise ValueError("Commit the reviewed package sources before building")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    source_version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    component_spec = importlib.util.spec_from_file_location('campusops_runtime_components', ROOT / 'scripts/runtime_components.py')
    component_module = importlib.util.module_from_spec(component_spec)
    component_spec.loader.exec_module(component_module)
    native_component_validation = component_module.validate_runtime(runtime_source)
    proof_code = """import campusops,importlib.metadata as m,json
from pathlib import Path
from hashlib import sha256
p=Path(campusops.__file__).resolve().parent
files={f.relative_to(p).as_posix():sha256(f.read_bytes()).hexdigest() for f in p.rglob('*') if f.is_file() and f.suffix in ('.py','.sql','.json')}
print(json.dumps({'version':campusops.__version__,'installed_version':m.version('campusops'),'package':str(p),'files':files}))
"""
    runtime_proof = json.loads(subprocess.check_output(
        [str(runtime_source / "python.exe"), "-c", proof_code], cwd=ROOT, text=True,
    ))
    source_package = ROOT / "src/campusops"
    expected_files = {file.relative_to(source_package).as_posix(): sha256(file.read_bytes()).hexdigest()
                      for file in source_package.rglob("*") if file.is_file() and file.suffix in (".py", ".sql", ".json")}
    if (runtime_proof["version"] != source_version or runtime_proof["installed_version"] != source_version
            or Path(runtime_proof["package"]).resolve() != runtime_source / "Lib/site-packages/campusops"
            or runtime_proof["files"] != expected_files):
        raise ValueError("Staged runtime must contain the exact current wheel sources and version; preserve staging")
    sources = [ROOT / "src/campusops/ui" / name for name in ("windows_launcher.cs", "windows_processes.cs")]
    native_hashes = {p.relative_to(ROOT).as_posix(): sha256(p.read_bytes()).hexdigest() for p in sources}
    launcher_bytes, launcher_provenance = accepted_launcher(launcher_release, native_hashes) if launcher_release else (
        None, {"mode": "compiled_from_current_sources", "policy_or_signature_modified": False},
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    release = ROOT / "var/releases" / ("windows-package-" + stamp)
    package = release / "CampusOps"
    package.mkdir(parents=True, exist_ok=False)
    shutil.copytree(runtime_source, package / "runtime")
    for relative in ("AGENTS.md", "pyproject.toml", "requirements.lock.txt", ".env.example", ".streamlit/config.toml",
                     "docs/project-spec-v2.md", "docs/contracts.md", "docs/architecture.md",
                     "assets/demo_seed.json", "streamlit_app.py", "scripts/desktop_server.py",
                     "scripts/campusops_operator.py", "scripts/diagnose_environment.py",
                     "docs/quick-start-rc6.md", "docs/teammate-checklist.md", "docs/third-party-notices.md",
                     "docs/totp-daily-use.md", "docs/workbench-rc8.md", "docs/bookings-rc9.md", "docs/booking-change-rc10.md",
                     "docs/contracts-v1.6.0.schema.json", "docs/contracts-v1.7.0.schema.json", "docs/equipment-catalog-rc11.md",
                     "docs/runtime-fix-rc12.md", "docs/totp-security-rc13.md", "docs/totp-upgrade-rc13.md",
                     "docs/runtime-fix-rc14.md", "docs/windows-startup-rc13-diagnostics.md",
                     "docs/easy-deploy-rc15.md",
                     "docs/inputs/native-components-rc14.json", "docs/licenses/CPython-LICENSE.txt",
                     "docs/inputs/native-components-rc12.json", "scripts/runtime_components.py",
                     "docs/licenses/libffi-LICENSE.txt", "docs/licenses/SQLite-NOTICE.txt"):
        destination = package / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    shutil.copyfile(ROOT / "docs/windows-package.md", package / "Windows使用说明.md")
    portable_entry = "\r\n".join((
        "@echo off", "setlocal", "chcp 65001 >nul", 'set "CampusOpsPackageRoot=%~dp0."',
        'if not exist "%CampusOpsPackageRoot%\\CampusOps.exe" (',
        "  echo 未找到完整程序，请将整个压缩包解压到 D 盘后再启动。",
        "  pause", "  exit /b 1", ")",
        'start "" /D "%CampusOpsPackageRoot%" "%CampusOpsPackageRoot%\\CampusOps.exe"',
        "if errorlevel 1 (", "  echo CampusOps 未能启动，请保留此提示并运行环境诊断。",
        "  pause", "  exit /b 1", ")", "exit /b 0", "",
    )).encode("utf-8")
    with (package / "启动CampusOps.cmd").open("xb") as handle:
        handle.write(portable_entry)
    diagnostic_entry = "\r\n".join((
        "@echo off", "setlocal", "chcp 65001 >nul",
        'if /I not "%~d0"=="D:" (',
        "  echo 请先把完整 CampusOps 文件夹解压到 D 盘。",
        "  pause", "  exit /b 2", ")", 'cd /d "%~dp0"',
        'set "PYTHONUTF8=1"', 'set "PYTHONPYCACHEPREFIX=%~dp0.cache\\python"',
        'set "TEMP=%~dp0.tmp"', 'set "TMP=%TEMP%"',
        'if not exist "%TEMP%" mkdir "%TEMP%"',
        '"%~dp0runtime\\python.exe" "%~dp0scripts\\diagnose_environment.py" --project-root "%~dp0." --save',
        'set "CampusOpsResult=%errorlevel%"', "pause", "exit /b %CampusOpsResult%", "",
    )).encode("utf-8")
    with (package / "诊断环境.cmd").open("xb") as handle:
        handle.write(diagnostic_entry)
    shutil.copyfile(ROOT / "docs/inputs/python-runtime-source.json", release / "runtime-source.json")
    executable = package / "CampusOps.exe"
    if launcher_bytes is not None:
        with executable.open("xb") as handle:
            handle.write(launcher_bytes)
    else:
        subprocess.run([str(COMPILER), "/nologo", "/utf8output", "/warn:4", "/warnaserror", "/platform:x64",
                        "/target:winexe", "/out:" + str(executable), "/r:System.dll", "/r:System.Core.dll",
                        "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll", *map(str, sources)], check=True)
    subprocess.run([str(package / "runtime/python.exe"), "-m", "pip", "check"], cwd=package, check=True)
    files = [p for p in package.rglob("*") if p.is_file()]
    forbidden = [p for p in files if p.relative_to(package).parts[0] in {".git", ".venv", "var", ".cache", ".tmp"}
                 or p.name == ".env"]
    if forbidden:
        raise ValueError("Unexpected private paths in new package; preserve for inspection")
    manifest = {
        "schema_version": 1, "source_commit": head, "package": str(package),
        "package_version": source_version, "installed_runtime_source_files": len(expected_files),
        "staged_wheel_sources_match": True,
        "native_component_validation": native_component_validation,
        "launcher": launcher_provenance,
        "form": "Windows x64 native launcher + isolated local service + Edge application window",
        "default_model_enabled": False, "api_keys_included": False, "demo_database_included": False,
        "access_lock": {"type": "single-factor offline TOTP", "digits": 6, "interval_seconds": 30,
                        "secret_protection": "Windows DPAPI CurrentUser", "binding_included": False,
                        "owner_enrollment_tools_included": False,
                        "unconfigured_behavior": "locked; contact owner"},
        "native_sources": native_hashes,
        "files": {p.relative_to(package).as_posix(): sha256(p.read_bytes()).hexdigest() for p in files},
        "native_runtime_acceptance": "NotRun for this new artifact; append a separate actual-product receipt after execution",
        "independent_lifecycle_probe": "Build does not infer execution; see this candidate's separate actual Windows acceptance receipt",
        "limitations": ["Real AI and actual other-PC acceptance are recorded separately",
                        "Requires Windows x64, D-drive location, Microsoft Edge and .NET Framework 4",
                        "Four pinned native components retain original publisher signatures; this does not sign the entire application",
                        "No signing certificate included; do not change system trust or policy to run tests"],
    }
    archive = release / "CampusOps-Windows-Engineering.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for p in files:
            bundle.write(p, "CampusOps/" + p.relative_to(package).as_posix())
    manifest["archive"] = {"path": str(archive), "sha256": sha256(archive.read_bytes()).hexdigest(),
                           "bytes": archive.stat().st_size}
    with (release / "release-manifest.json").open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return {"package": str(package), "source_commit": head, "archive": manifest["archive"],
            "native_runtime_acceptance": manifest["native_runtime_acceptance"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-source", required=True, type=Path)
    parser.add_argument("--reuse-launcher-from", type=Path,
                        help="Explicitly reuse the pinned accepted artifact only when both C# sources are unchanged")
    arguments = parser.parse_args()
    print(json.dumps(build(arguments.runtime_source, arguments.reuse_launcher_from), ensure_ascii=False, indent=2))
