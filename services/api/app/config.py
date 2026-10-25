"""Read literal dotenv assignments without executing shell code."""
import os
import re
import shlex
import hashlib
from pathlib import Path


def runtime_revision(root):
    root = Path(root)
    digest = hashlib.sha256()
    files = sorted((root / "services/api/app").glob("*.py")) + sorted((root / "services/mcp_reference").glob("*.py")) + [root / "dist/index.html"]
    for file in files:
        if file.is_file():
            digest.update(str(file.relative_to(root)).encode())
            digest.update(file.read_bytes())
    return digest.hexdigest()


def load_env_file(path, override=False):
    path = Path(path)
    if not path.is_file():
        return set()
    values = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        if "=" not in line:
            raise ValueError(f"配置文件第 {number} 行应为 NAME=value")
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"配置文件第 {number} 行的变量名称无效")
        try:
            parts = shlex.split(value, comments=True)
        except ValueError:
            raise ValueError(f"配置文件第 {number} 行引号不匹配") from None
        if len(parts) > 1:
            raise ValueError(f"配置文件第 {number} 行含空格的值需用引号包围")
        values[key] = parts[0] if parts else ""
    for key, value in values.items():
        if override or key not in os.environ:
            os.environ[key] = value
    return set(values)
