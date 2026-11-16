"""Launch the review package on loopback; no .env, Owner tool or TOTP access."""
import argparse
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from urllib.request import build_opener, ProxyHandler

ROOT = Path(__file__).absolute().parents[1]
if (ROOT / "src/campusops/reviewer.py").is_file():
    sys.path.insert(0, str(ROOT / "src"))


def open_when_ready(port):
    opener = build_opener(ProxyHandler({}))
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            with opener.open(url + "/_stcore/health", timeout=1) as response:
                if response.status == 200:
                    for edge in (Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
                                 Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe")):
                        if edge.is_file():
                            subprocess.Popen([str(edge), "--app=" + url])
                            return
                    import webbrowser
                    webbrowser.open(url)
                    return
        except OSError:
            time.sleep(0.25)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    if args.port != 0 and not 1024 <= args.port <= 65535:
        parser.error("port must be 0 or between 1024 and 65535")
    from campusops.reviewer import get_context, initialize_data, configure_environment
    context = get_context()
    initialize_data(context)
    configure_environment(context)
    sys.dont_write_bytecode = True
    sys.pycache_prefix = str(context.directory / "cache/python")
    if args.port:
        port = args.port
    else:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(port,), daemon=True).start()
    import os
    os.chdir(context.root)
    from streamlit.web.cli import main as streamlit_main
    sys.argv = ["streamlit", "run", str(context.root / "streamlit_app.py"),
                "--server.address", "127.0.0.1", "--server.port", str(port),
                "--server.headless", "true", "--server.fileWatcherType", "none",
                "--server.enableCORS", "true", "--server.enableXsrfProtection", "true",
                "--browser.gatherUsageStats", "false", "--client.toolbarMode", "minimal"]
    print(f"CampusOps RC15-Review-1: http://127.0.0.1:{port}", flush=True)
    return streamlit_main() or 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("评审服务未能启动。请确认已完整解压到D盘的新目录、文件未被系统策略阻止，且端口可用。", file=sys.stderr)
        raise SystemExit(2) from None
