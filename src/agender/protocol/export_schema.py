from __future__ import annotations

import json
from pathlib import Path

from agender.protocol.results import RESULT_ADAPTER
from agender.protocol.schemas import (
    PARAM_MODELS,
    ArtifactMeta,
    ArtifactUploadResponse,
    ClaimRequest,
    ErrorBody,
    Heartbeat,
    HeartbeatAck,
    Progress,
    ProgressAck,
    ResultAck,
    TaskClaim,
    TaskResult,
)

MODELS = {
    "Heartbeat": Heartbeat,
    "HeartbeatAck": HeartbeatAck,
    "ClaimRequest": ClaimRequest,
    "TaskClaim": TaskClaim,
    "Progress": Progress,
    "ProgressAck": ProgressAck,
    "TaskResult": TaskResult,
    "ResultAck": ResultAck,
    "ArtifactMeta": ArtifactMeta,
    "ArtifactUploadResponse": ArtifactUploadResponse,
    "ErrorBody": ErrorBody,
}


def export_jsonschema(dest: Path | None = None) -> Path:
    dest = dest or Path(__file__).parent / "jsonschema"
    dest.mkdir(parents=True, exist_ok=True)
    models = {**MODELS, **{model.__name__: model for model in PARAM_MODELS.values()}}
    for name, model in models.items():
        path = dest / f"{name}.json"
        path.write_text(json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (dest / "ResultPayload.json").write_text(json.dumps(RESULT_ADAPTER.json_schema(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return dest


if __name__ == "__main__":
    print(export_jsonschema())
