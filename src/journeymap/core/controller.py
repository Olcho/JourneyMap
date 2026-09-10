"""The complete in-process capability granted to a Controller."""

from collections.abc import Callable
from dataclasses import dataclass, field

from journeymap.core.handlers import ActionRequest, ActionStatus
from journeymap.core.observations import Observation


@dataclass(frozen=True, slots=True)
class ControllerActionResult:
    """Actor-visible receipt, without world digest or engine provenance."""

    action_request_id: str
    run_id: str
    actor_id: str
    status: ActionStatus
    reason_code: str | None
    started_at: int
    resolved_at: int
    schema_version: int = 1


class GameSubmissionError(RuntimeError):
    """A sanitized application failure; detailed diagnostics are research-only."""


@dataclass(frozen=True, slots=True)
class GamePort:
    """Actor binding is captured by narrow callbacks, with no actor selector.

    This is an API capability boundary in trusted Python, not a Python sandbox.
    Private closure introspection is not part of the supported interface.
    """

    _observe: Callable[[], Observation] = field(repr=False)
    _submit: Callable[[ActionRequest], ControllerActionResult] = field(repr=False)

    def observe(self) -> Observation:
        return self._observe()

    def submit(self, request: ActionRequest) -> ControllerActionResult:
        return self._submit(request)
