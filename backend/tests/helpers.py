"""Test-only scripted LLM. It exercises the harness (validation, retry, fallback, labeling).
Its outputs are never reported as model results."""

from collections.abc import Callable
from typing import Any

from app.adapters.llm import LLMError, LLMMeta
from app.core.steps import required_bump
from app.models.domain import Commit


class ScriptedLLM:
    provider = "scripted-test"

    def __init__(self, handler: Callable[[str, type, str], Any]):
        self._handler = handler
        self.calls_made = 0

    def complete(self, system: str, user: str, schema):
        self.calls_made += 1
        value = self._handler(schema.__name__, schema, user)
        if isinstance(value, LLMError):
            raise value
        return schema.model_validate(value), LLMMeta("scripted-test", "scripted-1", tokens=10)


def good_replies(commits: list[dict]) -> Callable[[str, type, str], Any]:
    parsed = [Commit(**c) for c in commits]

    def handler(name: str, schema: type, user: str):
        if name == "InspectLLM":
            return {"summary": "Repo looks releasable.", "concerns": []}
        if name == "ChecksLLM":
            return {"verdict": "pass", "reasons": ["all green"]}
        if name == "ChangelogLLM":
            cats = {"feat": "feature", "fix": "fix", "docs": "docs"}
            return {
                "entries": [
                    {
                        "sha": c.sha,
                        "text": c.message.split(":", 1)[-1].strip(),
                        "category": "breaking"
                        if "!:" in c.message
                        else cats.get(c.message.split(":")[0].rstrip("!"), "other"),
                    }
                    for c in parsed
                ]
            }
        if name == "VersionLLM":
            return {"bump": required_bump(parsed), "justification": "per commits"}
        if name == "PlanLLM":
            return {"rationale": "Ready to release."}
        raise AssertionError(name)

    return handler
