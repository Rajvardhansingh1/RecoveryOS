"""Renders the release plan as a Markdown file the owner can keep and act on themselves.

Pure: takes stored data, returns text. Nothing owner-typed or repository-derived ever reaches a
shell command in the file: commands use only the tag (digits), a hex commit and the validated
repository name. Free text appears only in prose sections.
"""

import re
from datetime import UTC, datetime
from typing import Any

from app.models.domain import ReleasePlan

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
LABEL = {
    "inspect_repo": "Inspect repository",
    "run_checks": "Run checks",
    "prepare_changelog": "Prepare changelog",
    "validate_version": "Validate version",
    "create_release_plan": "Create release plan",
}


def plan_filename(plan: ReleasePlan) -> str:
    repo = _SAFE.sub("-", plan.repo.replace("/", "-")).strip("-") or "repo"
    return f"release-plan-{repo}-{_SAFE.sub('', plan.tag)}-r{plan.revision}.md"


def _produced(step: dict[str, Any]) -> str:
    who = step.get("produced_by")
    if who == "llm":
        return f"LLM ({step.get('provider')} / {step.get('model')})"
    if who == "deterministic_fallback":
        return f"deterministic fallback ({step.get('fallback_reason') or 'no reason recorded'})"
    return str(who or "unknown")


def render_plan(
    plan: ReleasePlan,
    *,
    source: str,
    steps: dict[str, dict[str, Any]],
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(UTC)
    inspect = (steps.get("inspect_repo") or {}).get("output") or {}
    checks = (steps.get("run_checks") or {}).get("output") or {}
    version = (steps.get("validate_version") or {}).get("output") or {}
    snap = inspect.get("snapshot") or {}

    out: list[str] = [
        f"# Release plan: {plan.tag} for {plan.repo}",
        "",
        f"Prepared by RecoveryOS on {now:%Y-%m-%d %H:%M} UTC. Plan revision {plan.revision}.",
        (
            "Source: demo repository on a mock GitHub (illustrative, not a real repository)."
            if source == "demo"
            else "Source: public GitHub repository, read live. Nothing was written to it."
        ),
        "",
        "## Summary",
        "",
        f"- Repository: {plan.repo}",
        f"- Version: {version.get('current', '?')} to {plan.version}"
        f" ({version.get('bump', '?')} bump)",
        f"- Commit to release: {plan.head_sha}",
        f"- Plan hash: {plan.plan_hash}",
        "",
        "## Why this release",
        "",
        plan.rationale,
        "",
    ]
    if version.get("justification"):
        out += [f"Version choice: {version['justification']}", ""]

    out += ["## Release notes", "", plan.notes_markdown, ""]

    out += ["## Checks", "", f"Verdict: {checks.get('verdict', 'unknown')}", ""]
    for r in checks.get("results") or []:
        mark = "passed" if r.get("exit_code") == 0 else "not passed"
        out.append(f"- {r.get('name')}: {mark} ({r.get('log_excerpt', '')})")
    if checks.get("reasons"):
        out += ["", "Reasons: " + "; ".join(str(x) for x in checks["reasons"])]
    out.append("")

    if plan.owner_notes:
        out += ["## Your suggestions and how they were handled", ""]
        out += [f"{i}. {note}" for i, note in enumerate(plan.owner_notes, start=1)]
        out += ["", plan.suggestion_response or "No response recorded.", ""]

    out += ["## How each step was produced", "", "| Step | Produced by |", "|---|---|"]
    for name, label in LABEL.items():
        if name in steps:
            out.append(f"| {label} | {_produced(steps[name])} |")
    out.append("")

    out += ["## Publish it yourself", ""]
    if source == "demo":
        out += [
            "This is a demo repository, so the commands below are illustrative only.",
            "",
        ]
    out += [
        "Save the release notes above as `RELEASE_NOTES.md`, then, from a clone of the repository:",
        "",
        "```",
        f'git tag -a {plan.tag} {plan.head_sha} -m "{plan.tag}"',
        f"git push origin {plan.tag}",
        f"gh release create {plan.tag} --repo {plan.repo} --target {plan.head_sha} "
        f'--title "{plan.tag}" --notes-file RELEASE_NOTES.md',
        "```",
        "",
        "Check that the commit is still the head of the default branch"
        + (f" ({snap.get('default_branch')})" if snap.get("default_branch") else "")
        + " before you tag it.",
        "",
    ]
    return "\n".join(out)
