"""Technology-independent deterministic simulation kernel contracts."""

from journeymap.core.canonical import JsonObject, JsonValue, canonical_json, state_digest
from journeymap.core.events import (
    EventBus,
    EventDeliveryError,
    EventDraft,
    EventEnvelope,
    EventSourceKind,
)
from journeymap.core.handlers import (
    ActionHandler,
    ActionRegistry,
    ActionRequest,
    ActionResult,
    ResolutionContext,
    SystemEventHandler,
    SystemEventOutcome,
    SystemEventRegistry,
    TransitionPlan,
    ValidationContext,
)
from journeymap.core.kernel import ReentrantMutationError, SimulationKernel
from journeymap.core.modules import (
    Module,
    ModuleDependencyError,
    ModuleMetadata,
    ModuleRegistrationError,
    ModuleRegistry,
)
from journeymap.core.persistence import Persistence
from journeymap.core.replay import ReplayHarness, ReplayInput, ReplayReport
from journeymap.core.run import (
    DeterministicRng,
    RunManifest,
    SimulationClock,
    SimulationRun,
    TransitionIdentity,
)
from journeymap.core.scheduler import (
    DeterministicScheduler,
    ScenarioSchedule,
    ScheduledEvent,
    ScheduledEventSpec,
)

__all__ = [
    "ActionHandler",
    "ActionRegistry",
    "ActionRequest",
    "ActionResult",
    "DeterministicRng",
    "DeterministicScheduler",
    "EventBus",
    "EventDeliveryError",
    "EventDraft",
    "EventEnvelope",
    "EventSourceKind",
    "JsonObject",
    "JsonValue",
    "Module",
    "ModuleDependencyError",
    "ModuleMetadata",
    "ModuleRegistrationError",
    "ModuleRegistry",
    "Persistence",
    "ReentrantMutationError",
    "ReplayHarness",
    "ReplayInput",
    "ReplayReport",
    "ResolutionContext",
    "RunManifest",
    "ScenarioSchedule",
    "ScheduledEvent",
    "ScheduledEventSpec",
    "SimulationClock",
    "SimulationKernel",
    "SimulationRun",
    "SystemEventHandler",
    "SystemEventOutcome",
    "SystemEventRegistry",
    "TransitionIdentity",
    "TransitionPlan",
    "ValidationContext",
    "canonical_json",
    "state_digest",
]
