"""Replay concurrent commands into fresh engines, retaining in-flight frames."""

from collections.abc import Callable
from dataclasses import dataclass

from journeymap.core.canonical import JsonObject
from journeymap.core.concurrent import ConcurrentKernel, ExecutionCommand


@dataclass(frozen=True, slots=True)
class ConcurrentReplayReport:
    frames: tuple[JsonObject, ...]
    final_execution: JsonObject


def replay_concurrent(
    factory: Callable[[], ConcurrentKernel],
    commands: tuple[ExecutionCommand, ...],
    *,
    capture_frames: bool = True,
) -> ConcurrentReplayReport:
    kernel = factory()
    if kernel.commands:
        raise ValueError("replay factory must have no preinstalled commands")
    kernel.boot()
    frames = []
    try:
        for command in commands:
            if command.kind == "accept" and command.request is not None:
                if command.tick != kernel.simulation_time:
                    raise ValueError("admission tick mismatch")
                kernel.accept_action(command.request)
            elif command.kind == "schedule" and command.schedule is not None:
                if command.tick != kernel.simulation_time:
                    raise ValueError("schedule installation tick mismatch")
                kernel.schedule(command.schedule)
            elif command.kind == "advance":
                kernel.advance_to(command.tick)
            else:
                raise ValueError("invalid concurrent-v1 command")
            if capture_frames:
                frames.append(kernel.execution_snapshot)
        return ConcurrentReplayReport(tuple(frames), kernel.execution_snapshot)
    finally:
        kernel.close()
