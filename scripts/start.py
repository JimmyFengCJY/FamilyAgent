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
    parser.add_argument("--no-env-file", action="store_true", help="不加载本机 .env，完全使用终端环境")
    args = parser.parse_args()
    os.chdir(ROOT)
    if not shutil.which("node") or not shutil.which("npm"):
        sys.exit("请先安装 Node.js 20.19+ 或 22.12+。")
    python = ROOT / ".venv312/bin/python"
    if not python.exists():
        candidates = [shutil.which("python3.12"), sys.executable]
        interpreter = next((c for c in candidates if c and Path(c).exists()), None)
        version = subprocess.check_output([interpreter, "-c", "import sys; print(sys.version_info >= (3, 11))"], text=True).strip()
        if version != "True":
            sys.exit("当前依赖需要 Python 3.11+，推荐 Python 3.12。")
        run([interpreter, "-m", "venv", ".venv312"])
    if subprocess.check_output([str(python), "-c", "import sys; print(sys.version_info >= (3, 11))"], text=True).strip() != "True":
        sys.exit("当前虚拟环境低于 Python 3.11，请用 Python 3.12 准备项目环境。")
    check = subprocess.run([str(python), "-c", "import fastapi, uvicorn, httpx, mcp"], capture_output=True)
    if check.returncode:
        print("正在准备后端依赖…", flush=True)
        run([str(python), "-m", "pip", "install", "--only-binary=cryptography,cffi,rpds-py,pydantic-core", "-r", "requirements.lock"])
    # Re-exec in the project environment before importing dependencies.
    if Path(sys.executable).parent != python.parent:
        os.execv(str(python), [str(python), str(Path(__file__).resolve()), *sys.argv[1:]])
    sys.path.insert(0, str(ROOT))
    from services.api.app.config import load_env_file, runtime_revision
    from services.api.app.models import ModelGateway
    try:
        if not args.no_env_file:
            loaded = load_env_file(ROOT / ".env", override=True)
            if loaded:
                # An old generic model from a prior terminal must not shadow the explicit file.
                if "FAMILYAGENT_MODEL" not in loaded and any(key in loaded for key in ("ANTHROPIC_MODEL", "DEEPSEEK_MODEL", "GEMINI_MODEL")):
                    os.environ.pop("FAMILYAGENT_MODEL", None)
                print("已加载本机 .env（文件中的值优先），未输出密钥。", flush=True)
        config = ModelGateway().validate_config()
    except ValueError as exc:
        sys.exit(str(exc))
    print(f"模型配置：{config['provider']} / {config['model']}", flush=True)
    if not (ROOT / "node_modules").exists():
        print("正在准备网页依赖…", flush=True)
        run(["npm", "ci", "--no-audit", "--no-fund"])
    if not args.skip_build or not (ROOT / "dist/index.html").exists():
        run(["npm", "run", "build"])
    port = int(os.getenv("FAMILYAGENT_PORT", "8000"))
    url = f"http://127.0.0.1:{port}"
    health = None
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=1) as response:
            health = json.load(response)
    except Exception:
        pass
    if health and health.get("service") == "familyagent":
        if health.get("model_config") != config or health.get("runtime_revision") != runtime_revision(ROOT):
            sys.exit(f"{url} 已有服务，但其代码或模型配置不同或无法核实。请在旧服务终端按 Ctrl+C 停止，再重新启动；本次未复用旧服务。")
        print(f"FamilyAgent 已在运行，模型配置一致：{url}", flush=True)
        if not args.no_open:
            webbrowser.open(url)
        return
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
