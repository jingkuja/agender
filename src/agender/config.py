from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

ROOT = Path(__file__).resolve().parents[2]


class Viewport(BaseModel):
    width: int = 1440
    height: int = 900


class FileClientConfig(BaseModel):
    inbox_dir: str = "data/inbox/tasks"
    outbox_dir: str = "data/outbox"


class ServerConfig(BaseModel):
    base_url: str = ""
    timeout_s: int = 30


class WorkerConfig(BaseModel):
    id: str = ""
    poll_interval_s: float = 15
    poll_jitter_s: float = 5
    heartbeat_interval_s: float = 20
    task_wall_clock_s: int = 180
    task_hard_cap_s: int = 600
    shutdown_grace_s: float = Field(default=5, ge=0.1, le=30)
    submit_retry_s: float = Field(default=2, ge=0.01, le=60)
    file_client: FileClientConfig = Field(default_factory=FileClientConfig)


class ToolsConfig(BaseModel):
    web: bool = True
    douyin: bool = False
    xiaohongshu: bool = False
    weibo: bool = False
    bilibili: bool = False


class SessionBackupConfig(BaseModel):
    douyin: str = "data/sessions/douyin.storage_state.json"
    xiaohongshu: str = "data/sessions/xiaohongshu.storage_state.json"
    weibo: str = "data/sessions/weibo.storage_state.json"
    bilibili: str = "data/sessions/bilibili.storage_state.json"


