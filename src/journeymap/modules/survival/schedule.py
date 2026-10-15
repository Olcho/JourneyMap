"""Finite elapsed-time needs inputs; historical schedules remain untouched."""

from journeymap.core.scheduler import ScheduledEventSpec


def needs_schedule(start_tick: int, horizon: int) -> tuple[ScheduledEventSpec, ...]:
    if type(start_tick) is not int or type(horizon) is not int or not 0 <= start_tick <= horizon:
        raise ValueError("invalid needs horizon")
    return tuple(
        ScheduledEventSpec(tick, 10, "SurvivalTick", 1, {})
        for tick in range(start_tick + 1, horizon + 1)
    )
