"""Logical clock, seeded RNG, and canonical digest tests."""

from typing import cast

import pytest

from journeymap.core.canonical import (
    CanonicalValueError,
    JsonObject,
    JsonValue,
    canonical_json,
    state_digest,
)
from journeymap.core.run import ClockError, DeterministicRng, SimulationClock


class CustomInteger(int):
    pass


def test_clock_uses_explicit_ticks_and_never_moves_backwards() -> None:
    clock = SimulationClock(10)

    clock.advance_by(5)
    clock.advance_to(20)

    assert clock.now == 20
    with pytest.raises(ClockError, match="cannot move time backwards"):
        clock.advance_to(19)


@pytest.mark.parametrize("invalid_tick", [True, 1.5, float("nan")])
def test_clock_rejects_non_integer_ticks(invalid_tick: object) -> None:
    with pytest.raises(ClockError, match="non-negative integer"):
        SimulationClock(cast(int, invalid_tick))


def test_splitmix64_sequence_and_draw_count_are_stable() -> None:
    rng = DeterministicRng(0)

    values = tuple(rng.next_u64() for _ in range(3))

    assert values == (
        0xE220A8397B1DCDAF,
        0x6E789E6AA1B965F4,
        0x06C45D188009454F,
    )
    assert rng.draw_count == 3


def test_rng_clone_is_an_isolated_transaction_candidate() -> None:
    rng = DeterministicRng(42)
    candidate = rng.clone()

    candidate.randbelow(10)

    assert rng.draw_count == 0
    assert candidate.draw_count == 1


def test_rng_seed_is_normalized_to_unsigned_64_bit_width() -> None:
    assert DeterministicRng(-1).next_u64() == DeterministicRng((1 << 64) - 1).next_u64()
    assert DeterministicRng((1 << 64) + 7).next_u64() == DeterministicRng(7).next_u64()


def test_rng_rejects_non_integer_seed() -> None:
    with pytest.raises(TypeError, match="seed must be an integer"):
        DeterministicRng(cast(int, 1.5))


def test_canonical_json_and_digest_ignore_mapping_insertion_order() -> None:
    left: JsonObject = {"nested": {"b": 2, "a": 1}, "value": 3}
    right: JsonObject = {"value": 3, "nested": {"a": 1, "b": 2}}

    assert canonical_json(left) == canonical_json(right)
    assert state_digest(left) == state_digest(right)


def test_canonical_json_explicitly_supports_finite_float_values() -> None:
    assert canonical_json({"value": 1.25}) == '{"value":1.25}'


@pytest.mark.parametrize(
    "invalid",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        {"set-value"},
        b"bytes-value",
        ("tuple-value",),
        CustomInteger(1),
        object(),
    ],
)
def test_canonical_json_rejects_unsupported_values(invalid: object) -> None:
    with pytest.raises(CanonicalValueError):
        canonical_json({"invalid": cast(JsonValue, invalid)})


def test_canonical_json_rejects_non_string_mapping_keys() -> None:
    invalid = cast(JsonValue, {1: "integer-key"})

    with pytest.raises(CanonicalValueError, match="non-string mapping key"):
        canonical_json(invalid)
