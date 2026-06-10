from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable


DOUYIN_DOMAINS = ("douyin.com", "iesdouyin.com", "amemv.com")


def load_cookies_from_storage_state(
    storage_state_path: str | Path = "runtime/douyin_storage_state.json",
    domains: Iterable[str] = DOUYIN_DOMAINS,
) -> Dict[str, str]:
    path = Path(storage_state_path)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    allowed = tuple(domains)
    cookies: Dict[str, str] = {}
    for cookie in data.get("cookies") or []:
        domain = str(cookie.get("domain") or "")
        name = str(cookie.get("name") or "").strip()
        value = str(cookie.get("value") or "")
        if not name:
            continue
        if allowed and not any(domain.endswith(item) for item in allowed):
            continue
        cookies[name] = value
    return cookies


async def save_douyin_login_state(
    storage_state_path: str | Path = "runtime/douyin_storage_state.json",
    headless: bool = False,
    wait_seconds: int = 0,
) -> str:
    from playwright.async_api import async_playwright

    target = Path(storage_state_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=headless)
        context = await browser.new_context(locale="zh-CN")
        page = await context.new_page()
        await page.goto("https://www.douyin.com", wait_until="domcontentloaded", timeout=60000)
        if wait_seconds > 0:
            print(f"请在打开的浏览器里登录抖音。程序会在 {wait_seconds} 秒后自动保存 Cookie。")
            await page.wait_for_timeout(wait_seconds * 1000)
        else:
            print("请在打开的浏览器里登录抖音。登录完成后回到终端按 Enter。")
            input()
        await context.storage_state(path=str(target))
        await browser.close()
    return str(target)
