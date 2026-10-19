"""Failure types. Retryable ones are bounded by RetryPolicy; terminal ones end the run."""


class RecoveryError(Exception):
    code = "internal_error"
    retryable = False


class ToolTimeout(RecoveryError):
    code = "tool_timeout"
    retryable = True


class ToolRateLimited(RecoveryError):
    code = "tool_rate_limited"
    retryable = True

    def __init__(self, retry_after: float = 0.0):
        super().__init__("rate limited")
        self.retry_after = retry_after


class InvalidToolOutput(RecoveryError):
    code = "invalid_tool_output"
    retryable = True


class ChecksFailed(RecoveryError):
    code = "checks_failed"


class NothingToRelease(RecoveryError):
    code = "nothing_to_release"


class VersionConflict(RecoveryError):
    code = "version_conflict"


class UnsupportedRepo(RecoveryError):
    code = "unsupported_repo"


class ApprovalRequired(RecoveryError):
    """The approval gate refused a gated action."""

    code = "approval_required"

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class RealWritesDisabled(RecoveryError):
    code = "real_writes_disabled"


class SimulatedCrash(BaseException):
    """Simulated process death. BaseException so no `except Exception` can swallow it."""

    def __init__(self, where: str):
        super().__init__(where)
        self.where = where
