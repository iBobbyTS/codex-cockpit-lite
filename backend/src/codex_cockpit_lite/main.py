"""Codex Cockpit Lite — main entry point."""

from __future__ import annotations

import argparse
import asyncio
import hmac
import logging
import secrets
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .api import router as api_router
from .api import set_api_config_dir
from .api_key_auth import has_valid_api_key, requires_api_key
from .config import ensure_config_dir, get_config_dir, load_config, save_config
from .status import router as status_router
from .status import set_actual_port, set_bind_host, set_config_dir

logger = logging.getLogger(__name__)

_config_dir: Path = get_config_dir()
_actual_port: int = 0
_shutdown_token = secrets.token_urlsafe(32)
_CONTROL_HEADER = "X-Codex-Cockpit-Control"


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Initialize shared state and stop the refresh worker cleanly."""
    ensure_config_dir(_config_dir)
    set_config_dir(_config_dir)
    set_api_config_dir(_config_dir)
    logger.info("Codex Cockpit Lite started, config dir: %s", _config_dir)

    refresh_task = asyncio.create_task(_periodic_quota_refresh())
    application.state.quota_refresh_task = refresh_task
    try:
        yield
    finally:
        refresh_task.cancel()
        with suppress(asyncio.CancelledError):
            await refresh_task


app = FastAPI(title="Codex Cockpit Lite", version="0.1.0", lifespan=lifespan)


class CockpitServer(uvicorn.Server):
    """Announce readiness only after Uvicorn has created its listener."""

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        if self.started and _actual_port > 0:
            print(f"CONTROL={_shutdown_token}", flush=True)
            print(f"PORT={_actual_port}", flush=True)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def authenticate_served_api(request: Request, call_next):
    """Allow service traffic only with the configured API key."""
    if requires_api_key(request):
        configured_api_key = load_config(_config_dir).api.api_key
        if not has_valid_api_key(request, configured_api_key):
            return JSONResponse(
                {"error": {"message": "Unauthorized"}},
                status_code=401,
            )
    return await call_next(request)


app.include_router(status_router)
app.include_router(api_router)


@app.post("/api/cockpit/shutdown", include_in_schema=False)
async def shutdown_backend(request: Request) -> Response:
    """Stop this backend only when called by the parent Tauri process."""
    supplied_token = request.headers.get(_CONTROL_HEADER)
    if supplied_token is None or not hmac.compare_digest(supplied_token, _shutdown_token):
        return Response(status_code=404)

    server = getattr(request.app.state, "backend_server", None)
    if server is None:
        return Response(status_code=404)
    server.should_exit = True
    return Response(status_code=204)


# Serve Svelte frontend static files
def frontend_dist_path() -> Path:
    """Resolve WebUI assets in both source and PyInstaller environments."""
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root is not None:
        return Path(bundle_root) / "frontend" / "dist"
    return Path(__file__).resolve().parents[3] / "frontend" / "dist"


def find_available_port(start_port: int, host: str = "127.0.0.1", max_attempts: int = 100) -> int:
    """Find the first available port starting from start_port."""
    for port in range(start_port, start_port + max_attempts):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            sock.close()
            return port
        except OSError:
            sock.close()
            continue
    raise RuntimeError(f"No available port found in range {start_port}-{start_port + max_attempts}")


@app.get("/v1/models")
async def get_models(request: Request):
    from .proxy import proxy_models

    return await proxy_models(request, _config_dir)


@app.post("/v1/responses")
async def post_responses(request: Request):
    from .proxy import proxy_responses

    return await proxy_responses(request, _config_dir)


@app.get("/v1/responses")
async def get_responses_ws(request: Request):
    """WebSocket upgrade for Responses API."""
    # WebSocket handling requires a different approach in FastAPI
    return JSONResponse(
        {"error": "WebSocket endpoint — use ws:// protocol"},
        status_code=426,
    )


@app.post("/v1/responses/compact")
async def post_responses_compact(request: Request):
    from .proxy import proxy_responses_compact

    return await proxy_responses_compact(request, _config_dir)


@app.post("/v1/chat/completions")
async def post_chat_completions(request: Request):
    from .proxy import proxy_chat_completions

    return await proxy_chat_completions(request, _config_dir)


@app.post("/v1/images/generations")
async def post_images_generations(request: Request):
    from .proxy import proxy_images_generations

    return await proxy_images_generations(request, _config_dir)


@app.post("/v1/images/edits")
async def post_images_edits(request: Request):
    from .proxy import proxy_images_edits

    return await proxy_images_edits(request, _config_dir)


@app.post("/v1/alpha/search")
async def post_alpha_search(request: Request):
    from .proxy import proxy_alpha_search

    return await proxy_alpha_search(request, _config_dir)


@app.post("/backend-api/codex/alpha/search")
async def post_backend_alpha_search(request: Request):
    from .proxy import proxy_alpha_search

    return await proxy_alpha_search(request, _config_dir)


_FRONTEND_DIST = frontend_dist_path()
if (_FRONTEND_DIST / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        """Serve Svelte SPA after all API routes have had a chance to match."""
        file_path = _FRONTEND_DIST / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(_FRONTEND_DIST / "index.html")


async def _periodic_quota_refresh():
    """Refresh quotas for all selected accounts periodically."""
    from .config import list_selected_accounts

    while True:
        await asyncio.sleep(300)  # 5 minutes
        try:
            accounts = list_selected_accounts(_config_dir)
            for meta in accounts:
                try:
                    from .quota import refresh_quota

                    await refresh_quota(meta.id, _config_dir)
                except Exception as error:  # noqa: BLE001 - long-running worker boundary.
                    logger.warning("Background quota refresh failed for %s: %s", meta.id, error)
        except Exception as error:  # noqa: BLE001 - long-running worker must stay alive.
            logger.warning("Background quota refresh error: %s", error)


def main():
    parser = argparse.ArgumentParser(description="Codex Cockpit Lite Backend")
    parser.add_argument(
        "--config-dir",
        default=None,
        help="Configuration directory (default: ~/.config/codex-cockpit)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Override API port from config",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Override bind host from config",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="Log level",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    global _actual_port, _config_dir
    if args.config_dir:
        _config_dir = Path(args.config_dir).expanduser()

    cfg = load_config(_config_dir)
    desired_port = args.port or cfg.api.port
    host = args.host or cfg.api.bind_host

    # Find an available port, auto-increment if taken
    actual_port = find_available_port(desired_port, host)
    _actual_port = actual_port
    set_actual_port(actual_port)
    set_bind_host(host)

    # Write back to config if port changed
    if actual_port != cfg.api.port:
        cfg.api.port = actual_port
        save_config(cfg, _config_dir)

    logger.info("Starting on %s:%d, config dir: %s", host, actual_port, _config_dir)
    server = CockpitServer(
        uvicorn.Config(app, host=host, port=actual_port, log_level=args.log_level)
    )
    app.state.backend_server = server
    server.run()


if __name__ == "__main__":
    main()
