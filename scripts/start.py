"""Install missing local dependencies, build the web UI, and serve on loopback."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]


def run(args):
    subprocess.run(args, cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description="启动 FamilyAgent 本地网页版")
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--skip-build", action="store_true", help="复用已有网页构建")
    args = parser.parse_args()
    os.chdir(ROOT)
    if not shutil.which("node") or not shutil.which("npm"):
        sys.exit("请先安装 Node.js 20.19+ 或 22.12+。")
    python = ROOT / ".venv312/bin/python"
    if not python.exists():
        candidates = [shutil.which("python3.12"), "/Users/jimmy/anaconda3/envs/agent/bin/python3.12", sys.executable]
        interpreter = next((c for c in candidates if c and Path(c).exists()), None)
        version = subprocess.check_output([interpreter, "-c", "import sys; print(sys.version_info >= (3, 10))"], text=True).strip()
        if version != "True":
            sys.exit("需要 Python 3.10+，推荐 Python 3.12。")
        run([interpreter, "-m", "venv", ".venv312"])
    check = subprocess.run([str(python), "-c", "import fastapi, uvicorn, httpx"], capture_output=True)
    if check.returncode:
        print("正在准备后端依赖…", flush=True)
        run([str(python), "-m", "pip", "install", "-r", "requirements.lock"])
    if not (ROOT / "node_modules").exists():
        print("正在准备网页依赖…", flush=True)
        run(["npm", "ci", "--no-audit", "--no-fund"])
    if not args.skip_build or not (ROOT / "dist/index.html").exists():
        run(["npm", "run", "build"])
    port = int(os.getenv("FAMILYAGENT_PORT", "8000"))
    url = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=1) as response:
            health = json.load(response)
        if health.get("service") == "familyagent":
            print(f"FamilyAgent 已在运行：{url}", flush=True)
            if not args.no_open:
                webbrowser.open(url)
            return
    except Exception:
        pass
    def open_when_ready():
        for _ in range(60):
            try:
                with urllib.request.urlopen(url + "/api/health", timeout=1) as response:
                    if json.load(response).get("service") == "familyagent":
                        print(f"\n打开浏览器：{url}\n按 Ctrl+C 停止。", flush=True)
                        if not args.no_open:
                            webbrowser.open(url)
                        return
            except Exception:
                time.sleep(0.3)
    threading.Thread(target=open_when_ready, daemon=True).start()
    try:
        run([str(python), "-m", "uvicorn", "services.api.app.main:app", "--host", "127.0.0.1", "--port", str(port)])
    except KeyboardInterrupt:
        print("\n已停止。记录保存在 data/，下次启动可以继续。")


if __name__ == "__main__":
    main()