class BrowserConfig(BaseModel):
    mode: Literal["cdp", "playwright"] = "cdp"
    cdp_endpoint_url: str = "http://127.0.0.1:9222"
    cdp_timeout_ms: int = Field(default=10_000, ge=100, le=120_000)
    channel: str = "chrome"
    headless: str | bool = "auto"
    locale: str = "zh-CN"
    timezone_id: str = "Asia/Shanghai"
    viewport: Viewport = Field(default_factory=Viewport)
    slow_mo_ms: int = 50
    navigation_timeout_ms: int = 30_000
    action_delay_ms: tuple[int, int] = (800, 2500)
    profile_dir: str = "data/profiles"
    session_backup: SessionBackupConfig = Field(default_factory=SessionBackupConfig)

    @field_validator("cdp_endpoint_url")
    @classmethod
    def valid_cdp_endpoint(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.hostname:
            raise ValueError("cdp_endpoint_url must be an HTTP(S) or WS(S) URL")
        return value


class LimitsConfig(BaseModel):
    douyin_comments_default: int = 20
    douyin_comments_max: int = 200
    douyin_bundle_wall_clock_s: int = 240
    session_login_wall_clock_s: int = 300
    web_result_pages_default: int = 2
    web_article_bodies_default: int = 3
    web_urls_max: int = 20
    per_host_rpm: dict[str, int] = Field(
        default_factory=lambda: {"www.douyin.com": 6, "default": 10}
    )


class DouyinConfig(BaseModel):
    tos_ack: bool = False
    creator_enabled: bool = False
    max_scroll_rounds: int = Field(default=30, ge=1, le=100)
    stall_rounds: int = Field(default=3, ge=1, le=10)
    load_wait_s: float = Field(default=8, ge=0.01, le=30)
    base_url: str = "https://www.douyin.com"
    login_url: str = "https://www.douyin.com/"
    selector_pack_version: str = "2026-10-06"
    redirect_allowlist: list[str] = Field(
        default_factory=lambda: ["v.douyin.com", "www.douyin.com", "www.iesdouyin.com"]
    )
    session_cookie_names: list[str] = Field(
        default_factory=lambda: ["sessionid", "sid_tt", "uid_tt", "ttwid"]
    )


class SocialConfig(BaseModel):
    tos_ack: bool = False
    creator_enabled: bool = True
    max_scroll_rounds: int = Field(default=30, ge=1, le=100)
    stall_rounds: int = Field(default=3, ge=1, le=10)
    load_wait_s: float = Field(default=2, ge=0.01, le=30)
    selector_pack_version: str = "2026-10-06"
    base_url: str
    login_url: str
    redirect_allowlist: list[str]
    session_cookie_names: list[str]


class XiaohongshuConfig(SocialConfig):
    base_url: str = "https://www.xiaohongshu.com"
    login_url: str = "https://www.xiaohongshu.com/explore"
    redirect_allowlist: list[str] = Field(default_factory=lambda: ["www.xiaohongshu.com", "xiaohongshu.com", "xhslink.com", "www.xhslink.com"])
    session_cookie_names: list[str] = Field(default_factory=lambda: ["web_session"])


class WeiboConfig(SocialConfig):
    base_url: str = "https://weibo.com"
    login_url: str = "https://weibo.com/"
    redirect_allowlist: list[str] = Field(default_factory=lambda: ["weibo.com", "www.weibo.com", "s.weibo.com", "m.weibo.cn", "passport.weibo.com", "passport.weibo.cn", "login.sina.com.cn"])
    session_cookie_names: list[str] = Field(default_factory=lambda: ["SUB"])


class BilibiliConfig(SocialConfig):
    base_url: str = "https://www.bilibili.com"
    login_url: str = "https://www.bilibili.com/"
    redirect_allowlist: list[str] = Field(default_factory=lambda: ["www.bilibili.com", "bilibili.com", "search.bilibili.com", "space.bilibili.com", "passport.bilibili.com", "b23.tv"])
    session_cookie_names: list[str] = Field(default_factory=lambda: ["SESSDATA"])


class HotListItem(BaseModel):
    enabled: bool = False
    url: str = ""


class WebConfig(BaseModel):
    respect_robots: bool = True
    default_search_engine: str = "auto"
    max_body_chars: int = 20_000
    denylist_cidrs: list[str] = Field(
        default_factory=lambda: [
            "127.0.0.0/8",
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
            "169.254.0.0/16",
            "100.64.0.0/10",
            "::1/128",
            "fe80::/10",
            "fc00::/7",
        ]
    )
    denylist_hosts: list[str] = Field(
        default_factory=lambda: ["localhost", "metadata.google.internal"]
    )
    block_gov_cn: bool = True
    hot_lists: dict[str, HotListItem] = Field(default_factory=dict)


class StoreConfig(BaseModel):
    sqlite_path: str = "data/agender.db"
    artifact_dir: str = "data/artifacts"
    log_path: str = "data/logs/agender.jsonl"
    artifact_retention_days: int = 7
    run_retention_days: int = 30
    log_retention_days: int = 14
    content_enabled: bool = False
    content_path: str = "data/content.db"
    checkpoint_ttl_s: int = Field(default=86400, ge=60)


class HealthConfig(BaseModel):
    failure_threshold: int = Field(default=5, ge=1)
    cooldown_s: float = Field(default=1800, ge=1)


class ObservabilityConfig(BaseModel):
    log_level: str = "INFO"
    log_json: bool = True
    metrics_addr: str = "127.0.0.1:9108"

    @field_validator("metrics_addr")
    @classmethod
    def loopback_only(cls, v: str) -> str:
        host = v.rsplit(":", 1)[0]
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("metrics_addr must be loopback (127.0.0.1:9108)")
        return v


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    worker: WorkerConfig = Field(default_factory=WorkerConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    douyin: DouyinConfig = Field(default_factory=DouyinConfig)
    xiaohongshu: XiaohongshuConfig = Field(default_factory=XiaohongshuConfig)
    weibo: WeiboConfig = Field(default_factory=WeiboConfig)
    bilibili: BilibiliConfig = Field(default_factory=BilibiliConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    store: StoreConfig = Field(default_factory=StoreConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    health: HealthConfig = Field(default_factory=HealthConfig)
    token: str = ""
    root: Path = Field(default_factory=lambda: ROOT)

    @model_validator(mode="after")
    def tools_tos(self) -> AppConfig:
        from agender.platforms import SESSION_SITES
        for site in SESSION_SITES:
            if getattr(self.tools, site) and not getattr(self, site).tos_ack:
                raise ValueError(f"tools.{site}=true requires {site}.tos_ack=true")
        if not self.worker.id:
            self.worker.id = socket.gethostname()
        return self

    def path(self, rel: str) -> Path:
        p = Path(rel)
        if p.is_absolute():
            return p
        return self.root / p

    @property
    def uses_http(self) -> bool:
        return bool(self.server.base_url.strip())


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path | None = None) -> AppConfig:
    from dotenv import load_dotenv

    load_dotenv()
    cfg_path = Path(path or os.environ.get("AGENDER_CONFIG") or ROOT / "config" / "config.example.yaml")
    if not cfg_path.is_absolute():
        cfg_path = (Path.cwd() / cfg_path).resolve()
    if not cfg_path.exists():
        cfg_path = ROOT / "config" / "config.example.yaml"
    raw: dict[str, Any] = {}
    if cfg_path.exists():
        loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"config is not a mapping: {cfg_path}")
        raw = loaded
    env_overlay: dict[str, Any] = {}
    if os.environ.get("AGENDER_SERVER_BASE_URL") is not None:
        env_overlay.setdefault("server", {})["base_url"] = os.environ["AGENDER_SERVER_BASE_URL"]
    if os.environ.get("AGENDER_WORKER_ID"):
        env_overlay.setdefault("worker", {})["id"] = os.environ["AGENDER_WORKER_ID"]
    for env, key in (("AGENDER_BROWSER_MODE", "mode"), ("AGENDER_CDP_ENDPOINT_URL", "cdp_endpoint_url"),
                     ("AGENDER_CDP_TIMEOUT_MS", "cdp_timeout_ms"), ("AGENDER_BROWSER_HEADLESS", "headless")):
        if env in os.environ:
            value = os.environ[env]
            if key == "headless" and value.lower() in {"true", "false"}:
                value = value.lower() == "true"
            env_overlay.setdefault("browser", {})[key] = value
    data = _deep_merge(raw, env_overlay)
    root = cfg_path.parent.parent if cfg_path.parent.name == "config" else Path.cwd()
    cfg = AppConfig.model_validate({**data, "root": root})
    cfg.token = os.environ.get("AGENDER_WORKER_TOKEN") or ""
    token_file = os.environ.get("AGENDER_TOKEN_FILE")
    if not cfg.token and token_file:
        cfg.token = Path(token_file).read_text(encoding="utf-8").strip()
    return cfg
