from __future__ import annotations

import asyncio
import contextlib
from urllib.parse import urlsplit

from agender.extractors.web.policy import PolicyDecision, check_url


class EgressProxy:
    """Loopback-only browser egress with DNS resolution pinned at connect time.

    HTTPS uses CONNECT: certificates and TLS remain end-to-end in Chromium.
    The upstream socket connects to the exact IP vetted by the policy, avoiding
    the check-then-resolve DNS rebinding gap in browser request interception.
    This is internal transport, not a rotating third-party proxy service.
    """

    def __init__(self, cfg, *, validator=None):
        self.cfg = cfg
        self.validator = validator
        self.allowlist: list[str] = []
        self.denylist: list[str] = []
        self.server = None
        self.url: str | None = None
        self._tasks: set[asyncio.Task] = set()

    async def decision(self, url: str) -> PolicyDecision:
        if self.validator:
            return await self.validator(url)
        return await asyncio.to_thread(check_url, url, self.cfg, allowlist=self.allowlist, extra_denylist=self.denylist)

    async def start(self) -> None:
        if self.server:
            return
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0, limit=65536)
        self.url = f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"

    async def close(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.url = None

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        self._tasks.add(task)
        upstream = None
        response_started = False
        try:
            async with asyncio.timeout(15):
                header = await reader.readuntil(b"\r\n\r\n")
                lines = header.decode("iso-8859-1").split("\r\n")
                method, target, version = lines[0].split(" ", 2)
                tunnel = method == "CONNECT"
                url = f"https://{target}/" if tunnel else target
                parsed = urlsplit(url)
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                decision = await self.decision(url)
                if not decision.ok or not decision.resolved or (not tunnel and parsed.scheme != "http"):
                    writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    await writer.drain()
                    return
                # No hostname resolution occurs after this policy decision.
                upstream_reader, upstream = await asyncio.open_connection(decision.resolved[0], port)
                if tunnel:
                    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    await writer.drain()
                    response_started = True
                else:
                    path = parsed.path or "/"
                    if parsed.query:
                        path += "?" + parsed.query
                    kept = [line for line in lines[1:] if line and line.split(":", 1)[0].lower() not in {"proxy-connection", "proxy-authorization", "connection", "host"}]
                    kept.extend([f"Host: {parsed.netloc}", "Connection: close"])
                    upstream.write((f"{method} {path} {version}\r\n" + "\r\n".join(kept) + "\r\n\r\n").encode("iso-8859-1"))
                    await upstream.drain()
                    response_started = True
            async def relay(src, dst):
                while data := await src.read(65536):
                    dst.write(data)
                    await dst.drain()
            pumps = [asyncio.create_task(relay(reader, upstream)), asyncio.create_task(relay(upstream_reader, writer))]
            try:
                await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for pump in pumps:
                    pump.cancel()
                await asyncio.gather(*pumps, return_exceptions=True)
        except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            if not response_started:
                with contextlib.suppress(OSError):
                    writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    await writer.drain()
        finally:
            for stream in (upstream, writer):
                if stream:
                    stream.close()
                    with contextlib.suppress(Exception):
                        await stream.wait_closed()
            self._tasks.discard(task)
