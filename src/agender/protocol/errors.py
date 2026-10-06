from __future__ import annotations


class ProtocolError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, http_status: int | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.http_status = http_status


class LeaseLost(ProtocolError):
    def __init__(self, message: str = "lease lost"):
        super().__init__("LEASE_LOST", message, retryable=False, http_status=409)
