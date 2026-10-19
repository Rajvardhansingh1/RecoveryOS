"""Test-only API server for the browser tests. NOT part of the product.

Runs the real app on in-memory SQLite. `E2E_LLM=scripted` injects the scripted test client (it
exercises the LLM label paths in the UI); `E2E_LLM=none` runs with no LLM so every step uses the
deterministic fallback. Retry back-off is 0 and the rate limit is raised for test speed.

    uvicorn tests.e2e_server:app --port 8765     (from backend/, with pythonpath set)
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import httpx  # noqa: E402
from helpers import ScriptedLLM, good_replies  # noqa: E402

from app.adapters.github import _FIXTURES  # noqa: E402
from app.adapters.github_live import GitHubReader  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db.store import Store, make_engine  # noqa: E402
from app.main import create_app  # noqa: E402

mode = os.environ.get("E2E_LLM", "scripted")
settings = Settings(
    _env_file=None,
    demo_mode=True,
    retry_backoff_seconds=0,
    rate_limit_per_minute=100000,
    cors_origins="http://localhost:3100,http://127.0.0.1:3100",
    llm_provider="openrouter" if mode == "scripted" else "none",
    llm_models="scripted",
    OPENROUTER_API_KEY="test-only" if mode == "scripted" else "",
)
store = Store(make_engine("sqlite+pysqlite:///:memory:"))
store.create_schema()


def _factory(cap: int):
    if mode != "scripted":
        return None
    # One client per run, with replies matching whichever repository the run uses.
    return ScriptedLLM(good_replies(_FIXTURES["demo/acme-api"]["commits"]))


def _fake_github(request: httpx.Request) -> httpx.Response:
    """A public repository `octo/hello` served from memory. Anything else is 404 (not found)."""
    path = request.url.path
    sha = "c" * 40
    if path == "/repos/octo/hello":
        return httpx.Response(200, json={"default_branch": "main"})
    if path == "/repos/octo/hello/commits/main":
        return httpx.Response(200, json={"sha": sha})
    if path == "/repos/octo/hello/tags":
        return httpx.Response(200, json=[{"name": "v0.3.0"}, {"name": "v0.2.0"}])
    if path.startswith("/repos/octo/hello/compare/"):
        return httpx.Response(
            200,
            json={
                "commits": [
                    {
                        "sha": "a1a1a1a" + "0" * 33,
                        "commit": {"message": "fix: handle empty query", "author": {"name": "Ada"}},
                    },
                    {
                        "sha": "b2b2b2b" + "0" * 33,
                        "commit": {"message": "feat: add search filters", "author": {"name": "Bo"}},
                    },
                ]
            },
        )
    if path.endswith("/check-runs"):
        return httpx.Response(200, json={"check_runs": [{"name": "ci", "conclusion": "success"}]})
    return httpx.Response(404, json={"message": "Not Found"})


reader = GitHubReader(http=httpx.Client(transport=httpx.MockTransport(_fake_github)))
app = create_app(settings, store=store, reader=reader, llm_factory=_factory)
