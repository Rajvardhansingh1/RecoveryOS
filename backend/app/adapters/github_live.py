"""Read-only GitHub access for public repositories.

Writes never happen here. A linked repository is inspected for real (commits, tags, checks);
drafting and publishing stay simulated by the mock ledger.

Safety:
  * the host is fixed to api.github.com; the repository name is validated before it is used in
    a path, and redirects are followed only one hop and only to the same host;
  * the token (optional, server-side env only) is sent as a header and is never logged or stored;
  * every call has a timeout, replies are size-checked, and commit data is truncated.
"""

import re
import time
from typing import Any
from urllib.parse import quote

import httpx

from app.core.errors import (
    InvalidToolOutput,
    NothingToRelease,
    ToolRateLimited,
    ToolTimeout,
    UnsupportedRepo,
)

API = "https://api.github.com"
MAX_COMMITS = 50
MAX_TAGS = 100
MAX_BODY = 2_000_000
_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPO = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def split_repo(repo: str) -> tuple[str, str]:
    """Validate `owner/name`. Raises UnsupportedRepo for anything else."""
    parts = repo.split("/")
    if len(parts) != 2:
        raise UnsupportedRepo("repository must look like owner/name")
    owner, name = parts
    if not _OWNER.match(owner) or not _REPO.match(name) or name in (".", ".."):
        raise UnsupportedRepo("repository must look like owner/name")
    return owner, name


