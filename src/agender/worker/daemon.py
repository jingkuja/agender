from __future__ import annotations

import asyncio

from agender.config import AppConfig
from agender.obs.logging import configure_logging
from agender.obs.metrics import start_metrics
from agender.worker.loop import WorkerLoop


def run_daemon(cfg: AppConfig) -> None:
    configure_logging(
        json_logs=cfg.observability.log_json,
        level=cfg.observability.log_level,
        log_path=cfg.path(cfg.store.log_path),
    )
    start_metrics(cfg.observability.metrics_addr)
    loop = WorkerLoop(cfg)
    asyncio.run(loop.run_forever())
