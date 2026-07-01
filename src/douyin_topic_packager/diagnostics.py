from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Mapping, Sequence


def run_diagnostics(
    state_path: str | Path = "runtime/douyin_storage_state.json",
    env: Mapping[str, str] | None = None,
    python_version: Sequence[int] | None = None,
    playwright_available: bool | None = None,
) -> list[dict[str, str]]:
    source = env if env is not None else __import__("os").environ
    version = tuple(python_version or sys.version_info[:3])
    state = Path(state_path)
    if playwright_available is None:
        playwright_available = importlib.util.find_spec("playwright") is not None

    checks: list[dict[str, str]] = []
    checks.append(
        {
            "name": "python",
            "label": "Python",
            "status": "ok" if version >= (3, 10, 0) else "error",
            "message": ".".join(str(part) for part in version[:3]),
        }
    )
    checks.append(
        {
            "name": "playwright",
            "label": "Playwright",
            "status": "ok" if playwright_available else "error",
            "message": "已安装" if playwright_available else "未安装，请运行 python -m playwright install chromium",
        }
    )
    checks.append(
        {
            "name": "cookie",
            "label": "Cookie",
            "status": "ok" if state.exists() else "warn",
            "message": str(state) if state.exists() else f"未找到 {state}，首次使用请先运行 login",
        }
    )
    llm_ready = all((source.get("LLM_PROVIDER"), source.get("LLM_MODEL"), source.get("LLM_API_KEY")))
    checks.append(
        {
            "name": "llm",
            "label": "LLM",
            "status": "ok" if llm_ready else "warn",
            "message": "已配置" if llm_ready else "未完整配置；只有使用 --llm 时才需要",
        }
    )
    return checks


def diagnostics_has_errors(checks: list[dict[str, str]]) -> bool:
    return any(item.get("status") == "error" for item in checks)


def format_diagnostics(checks: list[dict[str, str]]) -> str:
    status_label = {"ok": "OK", "warn": "WARN", "error": "ERROR"}
    lines = ["环境自检："]
    for item in checks:
        status = status_label.get(item.get("status", ""), item.get("status", "").upper())
        lines.append(f"- [{status}] {item.get('label')}: {item.get('message')}")
    return "\n".join(lines)
