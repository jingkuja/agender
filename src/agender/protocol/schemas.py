from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agender import SCHEMA_VERSION


class TaskType(str, Enum):
    DOUYIN_SEARCH = "douyin.search"
    DOUYIN_VIDEO_STATS = "douyin.video_stats"
    DOUYIN_VIDEO_COMMENTS = "douyin.video_comments"
    DOUYIN_VIDEO_BUNDLE = "douyin.video_bundle"
    SESSION_LOGIN = "session.login"
    WEB_SEARCH = "web.search"
    WEB_FETCH_ARTICLE = "web.fetch_article"
    WEB_HOT_LIST = "web.hot_list"
    WEB_RESEARCH = "web.research"


class ResultStatus(str, Enum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    BLOCKED = "blocked"
    NEEDS_HUMAN = "needs_human"
    CANCELLED = "cancelled"


class CaptureMode(str, Enum):
    NEVER = "never"
    ON_FAILURE = "on_failure"
    ALWAYS = "always"


class WorkerStatus(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    DEGRADED = "degraded"
    STOPPING = "stopping"


class SessionState(str, Enum):
    OK = "ok"
    EXPIRED = "expired"
    MISSING = "missing"
    UNKNOWN = "unknown"


class FetchStatus(str, Enum):
    OK = "ok"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    FAILED = "failed"


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: str = SCHEMA_VERSION
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] | None = None


class Lease(BaseModel):
    model_config = ConfigDict(extra="ignore")
    lease_id: str
    lease_until: str
    heartbeat_s: int | None = 20


class HeartbeatMetrics(BaseModel):
    model_config = ConfigDict(extra="ignore")
    uptime_s: int = 0
    tasks_completed: int = 0
    blocked_rate_1h: float = 0.0


class Heartbeat(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: str = SCHEMA_VERSION
    worker_id: str
    status: WorkerStatus
    current_task_id: str | None = None
    lease_id: str | None = None
    browser_ready: bool = False
    sessions: dict[str, SessionState] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)
    disabled_capabilities: list[str] = Field(default_factory=list)
    metrics: HeartbeatMetrics = Field(default_factory=HeartbeatMetrics)

    @model_validator(mode="after")
    def busy_requires_lease(self) -> Heartbeat:
        if self.status == WorkerStatus.BUSY:
            if not self.current_task_id or not self.lease_id:
                raise ValueError("busy heartbeat requires current_task_id and lease_id")
        return self


class HeartbeatAck(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: str = SCHEMA_VERSION
    ok: bool = True
    server_time: str | None = None
    lease: Lease | None = None
    stop: bool = False


class ClaimRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: str = SCHEMA_VERSION
    worker_id: str
    capabilities: list[str]
    max_tasks: Literal[1] = 1

    @field_validator("max_tasks")
    @classmethod
    def only_one(cls, v: int) -> int:
        if v != 1:
            raise ValueError("max_tasks must be 1")
        return v


class TaskEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: str = SCHEMA_VERSION
    task_id: str
    type: str
    priority: int = 0
    attempt: int = 1
    created_at: str | None = None
    deadline_at: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None

    @field_validator("attempt", mode="before")
    @classmethod
    def default_attempt(cls, v: Any) -> int:
        return 1 if v in (None, 0) else int(v)


class TaskClaim(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: str = SCHEMA_VERSION
    task: TaskEnvelope | None = None
    lease: Lease | None = None


class Progress(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: str = SCHEMA_VERSION
    task_id: str
    lease_id: str
    seq: int
    message: str = ""
    counts: dict[str, int] = Field(default_factory=dict)


class ProgressAck(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: str = SCHEMA_VERSION
    ok: bool = True
    task_id: str
    seq: int


class ArtifactRef(BaseModel):
    model_config = ConfigDict(extra="ignore")
    artifact_id: str
    kind: str
    path: str | None = None
    sha256: str | None = None
    bytes: int | None = None
    content_type: str | None = None
    uploaded: bool = False


class HintsForPlanner(BaseModel):
    model_config = ConfigDict(extra="ignore")
    suggested_followups: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


class TaskResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: str = SCHEMA_VERSION
    task_id: str
    worker_id: str
    attempt: int = 1
    lease_id: str | None = None
    status: ResultStatus
    started_at: str
    finished_at: str
    duration_ms: int = 0
    result: dict[str, Any] | None = None
    error: ErrorBody | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    hints_for_planner: HintsForPlanner = Field(default_factory=HintsForPlanner)


class ResultAck(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: str = SCHEMA_VERSION
    ok: bool = True
    task_id: str
    accepted_status: str
    duplicate: bool = False


class ArtifactMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: str = SCHEMA_VERSION
    task_id: str
    attempt: int = 1
    artifact_id: str
    kind: str
    content_type: str
    sha256: str
    bytes: int


class ArtifactUploadResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: str = SCHEMA_VERSION
    ok: bool = True
    artifact_id: str
    sha256: str
    stored: bool = False


class CommonParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    timeout_s: int | None = None
    capture_screenshot: CaptureMode = CaptureMode.ON_FAILURE
    capture_html: CaptureMode = CaptureMode.NEVER
    upload_artifacts: CaptureMode = CaptureMode.NEVER
    respect_robots: bool = True
    redact_commenters: bool = False
    locale: str = "zh-CN"

    @field_validator("timeout_s")
    @classmethod
    def clamp_timeout(cls, v: int | None) -> int | None:
        if v is None:
            return v
        return max(30, min(600, int(v)))


class DouyinSearchParams(CommonParams):
    query: str
    max_results: int = 12
    sort: str = "general"

    @field_validator("max_results")
    @classmethod
    def clamp_results(cls, v: int) -> int:
        return max(1, min(30, v))


class DouyinVideoParams(CommonParams):
    url: str | None = None
    video_id: str | None = None
    max_comments: int = 20
    include_replies: bool = False
    max_replies_per_comment: int = 5

    @model_validator(mode="after")
    def need_id_or_url(self) -> DouyinVideoParams:
        if not self.url and not self.video_id:
            raise ValueError("url or video_id required")
        return self

    @field_validator("max_comments")
    @classmethod
    def clamp_comments(cls, v: int) -> int:
        return max(1, min(200, v))


class SessionLoginParams(CommonParams):
    site: str = "douyin"
    username: str | None = None
    password: str | None = None
    timeout_s: int | None = 300

    @field_validator("site")
    @classmethod
    def only_douyin(cls, v: str) -> str:
        if v != "douyin":
            raise ValueError("v1 session.login site must be douyin")
        return v


class WebSearchParams(CommonParams):
    query: str
    search_engine: str = "auto"
    max_pages: int = 2
    allowlist: list[str] = Field(default_factory=list)
    denylist: list[str] = Field(default_factory=list)

    @field_validator("max_pages")
    @classmethod
    def clamp_pages(cls, v: int) -> int:
        return max(1, min(5, v))

    @field_validator("search_engine")
    @classmethod
    def engine_ok(cls, v: str) -> str:
        if v not in {"auto", "bing_cn", "baidu"}:
            raise ValueError("search_engine must be auto|bing_cn|baidu")
        return v


class WebFetchParams(CommonParams):
    urls: list[str]
    include_body: bool = True
    max_body_chars: int = 20_000
    allowlist: list[str] = Field(default_factory=list)
    denylist: list[str] = Field(default_factory=list)

    @field_validator("urls")
    @classmethod
    def clamp_urls(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("urls required")
        return v[:20]


class WebHotListParams(CommonParams):
    sources: list[str] = Field(default_factory=lambda: ["baidu_hot"])
    max_items_per_source: int = 20


class WebResearchParams(WebSearchParams):
    max_articles: int = 3
    include_body: bool = True

    @field_validator("max_articles")
    @classmethod
    def clamp_articles(cls, v: int) -> int:
        return max(1, min(8, v))


PARAM_MODELS: dict[TaskType, type[BaseModel]] = {
    TaskType.DOUYIN_SEARCH: DouyinSearchParams,
    TaskType.DOUYIN_VIDEO_STATS: DouyinVideoParams,
    TaskType.DOUYIN_VIDEO_COMMENTS: DouyinVideoParams,
    TaskType.DOUYIN_VIDEO_BUNDLE: DouyinVideoParams,
    TaskType.SESSION_LOGIN: SessionLoginParams,
    TaskType.WEB_SEARCH: WebSearchParams,
    TaskType.WEB_FETCH_ARTICLE: WebFetchParams,
    TaskType.WEB_HOT_LIST: WebHotListParams,
    TaskType.WEB_RESEARCH: WebResearchParams,
}


def parse_params(task_type: TaskType, params: dict[str, Any]) -> BaseModel:
    model = PARAM_MODELS[task_type]
    return model.model_validate(params)


@runtime_checkable
class ProtocolClient(Protocol):
    async def heartbeat(self, body: Heartbeat) -> HeartbeatAck: ...
    async def claim(self, body: ClaimRequest) -> TaskClaim: ...
    async def progress(self, task_id: str, body: Progress) -> ProgressAck: ...
    async def submit_result(self, body: TaskResult) -> ResultAck: ...
    async def upload_artifact(self, meta: ArtifactMeta, blob: bytes) -> ArtifactUploadResponse: ...
    async def aclose(self) -> None: ...
