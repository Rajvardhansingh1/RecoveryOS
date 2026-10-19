"""GitHub adapter interface and the mock used for demos, tests and the benchmark.

Methods return plain dicts, like a real HTTP API would. The executor validates them against
typed models, which is what lets "invalid tool output" be detected and retried.
"""

from typing import Any, Protocol

from app.adapters.github_live import GitHubReader
from app.core.errors import RealWritesDisabled, UnsupportedRepo
from app.db.store import Store

_FIXTURES: dict[str, dict[str, Any]] = {
    "demo/acme-api": {
        "default_branch": "main",
        "head_sha": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
        "current_version": "1.4.2",
        "tags": ["v1.4.0", "v1.4.1", "v1.4.2"],
        "commits": [
            {"sha": "a1b2c3d", "message": "feat: add pagination to /orders", "author": "mira"},
            {"sha": "b2c3d4e", "message": "fix: handle empty cart on checkout", "author": "jon"},
            {"sha": "c3d4e5f", "message": "docs: clarify rate limit headers", "author": "mira"},
            {"sha": "d4e5f60", "message": "chore: bump httpx", "author": "bot"},
        ],
        "checks": [
            {"name": "unit-tests", "exit_code": 0, "log_excerpt": "212 passed"},
            {"name": "lint", "exit_code": 0, "log_excerpt": "clean"},
        ],
    },
    "demo/breaking-change": {
        "default_branch": "main",
        "head_sha": "9f8e7d6c5b4a39281706f5e4d3c2b1a098765432",
        "current_version": "2.0.3",
        "tags": ["v2.0.0", "v2.0.3"],
        "commits": [
            {
                "sha": "e5f6071",
                "message": "feat!: remove deprecated /v1/users endpoint",
                "author": "ana",
            },
            {"sha": "f607182", "message": "fix: correct pagination offset", "author": "ana"},
        ],
        "checks": [
            {"name": "unit-tests", "exit_code": 0, "log_excerpt": "98 passed"},
            {"name": "lint", "exit_code": 0, "log_excerpt": "clean"},
        ],
    },
    "demo/failing-checks": {
        "default_branch": "main",
        "head_sha": "0123456789abcdef0123456789abcdef01234567",
        "current_version": "0.9.0",
        "tags": ["v0.9.0"],
        "commits": [
            {"sha": "0718293", "message": "feat: add export endpoint", "author": "li"},
        ],
        "checks": [
            {"name": "unit-tests", "exit_code": 1, "log_excerpt": "3 failed, 41 passed"},
            {"name": "lint", "exit_code": 0, "log_excerpt": "clean"},
        ],
    },
}

SUPPORTED_REPOS = tuple(_FIXTURES)


def is_demo_repo(repo: str) -> bool:
    return repo in _FIXTURES


class GitHubAdapter(Protocol):
    def get_repo_snapshot(self, repo: str) -> dict[str, Any]: ...
    def get_head_sha(self, repo: str) -> str: ...
    def run_checks(self, repo: str, sha: str) -> list[dict[str, Any]]: ...
    def create_draft_release(
        self, run_id: str, key: str | None, repo: str, tag: str, sha: str, notes: str
    ) -> dict[str, Any]: ...
    def publish_release(
        self, run_id: str, key: str | None, repo: str, tag: str, sha: str, notes: str
    ) -> dict[str, Any]: ...
    def find_effect(self, key: str) -> dict[str, Any] | None: ...


class MockGitHubAdapter:
    """Deterministic fake. Effects are recorded in `remote_effects`, which stands for the real
    remote world and therefore survives a simulated process crash.

    With a key, the mock deduplicates like a well-behaved API. With `key=None` it applies the
    effect every time, like a remote that ignores idempotency (used by the naive baseline).
    """

    def __init__(self, store: Store):
        self._store = store
        # Testing knob: pretend the repository moved on after the plan was made.
        self.head_override: str | None = None

    def _fixture(self, repo: str) -> dict[str, Any]:
        if repo not in _FIXTURES:
            raise UnsupportedRepo(f"unsupported repo: {repo}")
        return _FIXTURES[repo]

    def get_repo_snapshot(self, repo: str) -> dict[str, Any]:
        f = self._fixture(repo)
        return {
            "repo": repo,
            "default_branch": f["default_branch"],
            "head_sha": f["head_sha"],
            "current_version": f["current_version"],
            "tags": list(f["tags"]),
            "commits": [dict(c) for c in f["commits"]],
            "dirty": False,
        }

    def get_head_sha(self, repo: str) -> str:
        return self.head_override or self._fixture(repo)["head_sha"]

    def run_checks(self, repo: str, sha: str) -> list[dict[str, Any]]:
        return [dict(c) for c in self._fixture(repo)["checks"]]

    def _effect(
        self,
        action: str,
        result: dict[str, Any],
        run_id: str,
        key: str | None,
        repo: str,
        tag: str,
        sha: str,
        notes: str,
    ) -> dict[str, Any]:
        payload = {
            "repo": repo,
            "tag": tag,
            "sha": sha,
            "notes_chars": len(notes),
            "result": result,
        }
        if key is None:
            self._store.remote_apply_unkeyed(run_id, action, payload)
        else:
            self._store.remote_apply(run_id, key, action, payload)
        return result

    def create_draft_release(self, run_id, key, repo, tag, sha, notes):
        result = {"draft_ref": f"mock://{repo}/drafts/{tag}"}
        return self._effect("create_draft", result, run_id, key, repo, tag, sha, notes)

    def publish_release(self, run_id, key, repo, tag, sha, notes):
        result = {"release_url": f"mock://{repo}/releases/{tag}", "tag": tag}
        return self._effect("publish", result, run_id, key, repo, tag, sha, notes)

    def find_effect(self, key: str) -> dict[str, Any] | None:
        payload = self._store.remote_lookup(key)
        return payload["result"] if payload else None

    def preview(self, repo: str) -> dict[str, Any]:
        snap = self.get_repo_snapshot(repo)
        return {**snap, "checks": self.run_checks(repo, snap["head_sha"])}


class HybridAdapter:
    """Reads come from the demo fixtures or, for any other `owner/name`, from public GitHub.
    Every write (draft, publish) goes to the mock ledger: nothing is ever written to GitHub."""

    def __init__(self, mock: MockGitHubAdapter, reader: "GitHubReader | None"):
        self._mock = mock
        self._reader = reader

    def _read(self, repo: str):
        if is_demo_repo(repo):
            return self._mock
        if self._reader is None:
            raise UnsupportedRepo("linking a GitHub repository is disabled")
        return self._reader

    def get_repo_snapshot(self, repo):
        return self._read(repo).get_repo_snapshot(repo)

    def get_head_sha(self, repo):
        return self._read(repo).get_head_sha(repo)

    def run_checks(self, repo, sha):
        return self._read(repo).run_checks(repo, sha)

    def preview(self, repo):
        return self._read(repo).preview(repo)

    def create_draft_release(self, *a, **k):
        return self._mock.create_draft_release(*a, **k)

    def publish_release(self, *a, **k):
        return self._mock.publish_release(*a, **k)

    def find_effect(self, key):
        return self._mock.find_effect(key)


def build_github_adapter(
    store: Store, enable_real_writes: bool, reader: "GitHubReader | None" = None
) -> HybridAdapter:
    if enable_real_writes:
        # Not implemented in the MVP on purpose: real writes need explicit authorization.
        raise RealWritesDisabled("real GitHub writes are not implemented in this build")
    return HybridAdapter(MockGitHubAdapter(store), reader)
