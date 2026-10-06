"""CDP lifecycle verified against a temporary Chrome, never the user's browser."""
import asyncio
import subprocess
from pathlib import Path

import pytest

from agender.browser.runtime import BrowserRuntime
from agender.extractors.web.policy import PolicyDenied


async def test_real_cdp_keeps_existing_tab_and_browser(cfg, tmp_path, local_site):
    from playwright.async_api import async_playwright

    base, _, requests = local_site
    cfg.browser.mode = "cdp"
    cfg.browser.slow_mo_ms = 0
    cfg.web.denylist_cidrs = []
    cfg.limits.per_host_rpm = {"default": 600000}
    profile = tmp_path / "cdp-profile"
    process = None
    runtime = BrowserRuntime(cfg)
    async with async_playwright() as pw:
        executable = Path(pw.chromium.executable_path)
        if not executable.exists():
            executable = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        if not executable.exists():
            pytest.skip("local Chrome/Chromium not installed")
        try:
            process = subprocess.Popen([str(executable), "--headless=new", "--no-sandbox", "--no-first-run", "--no-default-browser-check",
                                        "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0", f"--user-data-dir={profile}", "about:blank"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            port_file = profile / "DevToolsActivePort"
            for _ in range(100):
                if port_file.exists() and port_file.read_text().strip():
                    break
                assert process.poll() is None, "temporary Chrome exited"
                await asyncio.sleep(0.1)
            assert port_file.exists(), "temporary Chrome did not expose CDP"
            port = port_file.read_text().splitlines()[0]
            cfg.browser.cdp_endpoint_url = f"http://127.0.0.1:{port}"
            observer = await pw.chromium.connect_over_cdp(cfg.browser.cdp_endpoint_url)
            context = observer.contexts[0]
            user_page = context.pages[0]
            await user_page.goto("data:text/html,<title>Existing tab</title><p>Keep me</p>")
            before = len(context.pages)
            await runtime.start("web")
            assert len(context.pages) == before + 1
            snapshot = await runtime.navigator.open(base + "/redirect", "web")
            assert snapshot.final_url == base + "/article"
            with pytest.raises(PolicyDenied):
                await runtime.navigator.open(base + "/blocked", "web")
            assert "/private" not in requests
            await runtime.start("xiaohongshu")
            assert len(context.pages) == before + 1
            assert await user_page.title() == "Existing tab"
            await runtime.aclose()
            assert observer.is_connected() and process.poll() is None
            assert len(context.pages) == before
            assert await user_page.title() == "Existing tab"
        finally:
            await runtime.aclose()
            if process and process.poll() is None:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait)
