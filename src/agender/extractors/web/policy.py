from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from agender.config import AppConfig

MAX_HOPS = 5


class PolicyDenied(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class PolicyDecision:
    ok: bool
    code: str | None = None
    message: str = ""
    resolved: list[str] | None = None


def _host_denied(host: str, names: list[str]) -> bool:
    h = host.lower().rstrip(".")
    for n in names:
        n = n.lower()
        if n.startswith("*.") and (h.endswith(n[1:]) or h == n[2:]):
            return True
        if h == n or h.endswith("." + n):
            if n in {"internal", "localhost"} or n.startswith("*"):
                return True
            if h == n:
                return True
    if h == "localhost" or h.endswith(".localhost") or h.endswith(".internal"):
        return True
    return False


def _gov_blocked(host: str, block: bool, allowlist: list[str]) -> bool:
    if not block:
        return False
    h = host.lower()
    if any(a.lower() == h for a in allowlist):
        return False
    return h == "gov.cn" or h.endswith(".gov.cn")


def _ip_denied(ip: str, cidrs: list[str]) -> bool:
    addr = ipaddress.ip_address(ip)
    mapped = addr.ipv4_mapped if isinstance(addr, ipaddress.IPv6Address) else None
    for c in cidrs:
        net = ipaddress.ip_network(c, strict=False)
        if addr.version == net.version and addr in net:
            return True
        if mapped is not None and net.version == 4 and mapped in net:
            return True
    return False


def resolve_host(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None)
    out: list[str] = []
    for info in infos:
        ip = info[4][0]
        if ip not in out:
            out.append(ip)
    return out


def check_url(
    url: str,
    cfg: AppConfig,
    *,
    allowlist: list[str] | None = None,
    extra_denylist: list[str] | None = None,
    resolve: bool = True,
) -> PolicyDecision:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return PolicyDecision(False, "POLICY_DENIED", f"scheme {parsed.scheme}")
    host = parsed.hostname
    if parsed.username or parsed.password:
        return PolicyDecision(False, "POLICY_DENIED", "URL credentials are not allowed")
    if not host:
        return PolicyDecision(False, "POLICY_DENIED", "missing host")
    allow = allowlist or []
    deny_hosts = list(cfg.web.denylist_hosts) + list(extra_denylist or [])
    if _host_denied(host, deny_hosts):
        return PolicyDecision(False, "POLICY_DENIED", f"host denied {host}")
    if _gov_blocked(host, cfg.web.block_gov_cn, allow):
        return PolicyDecision(False, "POLICY_DENIED", f"gov.cn blocked {host}")
    ips: list[str] = []
    if resolve:
        try:
            ips = resolve_host(host)
        except socket.gaierror as exc:
            return PolicyDecision(False, "POLICY_DENIED", f"dns failed {host}: {exc}")
        for ip in ips:
            if _ip_denied(ip, cfg.web.denylist_cidrs):
                return PolicyDecision(False, "POLICY_DENIED", f"cidr denied {ip}")
    return PolicyDecision(True, resolved=ips)


def check_redirect_chain(
    start_url: str,
    hops: list[str],
    cfg: AppConfig,
    **kwargs: object,
) -> PolicyDecision:
    seen = [start_url, *hops]
    if len(seen) > MAX_HOPS + 1:
        return PolicyDecision(False, "POLICY_DENIED", "too many redirects")
    for u in seen:
        d = check_url(str(u), cfg, **kwargs)  # type: ignore[arg-type]
        if not d.ok:
            return d
    return PolicyDecision(True)


_robots_cache: dict[str, tuple[float, object]] = {}
ROBOTS_TTL = 6 * 3600


def robots_allowed(url: str, *, fetch, respect: bool) -> tuple[bool, bool]:
    """Return (allowed, would_block). fetch(url)->str."""
    import time
    from urllib.robotparser import RobotFileParser

    parsed = urlparse(url)
    robots_url = urljoin(f"{parsed.scheme}://{parsed.netloc}", "/robots.txt")
    now = time.time()
    cached = _robots_cache.get(robots_url)
    rp: RobotFileParser | None
    if cached and now - cached[0] < ROBOTS_TTL:
        rp = cached[1]  # type: ignore[assignment]
    else:
        try:
            body = fetch(robots_url) or ""
            rp = RobotFileParser()
            rp.parse(body.splitlines())
        except Exception:
            rp = None
        _robots_cache[robots_url] = (now, rp)
    if rp is None:
        return True, False
    allowed = rp.can_fetch("AgenderWorker/0.1", url)
    if allowed:
        return True, False
    if respect:
        return False, True
    return True, True


def clear_robots_cache() -> None:
    _robots_cache.clear()


async def robots_allowed_async(url: str, *, fetch, respect: bool) -> tuple[bool, bool]:
    import asyncio
    import inspect
    import time
    from urllib.robotparser import RobotFileParser

    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    cached = _robots_cache.get(robots_url)
    now = time.time()
    if cached and now - cached[0] < ROBOTS_TTL:
        rp = cached[1]
    else:
        # A synchronous injected fetcher is isolated from the heartbeat loop too.
        if inspect.iscoroutinefunction(fetch):
            body = await fetch(robots_url)
        else:
            body = await asyncio.to_thread(fetch, robots_url)
            if inspect.isawaitable(body):
                body = await body
        rp = RobotFileParser()
        rp.parse((body or "").splitlines())
        _robots_cache[robots_url] = (now, rp)
    allowed = rp is None or rp.can_fetch("AgenderWorker/0.1", url)
    return (allowed or not respect), not allowed
