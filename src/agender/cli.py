from __future__ import annotations

from pathlib import Path

import typer

from agender import __version__
from agender.config import load_config

app = typer.Typer(name="agender-worker", no_args_is_help=True, help="Agender browsing worker")
session_app = typer.Typer(no_args_is_help=True, help="Session import/export/login")
app.add_typer(session_app, name="session")


def _cfg(config: Path | None) -> object:
    return load_config(config)


@app.callback()
def _root(
    ctx: typer.Context,
    config: Path | None = typer.Option(None, "--config", "-c", help="YAML config path"),
) -> None:
    ctx.obj = {"config": config}


@app.command()
def worker(ctx: typer.Context) -> None:
    """Run the unattended daemon."""
    from agender.worker.daemon import run_daemon

    run_daemon(_cfg(ctx.obj["config"]))


@app.command()
def run(
    ctx: typer.Context,
    file: Path = typer.Option(..., "--file", exists=True, help="Task JSON"),
) -> None:
    """Execute one task JSON without claiming."""
    import asyncio

    from agender.worker.loop import run_one_file

    cfg = _cfg(ctx.obj["config"])
    result = asyncio.run(run_one_file(cfg, file))
    typer.echo(result.model_dump_json(indent=2))


@app.command()
def doctor(ctx: typer.Context) -> None:
    """Check local runtime."""
    import sys

    from agender.browser.lock import FileLock, LockHeld

    cfg = _cfg(ctx.obj["config"])
    errors: list[str] = []
    if sys.version_info < (3, 12):
        errors.append("Python >= 3.12 required")
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except Exception as exc:
        errors.append(f"playwright import failed: {exc}")
    for p in (
        cfg.path(cfg.browser.profile_dir),
        cfg.path(cfg.store.sqlite_path).parent,
        cfg.path(cfg.worker.file_client.inbox_dir),
    ):
        p.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            errors.append(f"not writable: {p}")
    try:
        lk = FileLock(cfg.path(cfg.store.sqlite_path + ".lock"))
        lk.acquire()
        lk.release()
    except LockHeld as exc:
        errors.append(str(exc))
    host = cfg.observability.metrics_addr.rsplit(":", 1)[0]
    if host not in {"127.0.0.1", "localhost", "::1"}:
        errors.append("metrics_addr must be loopback")
    if not cfg.uses_http:
        typer.echo("FileClient mode")
    if errors:
        for e in errors:
            typer.echo(f"FAIL: {e}")
        raise typer.Exit(1)
    typer.echo("ok")


@app.command()
def gc(ctx: typer.Context) -> None:
    """Garbage-collect artifacts, logs, and old rows."""
    import asyncio

    from agender.store.db import Store
    from agender.store.retention import gc as run_gc

    cfg = _cfg(ctx.obj["config"])

    async def _go() -> None:
        store = Store(cfg.path(cfg.store.sqlite_path))
        await store.open()
        try:
            stats = await run_gc(
                store,
                artifact_dir=cfg.path(cfg.store.artifact_dir),
                log_path=cfg.path(cfg.store.log_path),
                artifact_days=cfg.store.artifact_retention_days,
                run_days=cfg.store.run_retention_days,
                log_days=cfg.store.log_retention_days,
            )
            typer.echo(stats)
        finally:
            await store.close()

    asyncio.run(_go())


@session_app.command("login")
def session_login(
    ctx: typer.Context,
    site: str = typer.Option("douyin", "--site"),
    username: str | None = typer.Option(None, "--username"),
) -> None:
    import asyncio

    from agender.browser.login import run_session_login
    from agender.browser.runtime import BrowserRuntime
    from agender.protocol.schemas import SessionLoginParams

    cfg = _cfg(ctx.obj["config"])
    if site != "douyin":
        raise typer.BadParameter("v1 only douyin")

    async def _go() -> None:
        rt = BrowserRuntime(cfg)
        try:
            result = await run_session_login(
                rt,
                SessionLoginParams(site="douyin", username=username),
                confirm_cli=True,
                timeout_s=cfg.limits.session_login_wall_clock_s,
            )
            if result.get("needs_cli_confirm"):
                typer.echo("Press Enter to confirm login heuristic...")
                input()
                backup = rt.backup_path("douyin")
                if backup:
                    await rt.export_state(backup)
            typer.echo(result)
        finally:
            await rt.aclose()

    asyncio.run(_go())


@session_app.command("import")
def session_import(
    ctx: typer.Context,
    site: str = typer.Option("douyin", "--site"),
    file: Path = typer.Option(..., "--file", exists=True),
) -> None:
    import asyncio

    from agender.browser.runtime import BrowserRuntime

    cfg = _cfg(ctx.obj["config"])

    async def _go() -> None:
        rt = BrowserRuntime(cfg)
        try:
            await rt.import_state(site, file)  # type: ignore[arg-type]
            typer.echo("imported")
        finally:
            await rt.aclose()

    asyncio.run(_go())


@session_app.command("export")
def session_export(
    ctx: typer.Context,
    site: str = typer.Option("douyin", "--site"),
    file: Path = typer.Option(..., "--file"),
) -> None:
    import asyncio

    from agender.browser.runtime import BrowserRuntime

    cfg = _cfg(ctx.obj["config"])

    async def _go() -> None:
        rt = BrowserRuntime(cfg)
        try:
            await rt.start(site)  # type: ignore[arg-type]
            await rt.export_state(file)
            typer.echo(f"wrote {file}")
        finally:
            await rt.aclose()

    asyncio.run(_go())


@app.command()
def version() -> None:
    typer.echo(__version__)
