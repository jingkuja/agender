from __future__ import annotations

from typing import Any

SECRET_KEYS = frozenset({"password", "passwd", "token", "worker_token", "authorization"})


def strip_secrets(value: Any) -> Any:
    """Return a copy with secret keys removed. Used before disk/log/result."""
    if isinstance(value, dict):
        return {k: strip_secrets(v) for k, v in value.items() if k.lower() not in SECRET_KEYS}
    if isinstance(value, list):
        return [strip_secrets(v) for v in value]
    return value


def pop_password(params: dict[str, Any] | None) -> str | None:
    if not params:
        return None
    return params.pop("password", None)
