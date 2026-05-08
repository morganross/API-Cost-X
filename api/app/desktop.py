"""Native desktop launcher for the Windows APICostX build."""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

LOCAL_BIND_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _runtime_home() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "APICostX"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "APICostX"


def _resource_root() -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _resource_path(relative_path: str) -> Path:
    primary = _resource_root() / relative_path
    if primary.exists():
        return primary

    development = Path(__file__).resolve().parents[2] / relative_path
    if development.exists():
        return development

    return primary


def _copy_env_template(env_path: Path) -> None:
    if env_path.exists():
        return

    template = _resource_path(".env.example")
    if template.exists():
        shutil.copy2(template, env_path)
        return

    env_path.write_text(
        "\n".join(
            [
                "# APICostX local self-host configuration",
                "API_COST_X_MODE=local",
                "API_COST_X_HOST=127.0.0.1",
                "API_COST_X_API_PORT=8000",
                "API_COST_X_DATA_DIR=./data",
                "API_COST_X_DATABASE_URL=sqlite+aiosqlite:///./data/api-cost-x.db",
                "API_COST_X_LOGS_DIR=./logs",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _configure_environment(home: Path) -> tuple[str, int]:
    home.mkdir(parents=True, exist_ok=True)
    os.chdir(home)

    data_dir = home / "data"
    logs_dir = home / "logs"
    data_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    env_path = home / ".env"
    _copy_env_template(env_path)
    _load_env_file(env_path)

    os.environ.setdefault("API_COST_X_MODE", "local")
    os.environ.setdefault("API_COST_X_HOST", "127.0.0.1")
    os.environ.setdefault("API_COST_X_API_PORT", "8000")
    os.environ.setdefault("API_COST_X_DATA_DIR", "./data")
    os.environ.setdefault("API_COST_X_DATABASE_URL", "sqlite+aiosqlite:///./data/api-cost-x.db")
    os.environ.setdefault("API_COST_X_LOGS_DIR", "./logs")
    os.environ.setdefault("LOGS_DIR", os.environ["API_COST_X_LOGS_DIR"])
    os.environ.setdefault("API_COST_X_ENV_FILE", str(env_path))

    web_dist = _resource_path("assets/react-build")
    if (web_dist / "index.html").is_file():
        os.environ.setdefault("API_COST_X_WEB_DIST_DIR", str(web_dist))

    host = os.environ.get("API_COST_X_HOST", "127.0.0.1")
    port = int(os.environ.get("API_COST_X_API_PORT", "8000"))
    return host, port


def _enforce_local_bind(host: str) -> None:
    if host in LOCAL_BIND_HOSTS:
        return
    if os.environ.get("API_COST_X_ALLOW_UNSAFE_BIND") == "1":
        return
    raise SystemExit(
        "Refusing to bind APICostX to a non-localhost address. "
        "Leave API_COST_X_HOST as 127.0.0.1 for the Windows desktop build."
    )


def _open_browser_when_ready(base_url: str) -> None:
    health_url = f"{base_url}/api/health"
    for _ in range(80):
        try:
            with urlopen(health_url, timeout=1):
                break
        except (OSError, URLError):
            time.sleep(0.25)
    webbrowser.open(base_url)


def main() -> None:
    host, port = _configure_environment(_runtime_home())
    _enforce_local_bind(host)

    browser_host = "127.0.0.1" if host in LOCAL_BIND_HOSTS else host
    base_url = f"http://{browser_host}:{port}"
    threading.Thread(target=_open_browser_when_ready, args=(base_url,), daemon=True).start()

    import uvicorn

    print(f"APICostX is starting at {base_url}")
    print(f"Local config and SQLite data live in: {Path.cwd()}")
    uvicorn.run("app.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