class GitHubReader:
    def __init__(
        self, token: str | None = None, http: httpx.Client | None = None, timeout: float = 10.0
    ):
        self._token = (token or "").strip()
        self._http = http or httpx.Client(timeout=timeout)
        self._timeout = timeout

    # ---- transport ----

    def _get(self, path: str, params: dict[str, Any] | None = None, _hop: int = 0) -> Any:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            res = self._http.get(
                API + path,
                params=params,
                headers=headers,
                timeout=self._timeout,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise ToolTimeout("GitHub timed out") from exc
        except httpx.HTTPError as exc:
            raise ToolTimeout("GitHub unreachable") from exc

        if res.status_code in (301, 302, 307, 308) and _hop == 0:
            target = res.headers.get("location", "")
            if target.startswith(API + "/"):
                return self._get(target[len(API) :], None, _hop=1)
            raise InvalidToolOutput("redirect to an unexpected host")
        if res.status_code == 409 and "empty" in res.text.lower():
            raise NothingToRelease("the repository has no commits yet")
        if res.status_code == 404:
            raise UnsupportedRepo("not found, or private (only public repositories are supported)")
        if res.status_code in (403, 429):
            remaining = res.headers.get("x-ratelimit-remaining")
            if res.status_code == 429 or remaining == "0" or "rate limit" in res.text.lower():
                reset = res.headers.get("x-ratelimit-reset", "")
                wait = max(0.0, float(reset) - time.time()) if reset.isdigit() else 5.0
                raise ToolRateLimited(retry_after=min(wait, 5.0))
            raise UnsupportedRepo("GitHub refused access to this repository")
        if res.status_code >= 500:
            raise ToolTimeout(f"GitHub error {res.status_code}")
        if res.status_code != 200:
            raise InvalidToolOutput(f"unexpected GitHub status {res.status_code}")
        if len(res.content) > MAX_BODY:
            raise InvalidToolOutput("GitHub reply too large")
        try:
            return res.json()
        except ValueError as exc:
            raise InvalidToolOutput("GitHub reply is not JSON") from exc

    # ---- reads ----

    @staticmethod
    def _commit(c: dict[str, Any]) -> dict[str, Any]:
        info = c.get("commit") or {}
        message = str(info.get("message") or "").splitlines()[0:1]
        author = (info.get("author") or {}).get("name") or (c.get("author") or {}).get("login")
        return {
            "sha": str(c.get("sha", ""))[:7],
            "message": (message[0] if message else "(no message)")[:500],
            "author": str(author or "unknown")[:100],
        }

    def get_head_sha(self, repo: str) -> str:
        owner, name = split_repo(repo)
        branch = self._default_branch(owner, name)
        head = self._get(f"/repos/{owner}/{name}/commits/{quote(branch, safe='')}")
        sha = head.get("sha") if isinstance(head, dict) else None
        if not isinstance(sha, str) or len(sha) < 7:
            raise InvalidToolOutput("head commit missing")
        return sha

    def _default_branch(self, owner: str, name: str) -> str:
        meta = self._get(f"/repos/{owner}/{name}")
        branch = meta.get("default_branch") if isinstance(meta, dict) else None
        if not isinstance(branch, str) or not branch:
            raise InvalidToolOutput("default branch missing")
        return branch

    def get_repo_snapshot(self, repo: str) -> dict[str, Any]:
        owner, name = split_repo(repo)
        branch = self._default_branch(owner, name)
        head = self._get(f"/repos/{owner}/{name}/commits/{quote(branch, safe='')}")
        head_sha = head.get("sha") if isinstance(head, dict) else None
        if not isinstance(head_sha, str) or len(head_sha) < 7:
            raise InvalidToolOutput("head commit missing")

        tags_raw = self._get(f"/repos/{owner}/{name}/tags", {"per_page": MAX_TAGS})
        if not isinstance(tags_raw, list):
            raise InvalidToolOutput("tags reply is not a list")
        tag_names = [str(t.get("name")) for t in tags_raw if isinstance(t, dict) and t.get("name")]
        versions = [
            (tuple(int(x) for x in m.groups()), n) for n in tag_names if (m := _SEMVER.match(n))
        ]

        if versions:
            version_tuple, base_tag = max(versions)
            current = ".".join(str(x) for x in version_tuple)
            base_q, head_q = quote(base_tag, safe=""), quote(branch, safe="")
            cmp = self._get(f"/repos/{owner}/{name}/compare/{base_q}...{head_q}")
            commits_raw = cmp.get("commits", []) if isinstance(cmp, dict) else []
            commits_raw = commits_raw[-MAX_COMMITS:]
        else:
            current = "0.0.0"
            listing = self._get(f"/repos/{owner}/{name}/commits", {"sha": branch, "per_page": 30})
            commits_raw = listing if isinstance(listing, list) else []

        commits = [self._commit(c) for c in commits_raw if isinstance(c, dict) and c.get("sha")]
        commits.reverse()  # newest first, like git log
        return {
            "repo": f"{owner}/{name}",
            "default_branch": branch,
            "head_sha": head_sha,
            "current_version": current,
            "tags": tag_names,
            "commits": commits,
            "dirty": False,
        }

    def run_checks(self, repo: str, sha: str) -> list[dict[str, Any]]:
        owner, name = split_repo(repo)
        runs = self._get(
            f"/repos/{owner}/{name}/commits/{quote(sha, safe='')}/check-runs", {"per_page": 30}
        )
        results: list[dict[str, Any]] = []
        for r in (runs.get("check_runs", []) if isinstance(runs, dict) else [])[:30]:
            conclusion = r.get("conclusion")
            ok = conclusion in ("success", "neutral", "skipped")
            results.append(
                {
                    "name": str(r.get("name") or "check")[:100],
                    "exit_code": 0 if ok else 1,
                    "log_excerpt": str(conclusion or r.get("status") or "unknown")[:500],
                }
            )
        if not results:
            status = self._get(f"/repos/{owner}/{name}/commits/{quote(sha, safe='')}/status")
            for s in (status.get("statuses", []) if isinstance(status, dict) else [])[:30]:
                ok = s.get("state") == "success"
                results.append(
                    {
                        "name": str(s.get("context") or "status")[:100],
                        "exit_code": 0 if ok else 1,
                        "log_excerpt": str(s.get("state") or "unknown")[:500],
                    }
                )
        if not results:
            results.append(
                {
                    "name": "no-checks-found",
                    "exit_code": 0,
                    "log_excerpt": (
                        "No check runs or commit statuses exist for this commit, "
                        "so nothing was verified."
                    ),
                }
            )
        return results

    def preview(self, repo: str) -> dict[str, Any]:
        snap = self.get_repo_snapshot(repo)
        checks = self.run_checks(repo, snap["head_sha"])
        return {**snap, "checks": checks}
