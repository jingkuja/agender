import asyncio
import contextlib
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import pytest_asyncio

from agender.browser.runtime import BrowserRuntime
from agender.extractors.web.policy import PolicyDecision


@pytest_asyncio.fixture
async def local_site():
    requests = []
    pages = {"/article": "<html><head><title>Local article</title></head><body><article>Verified locally</article></body></html>"}
    async def handle(reader, writer):
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            path = header.split(b" ")[1].decode()
            requests.append(path)
            if path == "/redirect":
                response = b"HTTP/1.1 302 Found\r\nLocation: /article\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            elif path == "/blocked":
                response = f"HTTP/1.1 302 Found\r\nLocation: http://localhost:{port}/private\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode()
            else:
                body = pages.get(path, "<html><body>missing</body></html>").encode()
                response = f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
            writer.write(response)
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}", pages, requests
    finally:
        server.close()
        await server.wait_closed()


@pytest_asyncio.fixture
async def browser_runtime(cfg, tmp_path):
    # Local test policy explicitly allows only the fixture server. No external site access.
    cfg.root = tmp_path
    cfg.browser.headless = True
    cfg.browser.mode = "playwright"
    cfg.browser.channel = "chromium"
    cfg.browser.slow_mo_ms = 0
    cfg.browser.action_delay_ms = (0, 0)
    cfg.limits.per_host_rpm = {"default": 600000}
    cfg.web.denylist_cidrs = []
    runtime = BrowserRuntime(cfg)
    async def validate(url):
        return PolicyDecision(urlsplit(url).hostname == "127.0.0.1", resolved=["127.0.0.1"])
    runtime.egress.validator = validate
    # Browser binaries are an explicit local prerequisite; CI installs Chromium.
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        executable = Path(pw.chromium.executable_path)
    if not executable.exists():
        if Path("/Applications/Google Chrome.app").exists():
            cfg.browser.channel = "chrome"
        else:
            if os.environ.get("AGENDER_REQUIRE_BROWSER"):
                pytest.fail("Playwright Chromium is required for this run")
            pytest.skip("Playwright Chromium or local Chrome is not installed")
    try:
        await runtime.start("web")
        yield runtime
    finally:
        await runtime.aclose()

