import logging
import re
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.adapters.github import GitHubAdapter, build_github_adapter
from app.adapters.github_live import GitHubReader
from app.adapters.llm import LLMClient, build_llm_client
from app.api.v1 import ActiveRuns, router
from app.config import Settings, get_settings
from app.core.executor import Executor
from app.core.retry import RetryPolicy
from app.db.store import Store, make_engine

log = logging.getLogger("recoveryos")

_SECRET = re.compile(r"(?i)(password|token|secret|api[_ -]?key|authorization)\s*[=:]\s*\S+")
_URL_CREDS = re.compile(r"://[^/\s:@]+:[^/\s@]+@")


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = _URL_CREDS.sub("://***:***@", _SECRET.sub(r"\1=***", record.getMessage()))
        record.msg, record.args = re.sub(r"[\x00-\x1f]", " ", msg), None
        return True


def _error(status: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status, headers=headers
    )


def create_app(
    settings: Settings | None = None,
    *,
    store: Store | None = None,
    adapter: GitHubAdapter | None = None,
    reader: GitHubReader | None = None,
    llm_factory: Callable[[int], LLMClient | None] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    if not log.handlers:
        handler = logging.StreamHandler()
        handler.addFilter(RedactingFilter())
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)

    store = store or Store(make_engine(settings.sqlalchemy_url))
    store.create_schema() if store.engine.dialect.name == "sqlite" else None
    if reader is None and settings.enable_github_link:
        reader = GitHubReader(settings.github_token)
    adapter = adapter or build_github_adapter(store, settings.enable_real_github_writes, reader)
    llm_factory = llm_factory or (lambda cap: build_llm_client(settings, cap))
    executor = Executor(
        store,
        adapter,
        llm_factory,
        policy=RetryPolicy(
            max_attempts=settings.max_step_attempts, base_delay=settings.retry_backoff_seconds
        ),
        approval_ttl_minutes=settings.approval_ttl_minutes,
        llm_call_cap=settings.llm_max_calls_per_run,
    )

    app = FastAPI(title="RecoveryOS API", version="0.1.0")
    app.state.ctx = SimpleNamespace(
        settings=settings, store=store, executor=executor, active=ActiveRuns()
    )
    app.include_router(router)

    hits: dict[str, deque[float]] = defaultdict(deque)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        request_id = uuid.uuid4().hex[:12]
        started = time.perf_counter()
        response = None
        try:
            if request.method != "OPTIONS" and request.url.path != "/health":
                length = request.headers.get("content-length")
                if length and length.isdigit() and int(length) > settings.max_request_bytes:
                    response = _error(413, "request_too_large", "Request body is too large.")
                else:
                    now = time.monotonic()
                    window = hits[request.client.host if request.client else "unknown"]
                    while window and now - window[0] > 60:
                        window.popleft()
                    if len(window) >= settings.rate_limit_per_minute:
                        response = _error(
                            429, "rate_limited", "Too many requests.", {"Retry-After": "30"}
                        )
                    else:
                        window.append(now)
            if response is None:
                response = await call_next(request)
        except Exception as exc:  # noqa: BLE001 - last-resort handler, class name only is logged
            log.error("req=%s unhandled %s", request_id, type(exc).__name__)
            response = _error(500, "internal_error", "Something went wrong.")
        route = request.scope.get("route")
        log.info(
            "req=%s %s %s %s %.0fms",
            request_id,
            request.method,
            getattr(route, "path", "unmatched"),
            response.status_code,
            (time.perf_counter() - started) * 1000,
        )
        response.headers["X-Request-ID"] = request_id
        return response

    # Added after the guard so it is the outermost layer: even a 429, 413 or 500 produced by the
    # guard carries CORS headers, and the browser shows the real error instead of "unreachable".
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        expose_headers=["Retry-After", "X-Request-ID"],
    )

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            return _error(exc.status_code, exc.detail["code"], exc.detail["message"])
        return _error(exc.status_code, "http_error", "Request failed.")

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError):
        # Fixed message: never echo submitted values back.
        return _error(422, "invalid_request", "The request did not match the expected schema.")

    @app.get("/health")
    def health():
        try:
            store.count_active_runs()
            db = "ok"
        except Exception:  # noqa: BLE001
            db = "unavailable"
        return {
            "status": "ok" if db == "ok" else "degraded",
            "database": db,
            "llm_configured": build_llm_client(settings, 1) is not None,
            "demo_mode": settings.demo_mode,
            "real_github_writes": settings.enable_real_github_writes,
        }

    return app


def app_factory() -> FastAPI:
    return create_app()
