"""Reuse pinned RC15 third-party runtime; package this independent review build."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from uuid import uuid4
import zipfile

ROOT = Path(__file__).absolute().parents[1]
BASE_SHA256 = "a9520050a00ff191f239fa1c2f1f90c540ff1652dad747902cec8d28c5e9c7cf"


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def build(runtime_zip):
    assert ROOT.drive.casefold() == "d:" and ROOT != Path("D:/CampusOps")
    assert digest(runtime_zip) == BASE_SHA256, "Pinned runtime archive mismatch"
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    assert not subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT).strip(), "Commit reviewer source before building"
    out = ROOT / "var/releases" / ("RC15-Review-1-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6])
    package = out / "CampusOps"
    package.mkdir(parents=True)
    with zipfile.ZipFile(runtime_zip) as old:
        for item in old.infolist():
            if not item.filename.startswith("CampusOps/runtime/") or item.is_dir():
                continue
            relative = Path(item.filename[len("CampusOps/"):])
            assert not relative.is_absolute() and ".." not in relative.parts
            lower = relative.as_posix().lower()
            if ("__pycache__" in relative.parts or lower.endswith((".pyc", ".pdb"))
                    or "site-packages/campusops" in lower or "site-packages/__editable__" in lower):
                continue
            assert (item.external_attr >> 16) & 0o170000 != 0o120000
            target = package / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(old.read(item))
    subprocess.run([str(ROOT / ".venv/Scripts/python.exe"), "-m", "pip", "wheel", "--no-index",
        "--no-deps", "--no-build-isolation", "--wheel-dir", str(out), str(ROOT)], check=True, cwd=ROOT)
    wheel = next(out.glob("campusops-0.2.0rc15.post1-*.whl"))
    site = package / "runtime/Lib/site-packages"
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            relative = Path(name)
            assert not relative.is_absolute() and ".." not in relative.parts
            target = site / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
    names = ["streamlit_app.py", "reviewer-build.json", "pyproject.toml", "requirements.lock.txt", "AGENTS.md",
             "README.md", "Reviewer_Entry.md", ".streamlit/config.toml", "assets/demo_seed.json",
             "scripts/reviewer_server.py", "scripts/desktop_server.py", "docs/project-spec-v2.md", "docs/third-party-notices.md"]
    names += [p.relative_to(ROOT).as_posix() for p in (ROOT / "docs/licenses").glob("*") if p.is_file()]
    for name in names:
        target = package / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    command = '@echo off\r\nsetlocal\r\nchcp 65001 >nul\r\ncd /d "%~dp0"\r\n"%~dp0runtime\\python.exe" -I "%~dp0scripts\\reviewer_server.py"\r\nif errorlevel 1 pause\r\n'
    (package / "启动CampusOps.cmd").write_bytes(command.encode("utf-8"))
    first_party = {}
    for path in sorted((ROOT / "src/campusops").rglob("*")):
        if path.is_file() and path.suffix in (".py", ".sql", ".json"):
            relative = path.relative_to(ROOT / "src").as_posix()
            assert (site / relative).read_bytes() == path.read_bytes(), relative
            first_party[relative] = digest(path)
    files = {p.relative_to(package).as_posix(): digest(p) for p in sorted(package.rglob("*")) if p.is_file()}
    manifest = {"label": "RC15-Review-1", "version": "0.2.0rc15.post1", "source_commit": head,
                "locked_public_source": "083e7722cbaf6b1f782562a81d4f5b2217661bff", "runtime_base_sha256": BASE_SHA256,
                "wheel_sha256": digest(wheel), "first_party_files": first_party,
                "first_party_fingerprint": sha256("".join(f"{p}:{h}\n" for p,h in sorted(first_party.items())).encode()).hexdigest(),
                "files_excluding_manifest": files, "totp_required": False, "owner_tool_included": False,
                "real_model_default": False, "business_data_included": False}
    (package / "Review_Build_Manifest.json").write_bytes((json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode())
    archive = out / "CampusOps_演示程序_Windows.zip"
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as target:
        for path in sorted(package.rglob("*")):
            if path.is_file():
                target.write(path, path.relative_to(out).as_posix())
    result = {"source_commit": head, "archive": str(archive), "bytes": archive.stat().st_size,
              "sha256": digest(archive), "package": str(package), "first_party_fingerprint": manifest["first_party_fingerprint"]}
    (out / "SHA256SUMS.txt").write_bytes((result["sha256"] + "  " + archive.name + "\n").encode())
    (out / "build-receipt.json").write_bytes((json.dumps(result, indent=2) + "\n").encode())
    (ROOT / "var/reviewer-build-current.json").write_bytes((json.dumps(result, indent=2) + "\n").encode())
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-zip", type=Path, required=True)
    build(parser.parse_args().runtime_zip)
