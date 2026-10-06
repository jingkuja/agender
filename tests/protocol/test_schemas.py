from agender.protocol.export_schema import MODELS, export_jsonschema
from agender.protocol.redact import strip_secrets
from agender.protocol.schemas import (
    ClaimRequest,
    DouyinVideoParams,
    Heartbeat,
    SessionLoginParams,
    TaskType,
    WorkerStatus,
    parse_params,
)


def test_max_tasks_must_be_one():
    ClaimRequest(worker_id="w", capabilities=["web.search"], max_tasks=1)
    try:
        ClaimRequest.model_validate({"worker_id": "w", "capabilities": [], "max_tasks": 2})
        raise AssertionError
    except Exception:
        pass


def test_busy_heartbeat_requires_lease():
    try:
        Heartbeat(worker_id="w", status=WorkerStatus.BUSY)
        raise AssertionError
    except Exception:
        pass
    Heartbeat(worker_id="w", status=WorkerStatus.BUSY, current_task_id="t", lease_id="l")


def test_strip_password():
    out = strip_secrets({"params": {"password": "secret", "query": "q"}})
    assert "password" not in out["params"]
    assert out["params"]["query"] == "q"


def test_parse_params_video_id():
    p = parse_params(TaskType.DOUYIN_VIDEO_STATS, {"video_id": "7123456789012345678"})
    assert isinstance(p, DouyinVideoParams)


def test_session_login_site():
    SessionLoginParams(site="douyin")
    try:
        SessionLoginParams(site="web")
        raise AssertionError
    except Exception:
        pass


def test_export_jsonschema(tmp_path):
    dest = export_jsonschema(tmp_path)
    for name in MODELS:
        assert (dest / f"{name}.json").exists()
